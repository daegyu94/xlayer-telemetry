"""Common Mooncake observations remain useful without assuming a storage backend."""
import json

import pytest

from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, _investigation_rows


CLIENT = dict(cluster='lab', node='rollout', instance='client-a', component='client',
              client_mode='real', cluster_id='store-a')
READ_LATENCY = 'mooncake_dfs_read_p95_seconds'
READ_ERRORS = 'mooncake_dfs_read_errors_per_second'


class Native:
    def __init__(self, *, latency=True, errors=0, error_client='client-a', unsafe=False, stale=False, failed=False):
        self.latency, self.errors, self.error_client = latency, errors, error_client
        self.unsafe, self.stale, self.failed = unsafe, stale, failed
        self.calls = []

    def query_range_detail(self, query, start, end, step):
        self.calls.append(query)
        if 'node_time_seconds' in query or 'node_timex' in query:
            value = (0 if self.unsafe else 1) if 'sync_status' in query else .001
            return {'aggregate': dict(min=value, max=value, mean=value, last=value, sample_count=3), 'series': []}
        if 'mooncake' not in query:
            return {'aggregate': None, 'series': []}
        if self.failed and end == 100:
            raise ValueError('synthetic native source failure')
        labels = dict(CLIENT)
        if 'mooncake_dfs_read_errors_total' in query:
            labels.update(instance=self.error_client, error='READ_FAIL')
            value = self.errors if end == 100 else 0
        elif 'mooncake_dfs_read_latency_us_bucket' in query and self.latency:
            value = .03 if end == 100 else .01
        else:
            return {'aggregate': None, 'series': []}
        if 'timestamp(' in query:
            value = start - 5 if self.stale and end == 100 else end
        stats = dict(min=value, max=value, mean=value, last=value, sample_count=3)
        return {'aggregate': stats, 'series': [{'labels': labels, 'stats': stats,
            'source_timestamps': [value] if 'timestamp(' in query and self.stale else [start, (start+end)/2, end]}]}


def analyze(source, *, slow=True, threefs=None):
    config = {'cluster': 'lab', 'rollout_node': 'rollout', 'clock': {'monitoring_node': 'trainer'},
              'sampling': {'check_source_freshness': True},
              'prometheus': {'url': 'http://unused', 'metric_profiles': ['mooncake']}}
    if threefs is not None:
        config['threefs'] = {'url': 'http://unused'}
    before = dict(record_id='before', run_id='run', node='trainer', worker_id='driver', step=1,
                  step_duration_seconds=10, observed_at=70, analysis_window={'start': 60, 'end': 70})
    now = {**before, 'record_id': 'now', 'step': 2, 'step_duration_seconds': 20 if slow else 10,
           'observed_at': 100, 'analysis_window': {'start': 90, 'end': 100}}
    return DiagnosticEngine(config, prometheus=source, threefs=threefs).analyze(now, [before])


def candidate(report):
    return next(row for row in report['candidates'] if row['id'] == 'mooncake_dfs_read_pressure')


def test_slow_step_dfs_latency_creates_common_candidate_without_clickhouse():
    source = Native()
    report = analyze(source)
    row = candidate(report)
    assert row['state'] == 'supporting_signal'
    assert [e['signal'] for e in row['evidence']] == ['step_duration_seconds', READ_LATENCY]
    assert row['evidence'][1]['unit'] == 'seconds'
    assert row['evidence'][1]['labels'] == CLIENT
    assert any(e['signal'] == READ_ERRORS and e['value'] == 0 for e in row['counter_evidence'])
    assert 'storage_backend_identity_unverified' in row['missing_evidence']
    assert not any('3fs' in item for item in row['missing_evidence'])
    assert 'workload_comparability_unverified' in row['missing_evidence']
    assert 'storage_series' not in report
    assert report['storage_overview']['backend'] == {'status': 'not_reported', 'adapter': None}
    assert report['storage_overview']['threefs']['status'] == 'not_configured'
    assert len([q for q in source.calls if 'mooncake' in q and 'timestamp(' not in q]) == 12


def test_all_failed_batches_have_error_evidence_without_inventing_latency():
    report = analyze(Native(latency=False, errors=.25))
    row = candidate(report)
    assert row['state'] == 'supporting_signal'
    assert any(e['signal'] == READ_ERRORS and e['value'] == .25 for e in row['evidence'])
    assert READ_LATENCY in row['missing_evidence']
    assert not any(e['signal'] == READ_LATENCY for e in row['evidence'] + row['counter_evidence'])


def test_other_client_errors_cannot_supply_combined_evidence_or_counter():
    row = candidate(analyze(Native(errors=1, error_client='client-b')))
    assert all(e['signal'] != READ_ERRORS for e in row['evidence'] + row['counter_evidence'])
    assert READ_ERRORS + ':entity_mismatch' in row['missing_evidence']


@pytest.mark.parametrize('source', [Native(unsafe=True, errors=1), Native(latency=False, errors=1, stale=True), Native(failed=True)])
def test_unsafe_stale_or_failed_observations_do_not_create_candidate(source):
    report = analyze(source)
    assert not any(row['id'].startswith('mooncake_dfs') for row in report['candidates'])
    entries = report['storage_overview']['signals']
    read = next(row for row in entries if row['signal'] == (READ_ERRORS if source.stale else READ_LATENCY))
    assert read['status'] in {'clock_unverified', 'stale', 'query_failed'}


def test_normal_step_is_not_promoted_by_storage_spike():
    assert not any(row['id'].startswith('mooncake_dfs') for row in analyze(Native(errors=1), slow=False)['candidates'])


def test_storage_overview_projection_preserves_zero_missing_quality_and_unknown_backend():
    report = analyze(Native())
    summary = next(row for row in _investigation_rows(report) if row['row_kind'] == 'summary')
    overview = json.loads(summary['storage_overview'])
    error = next(row for row in overview['signals'] if row['signal'] == READ_ERRORS)
    assert error['current'] == 0 and error['status'] == 'observed'
    latency = next(row for row in overview['signals'] if row['signal'] == READ_LATENCY)
    assert latency['scope'] == 'shared-service' and 'range_window_exceeds_interval' in latency['quality_issues']
    assert overview['backend']['status'] == 'not_reported'
    assert len(summary['storage_overview']) < 16384


def test_configured_empty_threefs_does_not_identify_actual_mooncake_backend():
    class Empty:
        def query_window(self, *args): return []
    report = analyze(Native(), threefs=Empty())
    assert report['storage_overview']['backend']['status'] == 'not_reported'
    assert report['storage_overview']['threefs']['status'] == 'no_data'
    assert candidate(report)['state'] == 'supporting_signal'


def test_write_failures_and_skips_are_distinct_populations_without_error_fraction():
    from xlayer_telemetry.analysis.diagnosis_analysis import evaluate_rules
    errors = 'mooncake_dfs_write_errors_per_second'
    base = {'step_duration_seconds': 10}
    context = {'signal_labels': {errors: dict(CLIENT, error='CHECKSUM_MISMATCH')}}
    rows = evaluate_rules({'step_duration_seconds': 20, errors: .1}, base, thresholds={}, context=context)
    row = next(r for r in rows if r['id'] == 'mooncake_dfs_write_pressure')
    assert row['state'] == 'supporting_signal'
    assert row['evidence'][-1]['value'] == .1
    skipped = evaluate_rules({'step_duration_seconds': 20, 'mooncake_dfs_skipped_keys_per_second': 100}, base, thresholds={}, context=context)
    assert not any(r['id'].startswith('mooncake_dfs_') for r in skipped)
    unknown = evaluate_rules({'step_duration_seconds': 20, errors: .1}, base, thresholds={}, context={})
    row = next(r for r in unknown if r['id'] == 'mooncake_dfs_write_pressure')
    assert row['state'] == 'weak_signal' and errors + ':entity_unverified' in row['missing_evidence']


def test_threefs_query_failure_keeps_common_candidate_and_exposes_source_failure():
    class Failed:
        def query_window(self, *args): raise TimeoutError('hidden source credential')
    report = analyze(Native(), threefs=Failed())
    assert candidate(report)['state'] == 'supporting_signal'
    assert report['storage_overview']['threefs']['status'] == 'query_failed'
    assert 'hidden source credential' not in json.dumps(report)


def test_demo_publishes_one_complete_projection_after_native_coverage_enrichment(tmp_path):
    from xlayer_telemetry.demos.diagnosis import generate
    from xlayer_telemetry.analysis.diagnostics import write_report
    root = tmp_path / 'owned-demo'
    report = generate(root, run_id='owned-demo', publish_diagnosis=False)
    assert not (root / 'diagnostics/latest.json').exists()
    report['storage_overview'] = analyze(Native())['storage_overview']
    write_report(root / 'diagnostics', report)
    assert len((root / 'diagnostics/diagnostics.jsonl').read_text().splitlines()) == 1
    summary, = [json.loads(line) for path in (root / 'diagnostics/investigation').glob('*.jsonl')
                for line in path.read_text().splitlines() if json.loads(line)['row_kind'] == 'summary']
    assert json.loads(summary['storage_overview'])['backend']['status'] == 'not_reported'


def test_default_coverage_is_compact_and_adds_no_storage_query():
    source = Native()
    report = DiagnosticEngine({'prometheus': {'url': 'http://unused'}}, prometheus=source).analyze(None, [])
    assert report['storage_overview']['signals'] == []
    assert len(json.dumps(report['storage_overview'])) < 512
    assert not any('mooncake' in query for query in source.calls)


def test_zero_count_threefs_reports_do_not_claim_observed_distribution_coverage():
    class EmptyPopulation:
        def query_window(self, *args):
            return [{'metricName':'read_latency', 'labels':{'host':'archive'}, 'count':0,
                     'max_observed_p99':999, 'weighted_mean':999}]
    report = analyze(Native(), threefs=EmptyPopulation())
    assert report['storage_overview']['threefs']['status'] == 'no_data'
