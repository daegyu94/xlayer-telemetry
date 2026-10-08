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
