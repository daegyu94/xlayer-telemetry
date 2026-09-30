"""Boundary and concurrency failures reproduced during the repository audit."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from threading import Barrier

import pytest

from xlayer_telemetry import fileio
from xlayer_telemetry.analysis.diagnosis_analysis import select_baseline
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, load_history
from xlayer_telemetry.metrics import Metric, MetricEmitter
from xlayer_telemetry.metrics.prometheus import GaugeSample, format_gauges, write_gauges
from xlayer_telemetry.metrics.textfile import _iter_snapshots, build_metrics
from xlayer_telemetry.show_run import summarize
from xlayer_telemetry.step_backfill import backfill
from xlayer_telemetry.step_history import StepHistoryWriter
from xlayer_telemetry.collectors.topology_textfile import build_gauges
from xlayer_telemetry.collectors.sandbox_sampler import read_cgroup


def step(record_id="one", observed=20, duration=5):
    return {"schema_version": 1, "record_type": "verl_step_observation",
            "record_id": record_id, "run_id": "r", "node": "n", "worker_id": "driver",
            "step": 1, "boundary_scope": "rl_step", "observed_at": observed,
            "step_duration_seconds": duration,
            "analysis_window": {"start": observed-duration, "end": observed, "accuracy": "approximate"}}


def test_history_consumers_skip_bad_lines_and_keep_valid_steps(tmp_path):
    events = tmp_path / "telemetry-events"
    events.mkdir()
    path = events / "verl-steps.jsonl"
    path.write_bytes(b'null\n[]\n42\n{"broken":\xff}\n' + (json.dumps(step()) + "\n").encode())
    assert load_history(path) == [step()]
    # A bridge restart must not fail while rebuilding its deduplication set.
    StepHistoryWriter(path, run_id="r", node="n", worker_id="driver")
    assert backfill(tmp_path) == 1


def test_bad_snapshot_encoding_and_numeric_overflow_are_isolated(tmp_path):
    snapshot = {"schema_version": 2, "run_id": "r", "producer": "app", "role": "trainer",
                "worker_id": "0", "node": "n", "observed_at": 1,
                "samples": [{"name": "training_loss", "value": 1.25}]}
    (tmp_path / "valid.json").write_text(json.dumps(snapshot))
    (tmp_path / "broken.json").write_bytes(b"\xff")
    broken = snapshot | {"worker_id": "bad", "observed_at": 10**400,
                         "samples": [{"name": "invalid_value", "value": 10**400},
                                     {"name": "negative_total", "kind": "counter", "value": -1}]}
    (tmp_path / "overflow.json").write_text(json.dumps(broken))
    text = format_gauges(build_metrics(_iter_snapshots(tmp_path)))
    assert "training_loss{" in text and 'worker_id="0"' in text
    assert "invalid_value" not in text and "negative_total" not in text


@pytest.mark.parametrize("value", [10**400, "invalid", None])
def test_invalid_sdk_measurement_does_not_interrupt_application(tmp_path, value):
    emitter = MetricEmitter(tmp_path, run_id="r", producer="app", role="trainer", worker_id="0")
    assert emitter.emit(step=1, samples=[Metric("loss", value)]) is None
    assert emitter.disabled


@pytest.mark.parametrize("writer", ["emitter", "textfile"])
def test_threads_can_replace_same_snapshot_without_temp_file_collision(tmp_path, monkeypatch, writer):
    rendezvous = Barrier(2)
    replace = os.replace
    def synchronized_replace(source, destination):
        rendezvous.wait(timeout=5)
        return replace(source, destination)
    monkeypatch.setattr(fileio.os, "replace", synchronized_replace)
    emitter = MetricEmitter(tmp_path, run_id="r", producer="app", role="trainer", worker_id="0")
    def write(value):
        if writer == "emitter":
            return emitter.emit(step=value, samples=[Metric("loss", value)])
        return write_gauges(tmp_path, "worker.prom", [GaugeSample("loss", "Loss.", value)])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, (1, 2)))
    assert all(path is not None for path in results)
    assert results[0] == results[1]
    content = results[0].read_text()
    if writer == "emitter":
        assert json.loads(content)["step"] in (1, 2)
        assert not emitter.disabled
    else:
        assert content.endswith((" 1\n", " 2\n"))
    assert not list(tmp_path.glob(".*.tmp"))


def test_long_basename_and_failed_snapshot_leave_no_temporary_files(tmp_path):
    destination = write_gauges(tmp_path, "x"*245 + ".prom", [GaugeSample("loss", "Loss.", 1)])
    assert destination.is_file()
    with pytest.raises(RuntimeError):
        with fileio.atomic_text_writer(destination) as stream:
            stream.write("partial")
            raise RuntimeError("producer failed")
    assert destination.read_text().endswith(" 1\n")
    assert not list(tmp_path.glob(".*.tmp"))


def test_malformed_topology_does_not_hide_other_components(tmp_path):
    (tmp_path / "compute-topology.json").write_bytes(b"\xff")
    (tmp_path / "storage-topology.json").write_text(json.dumps({
        "components": [None, {}, {"id": "ssd-0"}, {"id": "ssd-0"}],
        "edges": [None, {"source": "sandbox-0", "destination": "ssd-0"}],
    }))
    samples = build_gauges(tmp_path)
    assert len(samples) == 2
    assert {sample.labels.get("component") for sample in samples} == {"ssd-0", None}
    format_gauges(samples)


def test_run_summary_skips_non_object_metadata(tmp_path):
    (tmp_path / "telemetry-manifest.json").write_text("[]")
    (tmp_path / "summary-invalid.json").write_bytes(b"\xff")
    metrics = tmp_path / "telemetry-metrics"
    metrics.mkdir()
    (metrics / "bad.json").write_text('{"schema_version":2,"samples":null}')
    assert summarize(tmp_path).startswith("Run:")


def test_cgroup_io_error_does_not_hide_other_controller_data(tmp_path, monkeypatch):
    (tmp_path / "memory.current").write_text("1024")
    read = Path.read_text
    def fail_one(path, *args, **kwargs):
        if path.name == "io.stat":
            raise OSError("cgroup controller unavailable")
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", fail_one)
    values = read_cgroup(tmp_path)
    assert values == {"memory_current": 1024}


@pytest.mark.parametrize("payload", [[], {"status": "success", "data": {"result": None}},
                                     {"status": "success", "data": {"result": [{"metric": None}]}}])
def test_malformed_backend_response_is_missing_evidence_not_engine_crash(monkeypatch, payload):
    monkeypatch.setattr("xlayer_telemetry.prometheus._read_json", lambda *_: payload)
    engine = DiagnosticEngine({"prometheus": {"url": "http://unused"}}, clock=lambda: 30)
    report = engine.analyze(step(), [])
    assert report["verdict"] == "insufficient_data"
    assert not report["candidates"]
    assert any(source.endswith(":RuntimeError") for source in report["missing_sources"])


def test_baseline_is_chronological_and_rejects_invalid_intervals():
    history = [step(str(i), observed=i+10, duration=i) for i in range(8, 0, -1)]
    invalid = step("invalid", observed=19, duration=1)
    invalid["analysis_window"] = {"start": 20, "end": 19}
    history.append(invalid)
    assert select_baseline(step(observed=20), history)["record_id"] == "6"
    assert select_baseline(step(observed=0), history) is None
    assert select_baseline(step() | {"observed_at": None}, []) is None


def test_phase_aliases_do_not_double_count_duration():
    record = {"stage_durations_seconds": {"gen": 5, "rollout": 7, "update_weights": 2, "weight_sync": 3}}
    signals = DiagnosticEngine._signals(record, {}, [])
    assert signals["rollout_duration_seconds"] == 7
    assert signals["communication_duration_seconds"] == 3


@pytest.mark.parametrize("kv_engine,expected", [("a", "supporting_signal"), ("b", None)])
@pytest.mark.parametrize("label", ["engine", "engine_id", "component"])
def test_missing_preemption_source_does_not_mix_vllm_engines(kv_engine, expected, label):
    class Prometheus:
        def query_range_detail(self, query, *args):
            if "num_requests_waiting" in query:
                engine, value = "a", 5
            elif "kv_cache_usage" in query:
                engine, value = kv_engine, .99
            else:
                return {"aggregate": None, "series": []}
            stats = {"max": value, "min": value, "mean": value, "max_series_delta": None}
            return {"aggregate": stats, "series": [{"labels": {"node": "n", label: engine}, "stats": stats}]}
    engine = DiagnosticEngine({"prometheus": {"url": "http://unused"}}, prometheus=Prometheus(), clock=lambda: 30)
    report = engine.analyze(step(), [])
    candidates = [item for item in report["candidates"] if item["id"] == "kv_cache_pressure"]
    if expected is None:
        assert not candidates
        assert "vllm:shared_engine_identity" in report["missing_sources"]
    else:
        assert candidates[0]["state"] == expected
        assert "vllm_preemptions_delta" in candidates[0]["missing_evidence"]
        assert all(item["labels"][label] == "a" for item in candidates[0]["evidence"])
