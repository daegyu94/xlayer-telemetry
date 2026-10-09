"""Replica evidence stays separate even under partial collection and clocks."""
import copy

import pytest

from xlayer_telemetry.analysis.diagnosis_analysis import select_vllm_observations
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, _investigation_rows
from xlayer_telemetry.analysis.clock_quality import clock_inventory
from tests.test_diagnosis_quality_boundaries import records, stats


INVENTORY = [
    {'id': 'replica-0', 'instance': 'a:8000', 'endpoint_node': 'rollout-a', 'nodes': ['rollout-a']},
    {'id': 'replica-1', 'instance': 'b:8000', 'endpoint_node': 'rollout-b', 'nodes': ['rollout-b', 'rollout-c']},
]


class Replicas:
    def __init__(self, *, stale=None, down=None, bad_clock=None, missing_preemption=True):
        self.stale, self.down, self.bad_clock = stale, down, bad_clock
        self.missing_preemption = missing_preemption
        self.calls = []

    def query_range_detail(self, query, start, end, step):
        self.calls.append(query)
        if 'node_time_seconds' in query or 'node_timex' in query:
            value = 1 if 'sync_status' in query else .001
            if self.bad_clock and f'instance="{self.bad_clock}"' in query and 'sync_status' in query:
                value = 0
            return {'aggregate': stats(value), 'series': []}
        name = next((name for name in ('num_requests_waiting', 'kv_cache_usage_perc', 'num_preemptions_total') if name in query), None)
        if not name:
            return {'aggregate': None, 'series': []}
        rows = []
        for index, replica in enumerate(INVENTORY):
            if replica['id'] == self.down or (index == 1 and name == 'num_preemptions_total' and self.missing_preemption):
                continue
            labels = {'cluster': 'lab', 'node': replica['endpoint_node'], 'instance': replica['instance'], 'engine': '0'}
            if query.startswith('timestamp('):
                times = [50, 50] if replica['id'] == self.stale and start == 100 else [start+2, end-1]
                row = {'labels': labels, 'stats': stats(times[-1]), 'source_timestamps': times}
            else:
                value = (18 if index else 0) if name == 'num_requests_waiting' else (.95 if index else .3) if name == 'kv_cache_usage_perc' else 0
                row = {'labels': labels, 'stats': {**stats(value), 'max_series_delta': value}}
            rows.append(row)
        return {'aggregate': stats(max((r['stats']['max'] for r in rows), default=0)) if rows else None, 'series': rows}


def report(source, inventory=INVENTORY):
    now, before = records()
    config = {'run_id': 'r', 'cluster': 'lab', 'rollout_replicas': inventory,
              'clock': {'monitoring_node': 'monitor'}, 'baseline': {'match_fields': ['perf/total_num_tokens']},
              'sampling': {'check_source_freshness': True}, 'prometheus': {'url': 'unused'}}
    return DiagnosticEngine(config, prometheus=source).analyze(now, [before])


def test_partial_hot_replica_is_not_hidden_by_complete_idle_replica():
    source = Replicas()
    series = {name: source.query_range_detail(query, 100, 120, 2)['series'] for name, query in (
        ('vllm_requests_waiting', 'num_requests_waiting'), ('vllm_kv_cache_usage', 'kv_cache_usage_perc'),
        ('vllm_preemptions_total', 'num_preemptions_total'))}
    chosen = select_vllm_observations(series, {'vllm_waiting': 1, 'vllm_kv_usage': .9})
    assert {item['labels']['instance'] for item in chosen.values()} == {'b:8000'}
    assert 'vllm_preemptions_total' not in chosen
    result = report(source)
    pressure = next(c for c in result['candidates'] if c['id'] == 'kv_cache_pressure')
    assert pressure['state'] == 'supporting_signal'
    assert 'vllm_preemptions_delta' in pressure['missing_evidence']
    assert {e['labels']['instance'] for e in pressure['evidence']} == {'b:8000'}


def test_fresh_replica_does_not_inherit_other_replicas_stale_timestamp():
    result = report(Replicas(stale='replica-0', missing_preemption=False))
    row = next(r for r in result['comparison']['signals'] if r['signal'] == 'vllm_requests_waiting')
    assert row['current'] == 18 and row['delta'] is not None
    assert row['sampling_quality']['current']['last_source_timestamp'] == 119
    assert not any('source_sample_before_interval' in w for w in row['sampling_quality']['current']['warnings'])


def test_declared_distributed_replica_inventory_and_scoped_queries_are_bounded():
    source = Replicas()
    result = report(source)
    nodes = clock_inventory({'rollout_replicas': INVENTORY, 'cluster': 'lab', 'clock': {'monitoring_node': 'monitor'}}, 'trainer')['nodes']
    assert set(nodes) == {'trainer', 'monitor', 'rollout-a', 'rollout-b', 'rollout-c'}
    query = next(q for q in source.calls if q.startswith('vllm:num_requests_waiting{'))
    assert 'rollout-a' in query and 'rollout-b' in query and 'rollout-c' not in query
    assert 'a:8000' in query and 'b:8000' in query
    assert sum('vllm:num_requests_waiting{' in q and not q.startswith('timestamp(') for q in source.calls) == 2
    assert result['rollout_replicas'][1]['nodes'] == ['rollout-b', 'rollout-c']
    assert any(row['row_kind'] == 'replica' for row in _investigation_rows(result))


@pytest.mark.parametrize('fault,status', [('down', 'missing'), ('bad_clock', 'clock_unverified'), ('stale', 'partial_evidence')])
def test_replica_failure_keeps_other_replicas_observations(fault, status):
    source = Replicas(**{fault: 'rollout-b' if fault == 'bad_clock' else 'replica-1'})
    rows = report(source)['rollout_replicas']
    assert rows[0]['status'] == 'observed'
    assert rows[1]['status'] == status
    assert rows[0]['signals']['vllm_requests_waiting']['current'] == 0
    if fault == 'bad_clock':
        assert rows[1]['candidates'] == []
        assert rows[1]['signals']['vllm_requests_waiting']['current'] == 18


@pytest.mark.parametrize('mutation', ['duplicate', 'node_missing', 'too_many', 'wrong_run', 'unknown_field'])
def test_inventory_is_validated_before_queries(mutation):
    inventory = copy.deepcopy(INVENTORY)
    if mutation == 'duplicate': inventory[1]['instance'] = inventory[0]['instance']
    elif mutation == 'node_missing': inventory[1]['nodes'] = ['other']
    elif mutation == 'too_many': inventory *= 9
    elif mutation == 'unknown_field': inventory[1]['fake_owner'] = 'r'
    else:
        with pytest.raises(ValueError, match='rollout_replicas'):
            DiagnosticEngine({'rollout_replicas': inventory, 'prometheus': {'url': 'unused'}})
        return
    with pytest.raises(ValueError, match='rollout_replicas'):
        report(Replicas(), inventory)


def test_unregistered_remote_observation_withholds_precise_diagnosis():
    now, before = records()
    result = DiagnosticEngine({'prometheus': {'url': 'unused'}}, prometheus=Replicas()).analyze(now, [before])
    assert result['clock_quality']['status'] == 'unknown'
    assert result['candidates'] == []
    assert result['evidence']['vllm_requests_waiting']['max'] == 18


def test_source_quality_and_counter_baseline_do_not_cross_engine_identity():
    class Mixed(Replicas):
        def query_range_detail(self, query, start, end, step):
            result = super().query_range_detail(query, start, end, step)
            if start == 60:
                for row in result['series']:
                    row['labels']['engine'] = 'restarted-engine'
            return result
    result = report(Mixed(missing_preemption=False))
    row = next(r for r in result['comparison']['signals'] if r['signal'] == 'vllm_requests_waiting')
    assert row['baseline'] is None and row['delta'] is None
    assert result['rollout_replicas'][1]['signals']['vllm_requests_waiting']['baseline'] is None


def test_replica_metadata_schema_and_partial_evidence_do_not_invent_preemptions():
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator
    result = report(Replicas())
    Draft202012Validator(json.loads((Path(__file__).parents[1] / 'config/diagnosis.schema.json').read_text())).validate(result)
    replica = result['rollout_replicas'][1]
    assert replica['signals']['vllm_kv_cache_usage']['unit'] == 'ratio'
    assert replica['signals']['vllm_requests_waiting']['unit'] == 'requests'
    assert 'vllm_preemptions_delta' not in replica['signals']
    missing = report(Replicas(down='replica-1'))['rollout_replicas'][1]
    assert missing['missing_sources'] == ['endpoint:no_returned_observations']


def test_replica_baseline_does_not_bypass_reference_session_change():
    now, before = records()
    for record, session in ((now, 'new'), (before, 'old')):
        window = record['analysis_window']
        window['time_alignment'] = {'status': 'aligned', 'method': 'four_timestamp', 'node': 'trainer',
            'reference_id': 'monitor', 'reference_session': session,
            'raw_window': {'start': window['start'], 'end': window['end']},
            'offset_seconds': 0, 'uncertainty_seconds': .02, 'exchange_uncertainty_seconds': .02,
            'valid_from': 0, 'valid_until': 200, 'local_anchor': 0, 'drift_ppm': 0, 'round_trip_seconds': .04}
    config = {'run_id': 'r', 'cluster': 'lab', 'rollout_replicas': INVENTORY,
              'clock': {'monitoring_node': 'monitor', 'calibration_reference': 'monitor'},
              'sampling': {'check_source_freshness': True}, 'prometheus': {'url': 'unused'}}
    result = DiagnosticEngine(config, prometheus=Replicas()).analyze(now, [before])
    assert result['clock_quality']['baseline']['status'] == 'unknown'
    assert result['rollout_replicas'][1]['baseline_clock_status'] == 'unknown'
    assert result['rollout_replicas'][1]['signals']['vllm_requests_waiting']['delta'] is None


@pytest.mark.parametrize('alignment', [None, []])
def test_invalid_calibration_retains_raw_replica_data_without_crashing(alignment):
    now, before = records()
    now['analysis_window']['time_alignment'] = alignment
    config = {'run_id': 'r', 'cluster': 'lab', 'rollout_replicas': INVENTORY,
              'clock': {'monitoring_node': 'monitor', 'calibration_reference': 'monitor'},
              'prometheus': {'url': 'unused'}}
    result = DiagnosticEngine(config, prometheus=Replicas()).analyze(now, [before])
    assert result['rollout_replicas'][1]['clock_status'] == 'unknown'
    assert result['rollout_replicas'][1]['signals']['vllm_requests_waiting']['current'] == 18
