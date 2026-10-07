"""Native reported scalars and explicit workload/phase evidence for the UI."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from xlayer_telemetry.adapters.verl import VerlMetricsAdapter
from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.metrics import Metric, MetricEmitter
from xlayer_telemetry.step_history import StepHistoryWriter
from xlayer_telemetry.telemetry_health import finish, observe


ROOT = Path(__file__).parents[1]


def test_framework_mfu_preserves_reported_stage_and_value():
    samples = VerlMetricsAdapter.translate({
        "perf/mfu/actor": 0.4,
        "perf/mfu/actor_infer": 0.2,
        "perf/mfu/critic": 0.0,
        "gpu_utilization_ratio": 0.99,
        "perf/mfu/unrecognized": 0.8,
    })
    mfu = [sample for sample in samples if sample.name == "training_model_flops_utilization_ratio"]
    assert {(sample.labels["reported_key"], sample.labels["phase"],
             sample.labels["verl_stage"], sample.value) for sample in mfu} == {
        ("perf/mfu/actor", "actor_update", "update_actor", 0.4),
        ("perf/mfu/actor_infer", "reference_log_prob", "old_log_prob", 0.2),
        ("perf/mfu/critic", "critic_update", "update_critic", 0.0),
    }


@pytest.mark.parametrize("value", [-0.1, 1.1, 40, True, "0.4", float("nan"), float("inf"), 10**1000])
def test_invalid_mfu_does_not_hide_valid_logger_metrics(value):
    samples = VerlMetricsAdapter.translate({"perf/mfu/actor": value, "perf/throughput": 5})
    assert [(sample.name, sample.value) for sample in samples] == [("training_tokens_per_second_per_gpu", 5)]


@pytest.mark.parametrize("value", [0, 1, 4.0])
def test_explicit_native_policy_version_is_collected_without_step_or_lag(value):
    samples = VerlMetricsAdapter.translate({
        "fully_async/count/current_param_version": value,
        "training/global_step": 99,
        "policy_version_lag": 2,
    })
    assert len(samples) == 1
    assert samples[0].name == "policy_version" and samples[0].value == value
    assert samples[0].labels == {"policy_scope": "trainer",
                                 "reported_key": "fully_async/count/current_param_version"}
    assert not VerlMetricsAdapter.translate({"training/global_step": 99, "policy_version_lag": 2})


@pytest.mark.parametrize("value", [-1, 1.5, True, "4", float("nan"), float("inf"), 2**53 + 1])
def test_invalid_policy_versions_remain_missing(value):
    assert not VerlMetricsAdapter.translate({"fully_async/count/current_param_version": value})
    assert not VerlMetricsAdapter.translate({"policy_version": value})


def test_policy_context_and_per_call_version_remain_event_fields(tmp_path):
    clock = iter((1_000_000_000, 2_000_000_000, 3_000_000_000, 4_000_000_000)).__next__
    recorder = EventRecorder(tmp_path, CorrelationContext("r", "native", "rollout", "0", "n",
                                                        policy_version=0), clock_ns=clock)
    recorder.event("weights.applied", phase="weight_sync", step=99)
    with recorder.span("rollout.generate", phase="rollout", step=100, policy_version=4):
        pass
    recorder.event("tool.call", phase="tool_interaction", policy_version=5)
    rows = [json.loads(line) for line in recorder.path.read_text().splitlines()]
    assert [row["policy_version"] for row in rows] == [0, 4, 5]
    assert all(row["policy_version_source"] == "producer_reported" for row in rows)
    assert all("policy_version" not in row["attributes"] for row in rows)


@pytest.mark.parametrize("value", [-1, True, 1.5, "4"])
def test_policy_context_rejects_invalid_explicit_versions(value):
    with pytest.raises(ValueError, match="policy_version"):
        CorrelationContext("r", "native", "rollout", "0", "n", policy_version=value)


def test_uninstrumented_policy_version_stays_unknown(tmp_path):
    recorder = EventRecorder(tmp_path, CorrelationContext("r", "native", "rollout", "0", "n"))
    recorder.event("rollout.result", phase="rollout", step=99,
                   attributes={"policy_version_lag": 3})
    assert "policy_version" not in json.loads(recorder.path.read_text())


def test_native_policy_history_preserves_source_without_manufacturing_replay_time(tmp_path):
    writer = StepHistoryWriter(tmp_path / "steps.jsonl", run_id="r", node="n", worker_id="driver",
                               execution_mode="async", clock=lambda: 100)
    record = writer.append({"step": 99, "data": {"fully_async/count/current_param_version": 4,
                                                 "timing_s/gen": 2}}, live=False)
    assert record["workload"]["fully_async/count/current_param_version"] == 4
    assert record["boundary_scope"] == "trainer_update"
    assert record["boundary_accuracy"] == "unknown" and record["window_start_ms"] is None
    invalid = writer.append({"step": 100, "data": {"policy_version": 1.5}})
    assert "policy_version" not in invalid["workload"]


def test_policy_version_cannot_become_a_prometheus_label(tmp_path):
    from xlayer_telemetry.metrics.textfile import build_metrics

    emitter = MetricEmitter(tmp_path, run_id="r", producer="native", role="rollout", worker_id="0", node="n")
    assert emitter.emit(step=99, samples=[Metric("training_loss", 1, labels={"policy_version": "4"})]) is None
    assert emitter.disabled
    snapshot = {"schema_version": 2, "run_id": "r", "producer": "native", "role": "rollout",
                "worker_id": "0", "node": "n", "step": 99, "observed_at": 100,
                "samples": [{"name": "training_loss", "value": 1, "labels": {"policy_version": "4"}}]}
    metrics = build_metrics([snapshot])
    assert all("policy_version" not in sample.labels for sample in metrics)
    assert all(sample.name != "training_loss" for sample in metrics)


def test_measured_verl_phase_covers_an_awaited_call_and_preserves_exception(tmp_path):
    from xlayer_telemetry.adapters.verl import measured_phase

    clock = iter((1_000_000_000, 3_000_000_000, 4_000_000_000, 5_000_000_000)).__next__
    recorder = EventRecorder(tmp_path, CorrelationContext("r", "verl_native", "trainer", "driver", "n"),
                             clock_ns=clock)

    async def actual_call():
        await asyncio.sleep(0)
        return 7

    async def instrumented_call():
        with measured_phase(recorder, "gen", step=9, policy_version=4) as identity:
            assert identity.span_id
            return await actual_call()

    assert asyncio.run(instrumented_call()) == 7
    with pytest.raises(RuntimeError, match="workload failed"):
        with measured_phase(recorder, "update_actor", step=9):
            raise RuntimeError("workload failed")
    rows = [json.loads(line) for line in recorder.path.read_text().splitlines()]
    assert rows[0]["phase"] == "rollout" and rows[0]["duration_seconds"] == 2
    assert rows[0]["start_time_unix_nano"] == 1_000_000_000
    assert rows[0]["end_time_unix_nano"] == 3_000_000_000
    assert rows[0]["boundary_accuracy"] == "exact" and rows[0]["policy_version"] == 4
    assert rows[0]["attributes"]["verl_stage"] == "gen"
    assert rows[0]["attributes"]["measurement_source"] == "native_sdk"
    assert rows[1]["status"] == "error" and rows[1]["phase"] == "actor_update"
    with measured_phase(None, "gen") as identity:
        assert identity is None


@pytest.mark.parametrize("aligned", [True, False])
def test_measured_phase_reuses_calibration_and_keeps_raw_boundaries(tmp_path, aligned):
    from xlayer_telemetry.adapters.verl import measured_phase
    from xlayer_telemetry.time_alignment import CalibrationCache

    path = tmp_path / "clock.json"
    path.write_text(json.dumps({
        "schema_version": 1, "method": "four_timestamp", "node": "n", "boot_id": "boot",
        "reference_id": "monitor", "offset_seconds": -12, "round_trip_seconds": 0.04,
        "uncertainty_seconds": 0.02, "local_anchor": 112, "monotonic_anchor": 50,
        "valid_from": 111.96, "valid_until": 172 if aligned else 115, "drift_ppm": 100,
    }))
    mapping = CalibrationCache(path, node="n", wall_clock=lambda: 122, monotonic=lambda: 60,
                               boot_id=lambda: "boot")
    clock = iter((120_000_000_000, 122_000_000_000)).__next__
    recorder = EventRecorder(tmp_path / "events", CorrelationContext("r", "verl_native", "trainer", "driver", "n"),
                             clock_ns=clock, time_calibration=mapping)
    with measured_phase(recorder, "gen", trace_id="trace", parent_span_id="parent"):
        pass
    record = json.loads(recorder.path.read_text())
    assert record["start_time_unix_nano"] == 120_000_000_000
    assert record["end_time_unix_nano"] == 122_000_000_000
    assert record["duration_seconds"] == 2 and record["parent_span_id"] == "parent"
    if aligned:
        assert record["boundary_accuracy"] == "calibrated"
        assert record["correlation_start_time_unix_nano"] == 108_000_000_000
        assert record["time_reference"] == "monitor" and record["time_uncertainty_seconds"] > 0
    else:
        assert record["boundary_accuracy"] == "unknown" and record["start_time_ms"] is None
        assert "correlation_start_time_unix_nano" not in record


def write_health(root, *, run="r", node="n", stamp=100, status="running", exit_code=None):
    metrics = root / "telemetry-metrics"
    metrics.mkdir(parents=True, exist_ok=True)
    health = {"schema_version": 1, "record_type": "telemetry_health", "status": "partial",
              "workload": {"status": status, "exit_code": exit_code},
              "workload_observation": {"run_id": run, "node": node, "observed_at": stamp,
                                       "source": "wrapper_health", "boundary_scope": "wrapped_command",
                                       "clock_scope": "node"}}
    (root / "telemetry-health.json").write_text(json.dumps(health))
    return metrics


@pytest.mark.parametrize("status,exit_code,expected", [
    ("running", None, "running"), ("finished", 0, "succeeded"),
    ("finished", 7, "failed"), ("finished", 143, "failed"),
])
def test_workload_metrics_use_explicit_exit_and_not_telemetry_health(tmp_path, status, exit_code, expected):
    from xlayer_telemetry.metrics.workload import collect_workload_metrics

    path = write_health(tmp_path / "a", status=status, exit_code=exit_code)
    samples = collect_workload_metrics([path], [tmp_path], now=110, max_age_seconds=30, node="n")
    states = [sample for sample in samples if sample.name == "telemetry_wrapped_workload_state"]
    assert len(states) == 3
    assert {sample.labels["state"]: sample.value for sample in states} == {
        state: int(state == expected) for state in ("running", "succeeded", "failed")}
    assert all(sample.labels["run_id"] == "r" and sample.labels["node"] == "n"
               and sample.labels["source"] == "wrapper_health"
               and sample.labels["boundary_scope"] == "wrapped_command" for sample in samples)
    assert next(sample.value for sample in samples
                if sample.name == "telemetry_wrapped_workload_observed_timestamp_seconds") == 100
    codes = [sample.value for sample in samples if sample.name == "telemetry_wrapped_workload_exit_code"]
    assert codes == ([] if exit_code is None else [exit_code])


@pytest.mark.parametrize("changes", [
    {"workload": {"status": "finished", "exit_code": None}},
    {"workload": {"status": "finished", "exit_code": True}},
    {"workload": {"status": "running", "exit_code": 7}},
    {"workload": {"status": "unknown", "exit_code": None}},
    {"workload_observation": {}}, {"workload_observation": {"run_id": "invented"}},
])
def test_unknown_workload_state_never_becomes_zero_or_success(tmp_path, changes):
    from xlayer_telemetry.metrics.workload import collect_workload_metrics

    path = write_health(tmp_path)
    destination = tmp_path / "telemetry-health.json"
    destination.write_text(json.dumps(json.loads(destination.read_text()) | changes))
    assert not collect_workload_metrics([path], [], now=110)


def test_workload_metrics_obey_node_freshness_and_bounded_reads(tmp_path):
    from xlayer_telemetry.metrics.workload import collect_workload_metrics

    path = write_health(tmp_path / "a", stamp=100)
    assert not collect_workload_metrics([path], [], now=140, max_age_seconds=30)
    assert not collect_workload_metrics([path], [], now=99)
    assert not collect_workload_metrics([path], [], now=110, node="other")
    counts = {}
    assert not collect_workload_metrics([path], [], now=110, max_record_bytes=20, counters=counts)
    assert counts["workload_rejections"] == 1
    write_health(tmp_path / "b", run="b")
    samples = collect_workload_metrics([], [tmp_path], now=110, max_runs=1, counters=counts)
    assert len({sample.labels["run_id"] for sample in samples}) == 1
    assert counts["workload_limit_drops"] == 1


def test_health_artifact_provides_explicit_scope_and_observation_time(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEMETRY_RUN_ID", "r")
    monkeypatch.setenv("TELEMETRY_NODE", "n")
    initial = observe(tmp_path, {"bridge": os.getpid()}, now=100)
    assert initial["workload_observation"] == {
        "run_id": "r", "node": "n", "source": "wrapper_health",
        "boundary_scope": "wrapped_command", "clock_scope": "node", "observed_at": 100}
    final = finish(tmp_path, 7, "failed", "disabled")
    assert final["workload"] == {"status": "finished", "exit_code": 7}
    assert final["workload_observation"]["observed_at"] == final["observed_at"]


def test_prelaunch_health_does_not_claim_a_running_command(tmp_path, monkeypatch):
    from xlayer_telemetry.metrics.workload import collect_workload_metrics

    monkeypatch.setenv("TELEMETRY_RUN_ID", "r")
    monkeypatch.setenv("TELEMETRY_NODE", "n")
    metrics = tmp_path / "telemetry-metrics"
    metrics.mkdir()
    initial = observe(tmp_path, {"bridge": os.getpid()}, now=100, workload_started=False)
    assert initial["workload"]["status"] == "starting"
    assert not collect_workload_metrics([metrics], [], now=110)


def test_sdk_and_native_phase_helper_do_not_import_collectors_or_profilers():
    result = subprocess.run([sys.executable, "-c",
        "import sys; from xlayer_telemetry.events import EventRecorder; "
        "from xlayer_telemetry.adapters.verl import measured_phase; "
        "assert not any(m.startswith(('xlayer_telemetry.collectors', 'xlayer_telemetry.analysis', "
        "'torch', 'transformers', 'ray', 'verl', 'vllm')) for m in sys.modules)"],
        cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("code,expected", [(0, "succeeded"), (7, "failed")])
def test_real_wrapper_to_application_textfile_keeps_terminal_workload_status(tmp_path, code, expected):
    output = tmp_path / "run"
    wrapped = subprocess.run([
        "bash", str(ROOT / "scripts/run_verl_with_telemetry.sh"), "--output", str(output),
        "--run-id", "explicit-result", "--node", "n", "--", "bash", "-c", f"exit {code}"],
        env=os.environ | {"TELEMETRY_PYTHON": sys.executable}, capture_output=True, text=True, timeout=20)
    assert wrapped.returncode == code
    published = tmp_path / "textfile"
    result = subprocess.run([
        sys.executable, "-m", "xlayer_telemetry.metrics.textfile", "--runs-root", str(tmp_path),
        "--node", "n", "--textfile-dir", str(published), "--once"],
        cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    text = (published / "application.prom").read_text()
    assert any(f'state="{expected}"' in line and line.endswith(" 1")
               for line in text.splitlines() if line.startswith("telemetry_wrapped_workload_state{"))
    assert 'source="wrapper_health"' in text
    assert "training_step{" not in text
