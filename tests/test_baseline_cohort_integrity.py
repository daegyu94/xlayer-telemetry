"""Reject mismatched executions and incomplete history before diagnosis."""

import pytest

from xlayer_telemetry.analysis.diagnosis_analysis import select_baseline
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine


def record(stamp, *, duration=10, **changes):
    return {
        "record_id": str(stamp), "run_id": "run", "node": "node",
        "worker_id": "worker", "boundary_scope": "rl_step", "execution_mode": "sync",
        "observed_at": stamp, "step_duration_seconds": duration,
        "stage_durations_seconds": {"gen": duration},
        "analysis_window": {"start": stamp-duration, "end": stamp, "accuracy": "approximate"},
        "workload": {"policy_version": 128, "perf/total_num_tokens": 1000},
        **changes,
    }


@pytest.mark.parametrize("field,other", [
    ("execution_mode", "async"), ("cluster", "other"), ("producer", "other"),
    ("role", "other"), ("rank", 1), ("local_rank", 1), ("gpu", "1"),
])
def test_explicit_execution_identity_is_not_substituted(field, other):
    selected = "sync" if field == "execution_mode" else 0 if isinstance(other, int) else "selected"
    current = record(100, **{field: selected})
    wrong = record(80, **{field: other})
    missing = record(70)
    missing.pop(field, None)
    assert select_baseline(current, [wrong, missing]) is None
    matching = record(60, **{field: current[field]})
    assert select_baseline(current, [wrong, matching])["record_id"] == "60"


@pytest.mark.parametrize("field", ["policy_version", "fully_async/count/current_param_version"])
def test_policy_identifiers_are_exact_even_with_numeric_tolerance(field):
    current = record(100, workload={field: 128, "perf/total_num_tokens": 1000})
    old_policy = record(80, workload={field: 127, "perf/total_num_tokens": 1000})
    policy = {"match_fields": [field, "perf/total_num_tokens"], "relative_tolerance": .1}
    assert select_baseline(current, [old_policy], policy=policy) is None
    comparable = record(60, workload={field: 128, "perf/total_num_tokens": 950})
    assert select_baseline(current, [old_policy, comparable], policy=policy)["record_id"] == "60"


class NoMetrics:
    def __init__(self):
        self.windows = []

    def query_range(self, query, start, end, step):
        self.windows.append((start, end))
        return None


@pytest.mark.parametrize("invalid", [
    {"execution_mode": "async"},
    {"analysis_window": {"start": None, "end": None, "accuracy": "unknown"}},
    {"observed_at": 110, "analysis_window": {"start": 109, "end": 110}},
    {"analysis_window": {"start": 79, "end": 80, "accuracy": "clock_discontinuity"}},
])
def test_stage_slowdown_uses_same_valid_prior_cohort_as_step_baseline(invalid):
    source = NoMetrics()
    report = DiagnosticEngine({"prometheus": {"url": "http://unused"}}, prometheus=source).analyze(
        record(100), [record(80, duration=1, **invalid)],
    )
    assert report["comparison"]["baseline_record_id"] is None
    assert report["symptom"]["slow_stages"] == []
    assert not report["findings"]
    assert all(window == (90, 100) for window in source.windows)


def test_recent_stage_and_step_selection_share_five_prior_observations():
    history = [record(stamp, duration=1 if stamp < 50 else 10) for stamp in range(10, 100, 10)]
    report = DiagnosticEngine({"prometheus": {"url": "http://unused"}}, prometheus=NoMetrics()).analyze(
        record(110), list(reversed(history)),
    )
    assert report["symptom"]["slow_stages"] == []
    assert report["comparison"]["baseline_record_id"] == "90"


class FreshPressureMetrics:
    def query_range_detail(self, query, start, end, step):
        if query.startswith('timestamp('):
            stats = {'max': end - 1}
            return {'aggregate': stats, 'series': [{'stats': stats, 'source_timestamps': [start + 1, end - 1]}]}
        labels = {'node': 'node', 'instance': 'endpoint'}
        if 'gpu_utilization' in query:
            value = 40 if end == 100 else 90
            labels['gpu'] = '0'
        elif 'num_requests_waiting' in query:
            value = 3
        else:
            return {'aggregate': None, 'series': []}
        stats = {'min': value, 'max': value, 'mean': value, 'sample_count': 5}
        return {'aggregate': stats, 'series': [{'labels': labels, 'stats': stats}]}


@pytest.mark.parametrize('configured', [False, True])
def test_duration_candidate_requires_declared_workload_comparability_for_strong(configured):
    config = {'prometheus': {'url': 'http://unused'}, 'sampling': {'check_source_freshness': True}}
    if configured:
        config['baseline'] = {'match_fields': ['perf/total_num_tokens'], 'relative_tolerance': 0}
    report = DiagnosticEngine(config, prometheus=FreshPressureMetrics(), clock=lambda: 110).analyze(
        record(100, duration=20), [record(60)])
    candidate = next(c for c in report['candidates'] if c['id'] == 'gpu_starvation')
    assert candidate['state'] == ('strong_signal' if configured else 'supporting_signal')
    assert ('workload_comparability_unverified' in candidate['missing_evidence']) is not configured
    assert report['comparison']['baseline_record_id'] == '60'
    assert next(r for r in report['comparison']['signals'] if r['signal'] == 'step_duration_seconds')['delta'] == 10


def test_token_only_workload_change_cannot_create_strong_resource_regression():
    config = {'prometheus': {'url': 'http://unused'}, 'sampling': {'check_source_freshness': True}}
    current = record(100, duration=25, workload={'policy_version': 11, 'perf/total_num_tokens': 4000})
    baseline = record(60, workload={'policy_version': 10, 'perf/total_num_tokens': 1000})
    report = DiagnosticEngine(config, prometheus=FreshPressureMetrics(), clock=lambda: 110).analyze(current, [baseline])
    candidate = next(c for c in report['candidates'] if c['id'] == 'gpu_starvation')
    assert candidate['state'] != 'strong_signal'
    assert 'workload_comparability_unverified' in candidate['missing_evidence']
    config['baseline'] = {'match_fields': ['perf/total_num_tokens'], 'relative_tolerance': .1}
    report = DiagnosticEngine(config, prometheus=FreshPressureMetrics(), clock=lambda: 110).analyze(current, [baseline])
    assert report['comparison']['baseline_record_id'] is None
    assert not any(c['id'] == 'gpu_starvation' for c in report['candidates'])
