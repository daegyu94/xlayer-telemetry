"""Representative public-path contracts; fixtures describe samples, not verdicts."""

import json

import pytest

from xlayer_telemetry.analysis import diagnostics, llm_diagnosis as llm
from xlayer_telemetry.analysis.diagnostic_artifacts import _investigation_rows
from xlayer_telemetry.analysis.llm_investigation import project_result


class SamplePrometheus:
    """One sampled device per interval; unrelated queries have no observations."""

    def __init__(self, scenario):
        self.scenario = scenario
        self.calls = []

    def query_range_detail(self, query, start, end, step):
        self.calls.append((query, start, end, step))
        if query == 'contract_optional' and self.scenario == 'optional_failure':
            from xlayer_telemetry.prometheus import PrometheusQueryTimeout
            raise PrometheusQueryTimeout('optional query unavailable')
        if query != 'contract_disk':
            return {'aggregate': None, 'series': []}
        current = start == 90
        value = 0 if current and self.scenario == 'observed_zero' else .2 if current else .1
        labels = {'node': 'n', 'instance': 'n:9100',
                  'device': 'sdb' if not current and self.scenario == 'different_entity' else 'sda'}
        stats = {'min': value, 'max': value, 'mean': value, 'last': value, 'sample_count': 2}
        return {'aggregate': dict(stats), 'series': [{'labels': labels, 'stats': stats}]}

    def query_range(self, query, start, end, step):
        return self.query_range_detail(query, start, end, step)['aggregate']


def step_record(number, start):
    return {'record_id': f'step-{number}', 'run_id': 'r', 'node': 'n',
            'worker_id': 'w', 'step': number, 'observed_at': start + 10,
            'step_duration_seconds': 10, 'boundary_scope': 'step',
            'analysis_window': {'start': start, 'end': start + 10, 'accuracy': 'exact'}}


def hypothesis(observation_id):
    return {'assessment': 'bottleneck_suspected', 'summary': 'Device pressure requires investigation.',
            'candidates': [{'title': 'Device pressure hypothesis', 'explanation': 'Inspect measured device pressure.',
                            'evidence_ids': [observation_id], 'counter_evidence_ids': [],
                            'missing_evidence': ['Resource attribution is unverified.'],
                            'observation_scope': 'device', 'next_checks': ['Inspect the device.']}],
            'limitations': ['A hypothesis does not establish workload attribution.']}


@pytest.mark.parametrize('scenario', ['normal', 'no_baseline', 'optional_failure',
                                      'observed_zero', 'different_entity', 'unsafe_clock'])
def test_representative_observations_reach_rule_llm_and_saved_projection(monkeypatch, tmp_path, scenario):
    backend = SamplePrometheus(scenario)
    monkeypatch.setattr(llm, 'PrometheusClient', lambda *_: backend)
    config = {'prometheus': {'url': 'http://unused', 'queries': {
        'disk_busy_ratio': 'contract_disk', 'host_memory_available_ratio': 'contract_optional'}}}
    source = {'prometheus_url': 'http://unused', 'current_interval': step_record(2, 90)['analysis_window'],
              'baseline_interval': None if scenario == 'no_baseline' else step_record(1, 70)['analysis_window'],
              'queries': [{'signal': 'disk_busy_ratio', 'query': 'contract_disk', 'unit': 'percentunit',
                           'scope': 'device'},
                          {'signal': 'optional', 'query': 'contract_optional', 'unit': 'bytes', 'scope': 'node'}]}
    if scenario == 'unsafe_clock':
        clock = {'status': 'unsafe', 'nodes': {'n': {'status': 'unsafe'}}}
        monkeypatch.setattr(diagnostics, 'assess_interval', lambda *_, **__: dict(clock))
        monkeypatch.setattr(llm, 'assess_interval', lambda *_, **__: dict(clock))
        config.update(cluster='lab', clock={'nodes': ['n']})
        source.update(cluster='lab', clock={'nodes': ['n']})
    history = [] if scenario == 'no_baseline' else [step_record(1, 70)]
    report = diagnostics.DiagnosticEngine(config, prometheus=backend, clock=lambda: 101).analyze(
        step_record(2, 90), history)
    standalone = llm.collect_packet(source)
    saved = llm.packet_from_report(report)
    llm.validate_packet(standalone)
    llm.validate_packet(saved)
    if scenario == 'unsafe_clock':
        assert report['verdict'] == 'insufficient_data' and not report['candidates'] and not report['findings']
        assert report['evidence']['disk_busy_ratio']['max'] == .2
        assert report['comparison']['signals'] == saved['observations'] == []
        for packet in (standalone, saved):
            with pytest.raises(ValueError, match='clock alignment'):
                llm.validate_diagnosis(hypothesis('m1'), packet)
            answer = {'assessment': 'insufficient_evidence', 'summary': 'Clock alignment is unsafe.',
                      'candidates': [], 'limitations': ['Cross-node overlap is unavailable.']}
            llm.validate_diagnosis(answer, packet)
            result = {'record_type': 'llm_diagnosis', 'generated_at': report['generated_at'],
                      'semantic_review': {'decision': 'accept'}, 'context': packet.get('context', {}),
                      'current_interval': packet['current_interval'], 'observation_packet': packet, 'diagnosis': answer}
            projected = [json.loads(line) for line in project_result(tmp_path, result).read_text().splitlines()]
            assert projected[0]['verdict'] == 'insufficient_evidence'
            assert len(projected) == 1
        assert _investigation_rows(report)[0]['verdict'] == 'insufficient_data'
        return
    row = next(row for row in report['comparison']['signals'] if row['signal'] == 'disk_busy_ratio')
    live = next(item for item in standalone['observations'] if item['labels']['device'] == 'sda')
    restored = next(item for item in saved['observations'] if item['signal'] == 'disk_busy_ratio')
    expected = 0 if scenario == 'observed_zero' else .2
    assert row['current'] == restored['current'] == live['current']['max'] == expected
    assert row['labels'] == restored['labels'] == live['labels']
    assert row['scope'] == restored['observation_scope'] == live['observation_scope'] == 'device'
    if scenario in {'no_baseline', 'different_entity'}:
        assert row['baseline'] is restored['baseline'] is live['baseline'] is None
    else:
        assert row['baseline'] == restored['baseline'] == live['baseline']['max'] == .1
    if scenario == 'different_entity':
        assert 'prometheus:disk_busy_ratio:baseline_entity_match' in report['missing_sources']
        prior_only = next(item for item in standalone['observations'] if item['labels']['device'] == 'sdb')
        assert prior_only['current'] is None and prior_only['baseline']['max'] == .1
    if scenario == 'optional_failure':
        assert 'prometheus:host_memory_available_ratio:PrometheusQueryTimeout' in report['missing_sources']
        assert 'current:optional:PrometheusQueryTimeout' in standalone['missing_sources']
        assert 'baseline:optional:PrometheusQueryTimeout' in standalone['missing_sources']
    rule_rows = _investigation_rows(report)
    assert rule_rows[0]['diagnosis_method'] == 'rule'
    assert rule_rows[0]['window_start_ms'] == 90000 and rule_rows[0]['window_end_ms'] == 100000
    for packet, observation in ((standalone, live), (saved, restored)):
        answer = hypothesis(observation['id'])
        llm.validate_diagnosis(answer, packet)
        result = {'record_type': 'llm_diagnosis', 'generated_at': report['generated_at'],
                  'semantic_review': {'decision': 'accept'}, 'context': packet.get('context', {}),
                  'current_interval': packet['current_interval'], 'observation_packet': packet, 'diagnosis': answer}
        projected = [json.loads(line) for line in project_result(tmp_path, result).read_text().splitlines()]
        assert projected[0]['diagnosis_method'] == 'llm'
        assert next(item for item in projected if item['row_kind'] == 'candidate')['state'] == 'hypothesis'
        evidence = next(item for item in projected if item['row_kind'] == 'evidence' and item.get('observation_id'))
        assert evidence['current'] == observation['current']
        assert evidence['baseline'] == observation['baseline']
        assert evidence['entity'] == ','.join(f'{key}={value}' for key, value in observation['labels'].items())
    assert 'candidates' not in saved and 'verdict' not in saved and 'thresholds' not in saved
    assert any(query == 'contract_disk' and start == 90 and end == 100 for query, start, end, _ in backend.calls)


def test_optional_clickhouse_failure_preserves_measured_core_and_saved_reader():
    class UnavailableThreeFS:
        def query_window(self, start, end):
            raise TimeoutError('optional ClickHouse unavailable')

    backend = SamplePrometheus('normal')
    config = {'prometheus': {'url': 'http://unused', 'queries': {'disk_busy_ratio': 'contract_disk'}}}
    report = diagnostics.DiagnosticEngine(config, prometheus=backend, threefs=UnavailableThreeFS(),
                                         clock=lambda: 101).analyze(step_record(2, 90), [step_record(1, 70)])
    assert 'threefs:TimeoutError' in report['missing_sources']
    signal = next(row for row in report['comparison']['signals'] if row['signal'] == 'disk_busy_ratio')
    assert signal['current'] == .2 and signal['baseline'] == .1
    saved = llm.packet_from_report(report)
    llm.validate_packet(saved)
    assert 'threefs:TimeoutError' in saved['missing_sources']
    assert any(row.get('signal') == 'disk_busy_ratio' for row in _investigation_rows(report))
