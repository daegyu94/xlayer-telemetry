from copy import deepcopy
import json
import sys

import pytest

from xlayer_telemetry.analysis.behavior_signature import Boundary, candidates, compare, encode, summarize
from xlayer_telemetry.analysis.triggered_profiling import CapturePolicy, TriggeredProfiler
from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.step_history import StepHistoryWriter


def boundary(step=1, *, worker="0", **kwargs):
    return Boundary(CorrelationContext("research", "sdk", "actor", worker, "node", gpu=worker),
                    step=step, sequence=step, duration_seconds=kwargs.pop("duration_seconds", 1),
                    accuracy=kwargs.pop("accuracy", "exact"), workload=kwargs.pop("workload", {"tokens": 100}), **kwargs)


def span(item, name="work", phase="cpu", duration=.1, **extra):
    return {**item.context.as_dict(), "record_type": "span", "name": name, "phase": phase,
            "step": item.step, "duration_seconds": duration, "boundary_accuracy": "exact",
            "trace_id": "trace", "span_id": name, "attributes": {}, **extra}


def observation(signal, value, scope="node", **extra):
    return {"signal": signal, "value": value, "scope": scope, "source": "synthetic", "accuracy": "sampled",
            "unit": "percent" if signal == "gpu_utilization_percent" else "ratio",
            "entity": {"node": "node"}, "status": "observed", **extra}


def test_sdk_events_and_explicit_parent_relations(tmp_path):
    before = boundary(step=0)
    recorder = EventRecorder(tmp_path, before.context, clock_ns=iter(range(0, 1000000000, 100000000)).__next__)
    with recorder.span("rollout", phase="rollout", step=0) as parent:
        with recorder.span("tool", phase="cpu", step=0, trace_id=parent.trace_id, parent_span_id=parent.span_id):
            pass
    events = [json.loads(line) for line in recorder.path.read_text().splitlines()]
    signature = summarize(before, events)
    assert signature["relations"][0]["parent_name"] == "rollout"
    assert signature["relations"][0]["child_duration_sum"] == .1
    assert "trace_id" not in encode(signature)
    assert signature["quality"]["event_accuracy_counts"] == {"exact": 2}


def test_no_parent_join_from_clock_proximity_or_other_trace():
    item = boundary()
    result = summarize(item, [span(item, "parent"), span(item, "child", parent_span_id="parent", trace_id="different")])
    assert result["relations"] == []
    assert result["quality"]["unresolved_parent_edges"] == 1
    assert not summarize(item, [span(item, "parent"), span(item, "child")])["relations"]


def test_unknown_time_preserved_and_cycles_rejected():
    item = boundary(accuracy="unknown")
    result = summarize(item, [span(item, "a", parent_span_id="b", boundary_accuracy="unknown"), span(item, "b", parent_span_id="a")])
    assert result["boundary"]["accuracy"] == "unknown"
    assert result["quality"]["invalid_parent_edges"] == 2
    assert not result["relations"]


def test_budget_consumes_at_most_one_lookahead_and_preserves_incomplete():
    item = boundary()
    visited = []
    def stream():
        for i in range(100):
            visited.append(i)
            yield span(item, str(i))
    result = summarize(item, stream(), max_records=3, max_groups=2)
    assert len(visited) == 4
    assert result["quality"]["events_truncated"] is True
    assert result["quality"]["dropped_groups"] == 1
    assert len(result["events"]) == 2


def test_duplicate_spans_and_wrong_worker_are_not_accumulated():
    item = boundary()
    event = span(item)
    result = summarize(item, [event, event, span(boundary(worker="1"))])
    assert result["events"][0]["count"] == 1
    assert result["quality"]["duplicate_spans"] == 1
    assert result["quality"]["excluded_events"] == 1


def test_rollout_and_async_membership_require_instrumentation():
    item = boundary(scope="rollout", phase="rollout", rollout_id="r1")
    result = summarize(item, [span(item, attributes={"rollout_id": "r1"}), span(item, "other", attributes={"rollout_id": "r2"})])
    assert result["quality"]["accepted_events"] == 1
    item = boundary(scope="trainer_update")
    result = summarize(item, [span(item), span(item, "explicit", attributes={"boundary_scope": "trainer_update"})])
    assert result["quality"]["accepted_events"] == 1


def test_step_adapter_never_invents_replay_time(tmp_path):
    context = boundary().context
    writer = StepHistoryWriter(tmp_path / "steps.jsonl", run_id=context.run_id, node=context.node, worker_id=context.worker_id)
    record = writer.append({"step": 1, "data": {"timing_s/step": 5}}, live=False)
    result = Boundary.from_step(record, context)
    assert result.accuracy == "unknown"
    assert result.duration_seconds == 5
    with pytest.raises(ValueError, match="identity"):
        Boundary.from_step(record, boundary(worker="1").context)


def test_workload_scope_history_and_peer_matching():
    item = summarize(boundary(step=2, duration_seconds=3), [])
    history = [summarize(boundary(step=0), []), summarize(boundary(step=1), []),
               summarize(boundary(step=3, duration_seconds=100), []),
               summarize(boundary(step=1, workload={"tokens": 200}), []), summarize(boundary(step=1, scope="trainer_update"), [])]
    result = compare(item, history)
    assert result["reference_count"] == 2
    assert result["duration_ratio"] == 3
    peers = [summarize(boundary(step=2, worker="1"), []), item]
    assert compare(item, peers, peer=True)["reference_count"] == 1
    assert compare(item, peers)["reference_count"] == 0
    assert compare(summarize(boundary(workload={}), []), history)["slow"] is None


def test_relation_delta_distinguishes_fanout_from_child_cost():
    before, now = boundary(step=0), boundary(step=1)
    old = summarize(before, [span(before, "parent", phase="rollout"), span(before, "tool", parent_span_id="parent")])
    current = summarize(now, [span(now, "parent", phase="rollout"), span(now, "tool", parent_span_id="parent"),
                              span(now, "tool", span_id="tool2", parent_span_id="parent")])
    row = compare(current, [old])["relations"][0]
    assert row["count_delta"] == 1
    assert row["duration_delta"] == pytest.approx(.1)


def test_missing_stale_failed_unknown_and_zero_are_distinct():
    observations = [observation("host_cpu_pressure_ratio", 0),
                    *[observation("other", 123, status=status) for status in ("stale", "query_failed", "no_data", "not_configured", "unknown")]]
    signature = summarize(boundary(), [], observations)
    assert signature["observations"][0]["value"] == 0
    assert all(row["value"] is None for row in signature["observations"][1:])
    assert len({row["status"] for row in signature["observations"]}) == 6


def test_entity_is_never_substituted_in_delta_or_candidate():
    before, now = boundary(step=0), boundary(step=1)
    old = summarize(before, [span(before, phase="compute")], [observation("gpu_utilization_percent", 40, "device", entity={"node": "node", "gpu": "1", "engine": "e1"})])
    current = summarize(now, [span(now, phase="compute", duration=.3)], [observation("gpu_utilization_percent", 99, "device", entity={"node": "node", "gpu": "1", "engine": "e1"})])
    result = compare(current, [old])
    row = next(row for row in candidates(current, result) if row["candidate"] == "gpu_pressure")
    assert row["state"] == "supporting_signal"
    assert row["missing"][0]["reason"] == "missing_matching_entity"
    changed_entity = deepcopy(current)
    changed_entity["observations"][0]["entity"]["engine"] = "e2"
    assert compare(changed_entity, [old])["observations"][0]["delta"] is None


def test_candidates_keep_against_missing_and_source_quality():
    before, now = boundary(step=0), boundary(step=1, duration_seconds=2)
    old = summarize(before, [span(before)])
    current = summarize(now, [span(now, duration=.3)], [observation("host_cpu_pressure_ratio", .4), observation("disk_busy_ratio", 0, "device")])
    rows = candidates(current, compare(current, [old]))
    assert rows[0]["state"] == "supported_candidate"
    assert rows[0]["confidence_kind"] == "source_quality"
    assert rows[0]["causality"] == "not_established"
    assert rows[3]["against"][0]["value"] == 0
    assert rows[3]["missing"]


@pytest.mark.parametrize("current_accuracy,baseline_accuracy", [("unknown", "exact"), ("exact", "unknown"), ("approximate", "exact")])
def test_unrelated_exact_span_does_not_validate_slow_span(current_accuracy, baseline_accuracy):
    before, now = boundary(step=0), boundary(step=1)
    old = summarize(before, [span(before, "slow", boundary_accuracy=baseline_accuracy), span(before, "normal")])
    current = summarize(now, [span(now, "slow", duration=.4, boundary_accuracy=current_accuracy), span(now, "normal")],
                        [observation("host_cpu_pressure_ratio", .4)])
    comparison = compare(current, [old])
    assert comparison["events"][0]["delta"] == pytest.approx(.3)
    selected = candidates(current, comparison)[0]
    assert selected["state"] == "supporting_signal"
    assert any(row["reason"] == "span_time_unknown_or_approximate" for row in selected["missing"])


def test_point_event_and_one_exact_span_do_not_validate_mixed_duration_group():
    before, now = boundary(step=0), boundary(step=1)
    old = summarize(before, [span(before)])
    current = summarize(now, [span(now), span(now, duration=.9, span_id="unknown", boundary_accuracy="unknown"),
                              span(now, record_type="event")], [observation("host_cpu_pressure_ratio", .4)])
    assert current["events"][0]["duration_seconds"]["mean"] == .5
    assert candidates(current, compare(current, [old]))[0]["state"] == "supporting_signal"


@pytest.mark.parametrize("budget", ["events_truncated", "observations_truncated", "dropped_groups"])
def test_incomplete_reference_cannot_be_a_complete_baseline(budget):
    before, now = boundary(step=0), boundary(step=1)
    old = summarize(before, [span(before)])
    old["quality"][budget] = 1
    current = summarize(now, [span(now, duration=.4)], [observation("host_cpu_pressure_ratio", .4)])
    result = compare(current, [old])
    assert result["reference_count"] == 0
    assert result["rejected_incomplete_references"] == 1
    assert result["missing_evidence"]
    assert candidates(current, result)[0]["state"] != "supported_candidate"
    complete = summarize(before, [span(before)])
    result = compare(current, [old, complete])
    assert result["reference_count"] == 1
    assert candidates(current, result)[0]["state"] == "supported_candidate"


def test_optional_hook_delivery_cooldown_duplicate_and_budget(tmp_path):
    output = tmp_path / "request.json"
    code = "import sys,pathlib; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())"
    clock = [0]
    controller = TriggeredProfiler([sys.executable, "-c", code, str(output)], policy=CapturePolicy(max_attempts=2), clock=lambda: clock[0])
    signature = summarize(boundary(), [])
    comparison = {"slow": True, "duration_ratio": 2}
    assert TriggeredProfiler().request(signature, comparison)["state"] == "disabled"
    assert controller.request(signature, comparison)["state"] == "hook_delivered"
    assert json.loads(output.read_text())["target"] == "next_cooperative_window"
    assert controller.request(signature, comparison)["state"] == "duplicate"
    second = summarize(boundary(step=2), [])
    assert controller.request(second, comparison)["state"] == "cooldown"
    clock[0] = 60
    assert controller.request(second, comparison)["capture_verified"] is False
    assert controller.request(summarize(boundary(step=3), []), comparison)["state"] == "budget_exhausted"


def test_hook_timeout_failure_and_unknown_time_do_not_escape():
    policy = CapturePolicy(window_seconds=.01, timeout_seconds=.04, cooldown_seconds=.1)
    signature = summarize(boundary(), [])
    comparison = {"slow": True, "duration_ratio": 2}
    controller = TriggeredProfiler([sys.executable, "-c", "import time; time.sleep(60)"], policy=policy)
    assert controller.request(signature, comparison)["state"] == "hook_timeout"
    controller = TriggeredProfiler(["/nonexistent/contains-secret"])
    result = controller.request(signature, comparison)
    assert result == {"state": "hook_failed", "error_type": "FileNotFoundError"}
    assert "contains-secret" not in json.dumps(result)
    assert controller.attempts == 1
    assert controller.request(summarize(boundary(accuracy="unknown"), []), comparison)["state"] == "missing_evidence"


@pytest.mark.parametrize("kwargs", [{"max_records": 0}, {"max_groups": 65}, {"max_observations": True}])
def test_signature_budgets_validated(kwargs):
    with pytest.raises(ValueError):
        summarize(boundary(), [], **kwargs)


def test_calibrated_boundary_requires_reference_and_preserves_uncertainty():
    with pytest.raises(ValueError):
        boundary(accuracy="calibrated")
    item = boundary(accuracy="calibrated", time_reference="reference", time_uncertainty_seconds=.02)
    assert summarize(item, [])["boundary"]["time_uncertainty_seconds"] == .02


def test_deep_parent_chain_is_bounded_and_malformed_edge_is_missing():
    item = boundary()
    events = [span(item, span_id=str(index), parent_span_id=str(index-1) if index else None) for index in range(2000)]
    signature = summarize(item, events)
    assert signature["relations"][0]["count"] == 1999
    signature = summarize(item, [span(item, "bad", parent_span_id=["invalid"])])
    assert signature["quality"]["invalid_parent_edges"] == 1


def test_original_and_calibrated_time_are_kept_separate():
    item = boundary()
    signature = summarize(item, [span(item, boundary_accuracy="calibrated", start_time_unix_nano=10,
                                      end_time_unix_nano=20, correlation_start_time_unix_nano=110,
                                      correlation_end_time_unix_nano=120, time_reference="r", time_uncertainty_seconds=.1)])
    provenance = signature["time_provenance"]
    assert provenance["original_start_ns_min"] == 10
    assert provenance["correlation_start_ns_min"] == 110
    assert provenance["references"] == ["r"]
    assert provenance["max_uncertainty_seconds"] == .1


def test_unknown_boundary_limits_resource_candidate_and_trigger():
    before = boundary(step=0)
    now = boundary(accuracy="unknown")
    signature = summarize(now, [span(now, duration=.4)], [observation("host_cpu_pressure_ratio", .5)])
    row = candidates(signature, compare(signature, [summarize(before, [span(before)])]))[0]
    assert row["state"] == "supporting_signal"
    assert any(item["reason"] == "boundary_time_unknown_for_resource_correlation" for item in row["missing"])


def test_event_engine_and_metric_engine_cannot_supply_combined_evidence():
    before, now = boundary(step=0), boundary(step=1)
    old = summarize(before, [span(before, phase="compute", attributes={"engine": "e2"})])
    current = summarize(now, [span(now, phase="compute", duration=.4, attributes={"engine": "e2"})],
                        [observation("gpu_utilization_percent", 99, "device", entity={"node": "node", "gpu": "0", "engine": "e1"})])
    candidate = candidates(current, compare(current, [old]))[1]
    assert candidate["state"] == "supporting_signal"
    assert any(item["reason"] == "missing_matching_instrumented_resource_identity" for item in candidate["missing"])
    old["events"][0]["entity"]["engine"] = "e1"
    assert compare(current, [old])["events"][0]["delta"] is None


def test_units_and_duplicate_reference_identity_cannot_inflate_evidence():
    before, now = boundary(step=0), boundary(step=1)
    old = summarize(before, [span(before)])
    current = summarize(now, [span(now, duration=.4)], [observation("host_cpu_pressure_ratio", 50, unit="percent")])
    comparison = compare(current, [old, old, old])
    assert comparison["reference_count"] == 1
    candidate = candidates(current, comparison)[0]
    assert candidate["state"] == "supporting_signal"
    assert any(item["reason"] == "unit_mismatch_or_unknown" for item in candidate["missing"])


def test_synthetic_benchmark_preserves_missing_mixed_and_async_boundaries(tmp_path):
    from examples.research.behavior_signature_benchmark import run
    report = run(tmp_path / "experiment", cases=2, repeats=1, iterations=1)
    assert all(row["cases"] == row["correct_unique_candidate"] for row in report["classification"].values())
    assert report["classification"]["normal"]["slow_detected"] == 0
    for name in ("missing_source_preserved", "mixed_candidates_preserved", "changed_workload_rejected", "slow_rollout_normal_async_update"):
        assert report[name] == {"correct": 2, "total": 2}
    assert report["volume"]["reduction_ratio"] > 10
    assert report["relation_delta"]["higher_child_cost"]["count_delta"] == 0
    assert report["relation_delta"]["higher_fanout"]["count_delta"] == 64
    assert (tmp_path / "experiment" / "report.json").is_file()
