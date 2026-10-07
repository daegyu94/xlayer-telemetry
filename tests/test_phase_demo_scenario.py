"""Synthetic phase replay must preserve clocks, identity and metric contracts."""
import json
from pathlib import Path

import pytest

from xlayer_telemetry.demos.scenario import make_scenario, frame_at, load_scenario
from xlayer_telemetry.demos.live import Demo
from xlayer_telemetry.demos.diagnosis import generate
from xlayer_telemetry.fileio import atomic_write_text
from xlayer_telemetry.metrics.prometheus import format_gauges

ROOT = Path(__file__).resolve().parents[1]


def scenario_file(tmp_path, *, current="storage-regression"):
    schedule = make_scenario(start=1000, run_id="phase-demo", node="gpu-node-0", step=127, current=current)
    path = tmp_path / "scenario.json"
    atomic_write_text(path, json.dumps(schedule))
    return path, schedule


def test_schedule_has_comparable_explicit_phases_without_fake_ownership(tmp_path):
    path, schedule = scenario_file(tmp_path)
    assert load_scenario(path) == schedule
    baseline, current = schedule["frames"]
    assert (baseline["step"], current["step"]) == (127, 128)
    assert baseline["end"] - baseline["start"] == 40
    assert current["end"] - current["start"] == 48
    assert schedule["data_origin"] == "synthetic"
    assert {p["phase"] for p in current["phases"]} == {"rollout", "reward", "actor_update", "weight_sync", "checkpoint_save"}
    assert all(p["end"] - p["start"] >= 6 for p in current["phases"])
    assert frame_at(schedule, 999) is None
    assert frame_at(schedule, 1010)[0]["step"] == 127
    assert frame_at(schedule, 1050)[0]["step"] == 128
    assert frame_at(schedule, 1089) is None


def test_live_phase_samples_follow_the_schedule_and_keep_bounded_identity(tmp_path, monkeypatch):
    path, schedule = scenario_file(tmp_path)
    demo = Demo(ROOT / "examples/live-demo", scenario_state=path)
    import xlayer_telemetry.demos.live as live
    monkeypatch.setattr(live.time, "time", lambda: 1010)
    baseline = demo.metrics("gpu-node-0")
    monkeypatch.setattr(live.time, "time", lambda: 1050)
    current = demo.metrics("gpu-node-0")
    assert format_gauges(baseline) and format_gauges(current)
    gpu = lambda rows: next(s.value for s in rows if s.name == "telemetry_gpu_utilization_percent" and s.labels.get("gpu") == "0")
    assert gpu(current) < gpu(baseline)
    app = [s for s in current if s.labels.get("run_id") == "phase-demo"]
    assert {s.value for s in app if s.name == "training_step"} == {127}
    assert any(s.name == "policy_version" and s.value == 128 for s in app)
    assert not any(s.labels.get("run_id") == "verl-agent-demo" for s in current)
    assert not any(set(s.labels) & {"trace_id", "span_id", "trajectory_id", "request_id", "step"} for s in current)
    monkeypatch.setattr(live.time, "time", lambda: 1010)
    normal_wait = next(s.value for s in demo.metrics("vllm") if s.name == "vllm:num_requests_waiting")
    monkeypatch.setattr(live.time, "time", lambda: 1050)
    slow_wait = next(s.value for s in demo.metrics("vllm") if s.name == "vllm:num_requests_waiting")
    assert slow_wait > normal_wait
    sandbox = next(s for s in app if s.name == "sandbox_io_pressure_ratio")
    assert sandbox.labels["worker_id"] == "pool-0"


def test_completed_report_and_native_spans_use_the_same_schedule(tmp_path):
    _, schedule = scenario_file(tmp_path)
    report = generate(tmp_path / "completed", run_id="phase-demo", node="gpu-node-0", scenario=schedule, clock=lambda: 1090)
    baseline, current = schedule["frames"]
    assert report["step"] == current["step"]
    assert any(c["id"] == "storage_queue_saturation" and c["state"] == "strong_signal" for c in report["candidates"])
    assert report["analysis_window"]["start"] == current["start"]
    assert report["analysis_window"]["end"] == current["end"]
    assert report["comparison"]["baseline_interval"]["start"] == baseline["start"]
    assert report["comparison"]["workload"] == schedule["workload"]
    records = [json.loads(line) for path in (tmp_path / "completed/telemetry-events").glob("*.jsonl") for line in path.read_text().splitlines()]
    spans = [r for r in records if r.get("record_type") == "span"]
    assert {r["step"] for r in spans} == {127, 128}
    assert all(r["attributes"]["data_origin"] == "synthetic" and r["boundary_accuracy"] == "exact" for r in spans)
    phases = [r for r in spans if r["attributes"].get("scenario_phase")]
    assert len(phases) == 10
    assert len({r["attributes"]["workload_fingerprint"] for r in phases}) == 1
    assert all(r["attributes"]["boundary_scope"] == "rl_step" for r in phases)
    for frame in schedule["frames"]:
        observed = [r for r in phases if r["step"] == frame["step"]]
        assert {r["phase"] for r in observed} == {p["phase"] for p in frame["phases"]}
        for row in observed:
            planned = next(p for p in frame["phases"] if p["phase"] == row["phase"])
            assert row["start_time_unix_nano"] == int(planned["start"] * 1e9)
            assert row["end_time_unix_nano"] == int(planned["end"] * 1e9)
    sandbox = next(r for r in spans if r["name"] == "sandbox.exec")
    assert sandbox["worker_id"] == "pool-0" and sandbox["run_id"] == "phase-demo"


def test_normal_pair_has_no_fabricated_storage_regression(tmp_path):
    _, schedule = scenario_file(tmp_path, current="normal")
    report = generate(tmp_path / "normal", run_id="phase-demo", node="gpu-node-0", scenario=schedule, clock=lambda: 1090)
    assert not any(c["state"] == "strong_signal" for c in report["candidates"])
    assert report["verdict"] == "no_anomaly_observed"
    import jsonschema
    jsonschema.validate(report, json.loads((ROOT / "config/diagnosis.schema.json").read_text()))
    values = {s["signal"]: s for s in report["comparison"]["signals"]}
    assert values["step_duration_seconds"]["delta_percent"] == 0


def test_scenario_rejects_future_or_mismatched_completed_record(tmp_path):
    _, schedule = scenario_file(tmp_path)
    with pytest.raises(ValueError, match="completed"):
        generate(tmp_path / "future", run_id="phase-demo", node="gpu-node-0", scenario=schedule, clock=lambda: 1050)
    with pytest.raises(ValueError, match="identity"):
        generate(tmp_path / "wrong", run_id="wrong", node="gpu-node-0", scenario=schedule, clock=lambda: 1090)
    assert not (tmp_path / "future").exists()
    assert not (tmp_path / "wrong").exists()


def test_invalid_scenario_state_is_bounded_and_rejected(tmp_path):
    path, schedule = scenario_file(tmp_path)
    schedule["frames"][1]["phases"][0]["end"] = float("nan")
    path.write_text(json.dumps(schedule))
    with pytest.raises(ValueError, match="scenario"):
        load_scenario(path)
    path.write_text(" " * 65537)
    with pytest.raises(ValueError, match="size"):
        load_scenario(path)
