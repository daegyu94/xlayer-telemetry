"""Distributed demo evidence is explicit, bounded and opt-in."""
import json
from pathlib import Path
import statistics

import pytest

from xlayer_telemetry.demos.diagnosis import generate
from xlayer_telemetry.demos.scenario import make_scenario
from xlayer_telemetry.events import CorrelationContext


def workers(run_id="distributed"):
    return [CorrelationContext(run_id=run_id, producer="demo_distributed", role="rollout", worker_id=f"rollout-{i}",
                               node=f"gpu-node-{i % 3}", gpu=str(i // 3)) for i in range(4)]


def read_events(root):
    return [json.loads(line) for path in (root / "telemetry-events").glob("*.jsonl")
            for line in path.read_text().splitlines()]


def test_optional_multiworker_fixture_contains_comparable_peer_spans_and_applied_policy(tmp_path):
    scenario = make_scenario(start=1000, run_id="distributed", node="gpu-node-0")
    report = generate(tmp_path / "multi", run_id="distributed", node="gpu-node-0", scenario=scenario,
                      rollout_workers=workers(), clock=lambda: 1090)
    records = read_events(tmp_path / "multi")
    spans = [r for r in records if r.get("producer") == "demo_distributed" and r["record_type"] == "span"]
    assert len(spans) == 8 and len({(r["node"], r["worker_id"], r["gpu"]) for r in spans}) == 4
    assert len({r["attributes"]["workload_fingerprint"] for r in spans}) == 1
    for frame in scenario["frames"]:
        peers = [r for r in spans if r["step"] == frame["step"]]
        phase = frame["phases"][0]
        assert len(peers) == 4
        assert all(phase["start"] * 1e9 <= r["start_time_unix_nano"] < r["end_time_unix_nano"] <= phase["end"] * 1e9 for r in peers)
        assert all(r["boundary_accuracy"] == "calibrated" and r["attributes"]["data_origin"] == "synthetic" for r in peers)
        durations = sorted(r["duration_seconds"] for r in peers)
        if frame["scenario"] == "storage-regression":
            assert durations[-1] >= statistics.median(durations[:-1]) * 2
        else:
            assert durations[-1] <= statistics.median(durations[:-1]) * 1.2
    applied = [r for r in records if r.get("name") == "weights.applied"]
    assert len(applied) == 8
    assert all(r["attributes"]["policy_scope"] == "worker_applied" and r["attributes"]["data_origin"] == "synthetic" for r in applied)
    for record in applied:
        span = next(r for r in spans if r["step"] == record["step"] and r["worker_id"] == record["worker_id"])
        assert record["node"] == span["node"] and record["trace_id"] == span["trace_id"]
        assert record["policy_version"] == span["policy_version"] == 128
    assert report["comparison"]["workload_comparability"] == "matched_configured_fields"


def test_default_scenario_does_not_invent_distributed_workers(tmp_path):
    scenario = make_scenario(start=1000, run_id="distributed", node="gpu-node-0")
    generate(tmp_path / "single", run_id="distributed", node="gpu-node-0", scenario=scenario, clock=lambda: 1090)
    assert not any(r.get("name") == "weights.applied" or r.get("producer") == "demo_distributed" for r in read_events(tmp_path / "single"))


@pytest.mark.parametrize("contexts", [workers()[:2], workers() + workers()[:1], workers("wrong")])
def test_invalid_multiworker_identity_is_rejected_before_writing(tmp_path, contexts):
    scenario = make_scenario(start=1000, run_id="distributed", node="gpu-node-0")
    with pytest.raises(ValueError, match="rollout worker"):
        generate(tmp_path / "invalid", run_id="distributed", node="gpu-node-0", scenario=scenario,
                 rollout_workers=contexts, clock=lambda: 1090)
    assert not (tmp_path / "invalid").exists()



def test_multiworker_records_share_an_explicit_synthetic_calibration(tmp_path):
    from xlayer_telemetry.time_alignment import event_window, validate_alignment
    scenario = make_scenario(start=1000, run_id="distributed", node="gpu-node-0")
    root = tmp_path / "clock"
    report = generate(root, run_id="distributed", node="gpu-node-0", scenario=scenario,
                      rollout_workers=workers(), clock=lambda: 1090)
    records = read_events(root)
    steps = [r for r in records if r["record_type"] == "verl_step_observation"]
    reference = report["analysis_window"]["time_alignment"]["reference_id"]
    assert reference.startswith("synthetic-scenes-")
    assert report["analysis_window"]["accuracy"] == "calibrated_approximate"
    assert validate_alignment(report["analysis_window"], reference, max_uncertainty=0)["status"] == "aligned"
    assert report["comparison"]["baseline_interval"]["time_alignment"]["reference_id"] == reference
    assert all(r["boundary_accuracy"] == "calibrated_approximate" and r["time_reference"] == reference
               and r["time_uncertainty_seconds"] == 0 for r in steps)
    for row in (r for r in records if r["record_type"] in {"event", "span"}):
        assert row["boundary_accuracy"] == "calibrated"
        assert row["time_reference"] == reference and row["time_uncertainty_seconds"] == 0
        assert row["time_alignment"]["node"] == row["node"]
        assert event_window(row, reference_id=reference) != (None, None)
        assert event_window(row, reference_id="other-clock") == (None, None)
    fixtures = list((root / "telemetry-clock-fixture").glob("*.json"))
    assert len(fixtures) == 3
    assert all(json.loads(p.read_text())["data_origin"] == "synthetic" for p in fixtures)


def test_default_demo_keeps_node_clock_precision_without_fake_calibration(tmp_path):
    scenario = make_scenario(start=1000, run_id="distributed", node="gpu-node-0")
    root = tmp_path / "ordinary"
    report = generate(root, run_id="distributed", node="gpu-node-0", scenario=scenario, clock=lambda: 1090)
    assert report["analysis_window"]["accuracy"] == "approximate"
    assert "time_alignment" not in report["analysis_window"]
    assert not (root / "telemetry-clock-fixture").exists()
    assert all(r.get("boundary_accuracy") == "exact" for r in read_events(root) if r["record_type"] == "span")
