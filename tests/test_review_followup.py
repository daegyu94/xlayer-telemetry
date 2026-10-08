"""Reproduce review failures at process, persistence and evidence boundaries."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from xlayer_telemetry.adapters.verl import FileRecord, VerlMetricsAdapter, bridge_records
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, write_report
from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.fileio import append_jsonl, json_objects
from xlayer_telemetry.metrics import MetricEmitter
from xlayer_telemetry.metrics.textfile import collect_snapshots
from xlayer_telemetry.step_history import StepHistoryWriter
from xlayer_telemetry.time_alignment import CalibrationCache


ROOT = Path(__file__).parents[1]
WRAPPER = ROOT / "scripts/run_verl_with_telemetry.sh"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
@pytest.mark.parametrize("operation", ["project", "metric", "event"])
def test_inherited_calibration_lock_does_not_block_child(tmp_path, operation):
    cache = CalibrationCache(tmp_path / "missing-clock.json", node="n")
    emitter = MetricEmitter(tmp_path, run_id="r", producer="verl", role="trainer",
                            worker_id="w", node="n", time_calibration=cache)
    recorder = EventRecorder(tmp_path, CorrelationContext("r", "agent", "rollout", "w", "n"),
                             time_calibration=cache)
    cache._lock.acquire()  # Simulate another parent thread during fork.
    child = os.fork()
    if child == 0:
        signal.alarm(2)
        try:
            if operation == "project":
                cache.project(1, 2)
            elif operation == "metric":
                VerlMetricsAdapter(emitter).emit({"timing_s/step": 1}, step=1)
            else:
                recorder.event("tool.call", phase="environment")
        except BaseException:
            os._exit(1)
        os._exit(0)
    try:
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        cache._lock.release()


def test_wrapper_does_not_log_arbitrary_workload_arguments(tmp_path):
    secret = "fake-api-key-not-for-logs"
    result = subprocess.run(["bash", str(WRAPPER), "--output", str(tmp_path / "run"),
                             "--", sys.executable, "-c", "import sys; sys.exit(7)",
                             "--api-key", secret], env=os.environ | {"TELEMETRY_PYTHON": sys.executable},
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 7
    assert secret not in result.stdout + result.stderr


@pytest.mark.parametrize("exit_code", [0, 7])
def test_normal_workload_exit_cleans_owned_descendants(tmp_path, exit_code):
    child_file = tmp_path / "child.pid"
    launcher = tmp_path / "launcher.py"
    launcher.write_text("import os, subprocess, sys\n"
                        "child = subprocess.Popen([sys.executable, '-c', "
                        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'])\n"
                        "open(os.environ['CHILD_PID_FILE'], 'w').write(str(child.pid))\n"
                        f"sys.exit({exit_code})\n")
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        result = subprocess.run(["bash", str(WRAPPER), "--output", str(tmp_path / "run"),
                                 "--", sys.executable, str(launcher)],
                                env=os.environ | {"TELEMETRY_PYTHON": sys.executable,
                                                  "CHILD_PID_FILE": str(child_file)},
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == exit_code, result.stderr
        state = Path(f"/proc/{child_file.read_text()}/stat")
        assert not state.exists() or state.read_text().rsplit(") ", 1)[1].split()[0] == "Z"
        assert unrelated.poll() is None
    finally:
        if child_file.exists():
            try:
                os.kill(int(child_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
        unrelated.terminate()
        unrelated.wait(timeout=5)


@pytest.mark.parametrize("producer", ["event", "history", "report"])
@pytest.mark.parametrize("tail", [b'{"broken":', b'{"retained":1}'])
def test_restart_preserves_first_jsonl_record_after_unterminated_tail(tmp_path, producer, tail):
    if producer == "event":
        writer = EventRecorder(tmp_path, CorrelationContext("r", "agent", "rollout", "w", "n"))
        path = writer.path
        append = lambda: writer.event("tool.call", phase="environment")
    elif producer == "history":
        path = tmp_path / "steps.jsonl"
        writer = StepHistoryWriter(path, run_id="r", node="n", worker_id="w")
        append = lambda: writer.append({"step": 1, "data": {"timing_s/step": 1}})
    else:
        path = tmp_path / "diagnostics.jsonl"
        append = lambda: write_report(tmp_path, {"record_type": "diagnosis", "analysis_id": "new"})
    path.write_bytes(tail)
    append()
    rows = list(json_objects(path))
    assert len(rows) == (2 if b"retained" in tail else 1)
    assert rows[-1].get("record_type") in {"event", "verl_step_observation", "diagnosis"}


def test_unknown_replay_is_not_a_fresh_application_snapshot(tmp_path):
    emitter = MetricEmitter(tmp_path / "metrics", run_id="r", producer="verl", role="trainer",
                            worker_id="w", node="n", clock=lambda: 100)
    bridge_records([FileRecord({"step": 1, "data": {"timing_s/step": 2}}, live=False)],
                   VerlMetricsAdapter(emitter))
    snapshot = json.loads(next((tmp_path / "metrics").glob("*.json")).read_text())
    assert snapshot["observed_at"] is None
    assert snapshot["ingested_at"] == 100
    assert collect_snapshots([tmp_path / "metrics"], [], now=100) == []


@pytest.mark.parametrize("previous_labels", [
    {"cluster": "c", "instance": "n", "device": "mlx5_1", "port": "1"},
    {"cluster": "c", "instance": "other", "device": "mlx5_0", "port": "1"},
    {"cluster": "c", "instance": "n", "device": "mlx5_0", "port": "2"},
    {"cluster": "c", "instance": "n", "device": "mlx5_0", "port": "1"},
    {},
])
def test_rdma_baseline_requires_same_endpoint_and_device(previous_labels):
    class Backend:
        def query_range_detail(self, query, start, end, step):
            if "gpu_utilization_percent" in query:
                value = 20 if end == 100 else 80
                stats = {"min": value, "mean": value, "max": value}
                return {"aggregate": stats, "series": [{"labels": {"gpu": "0", "node": "n"}, "stats": stats}]}
            if "infiniband_port_data" not in query:
                return {"aggregate": None, "series": []}
            value = 200 if end == 100 else 10
            labels = {"cluster": "c", "instance": "n", "device": "mlx5_0", "port": "1"} if end == 100 else previous_labels
            stats = {"min": value, "mean": value, "max": value}
            return {"aggregate": stats, "series": [{"labels": labels, "stats": stats}]}
    record = {"run_id": "r", "node": "n", "worker_id": "w", "record_id": "current",
              "step": 2, "observed_at": 100, "step_duration_seconds": 10,
              "stage_durations_seconds": {"update_weights": 7},
              "analysis_window": {"start": 90, "end": 100, "accuracy": "exact"}}
    prior = {**record, "record_id": "old", "observed_at": 80,
             "stage_durations_seconds": {"update_weights": 1},
             "analysis_window": {"start": 70, "end": 80, "accuracy": "exact"}}
    report = DiagnosticEngine({"cluster": "c", "clock": {"enabled": False},
                               "prometheus": {"url": "http://unused"}},
                              prometheus=Backend(), clock=lambda: 101).analyze(record, [prior])
    row = next(row for row in report["comparison"]["signals"] if row["signal"] == "rdma_bytes_per_second")
    matched = previous_labels == {"cluster": "c", "instance": "n", "device": "mlx5_0", "port": "1"}
    assert row["baseline"] == (10 if matched else None)
    assert row["labels"]["device"] == "mlx5_0"
    assert ("prometheus:rdma_bytes_per_second:baseline_entity_match" in report["missing_sources"]) is not matched
    candidate = next(item for item in report["candidates"] if item["id"] == "communication_bound")
    assert candidate["state"] == "supporting_signal"
    assert any('source_freshness_unknown' in value for value in candidate['missing_evidence'])


def test_jsonl_concurrent_recovery_keeps_all_complete_records(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"broken":')
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda value: append_jsonl(path, json.dumps({"value": value})), range(100)))
    assert sorted(row["value"] for row in json_objects(path)) == list(range(100))


def test_bridge_cli_reports_sdk_write_failure(tmp_path, monkeypatch):
    from xlayer_telemetry.adapters import verl
    path = tmp_path / "logger.jsonl"
    path.write_text('{"step":1,"data":{"timing_s/step":1}}\n')
    monkeypatch.setattr(sys, "argv", ["verl", "--input", str(path), "--metrics-dir", str(tmp_path / "out"), "--run-id", "r"])
    def fail(_item):
        raise OSError("injected disk full")
    monkeypatch.setattr(MetricEmitter, "_persist", staticmethod(fail))
    with pytest.raises(SystemExit) as failure:
        verl.main()
    assert failure.value.code == 1


def test_selected_step_links_clear_previous_trace():
    for name in ("run-overview", "bottleneck-summary"):
        dashboard = json.loads((ROOT / "examples/dashboards" / (name + ".json")).read_text())
        def visit(value):
            if isinstance(value, dict):
                url = value.get("url", "")
                if 'var-record_id=${__data.fields[' in url and "from=" in url:
                    assert "var-trace_id=.*" in url
                    assert "${trace_id:queryparam}" not in url
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(dashboard)


def test_timeline_gpu_excludes_stale_and_future_samples():
    dashboard = json.loads((ROOT / "examples/dashboards/cross-layer-timeline.json").read_text())
    panel = next(panel for panel in dashboard["panels"] if panel["id"] == 4)
    query = panel["targets"][0]["expr"]
    assert "telemetry_gpu_sample_timestamp_seconds" in query
    assert "< 30" in query and ">= 0" in query


def test_cache_benchmark_reports_bounded_fallback(tmp_path, monkeypatch):
    from examples.investigation import validate_runtime as benchmark
    from xlayer_telemetry.analysis.jsonl_cache import JSONLCache
    path = tmp_path / "events.jsonl"
    path.write_text('{"value":1}\n{"value":2}\n')
    monkeypatch.setattr(benchmark, "JSONLCache", lambda: JSONLCache(max_records=1))
    result = benchmark.measure(lambda cache: list(cache.read(path)) if cache else list(json_objects(path)), path, 2)
    assert result["warm_parsed_bytes"] == path.stat().st_size
    assert result["warm_parse_reused"] is False


def test_timeline_gpu_query_filters_by_node_and_sample_time(tmp_path):
    import shutil
    tool = os.environ.get("PROMTOOL") or shutil.which("promtool")
    if not tool:
        pytest.skip("set PROMTOOL to evaluate dashboard PromQL")
    dashboard = json.loads((ROOT / "examples/dashboards/cross-layer-timeline.json").read_text())
    query = next(panel for panel in dashboard["panels"] if panel["id"] == 4)["targets"][0]["expr"]
    query = query.replace("$cluster", "lab").replace("$node", ".*").replace("$gpu", ".*")
    fixture = ["evaluation_interval: 1m", "tests:", "  - interval: 1m", "    input_series:"]
    for node, stamp in (("fresh", 55), ("stale", 10), ("future", 80)):
        labels = f'job="telemetry",cluster="lab",instance="{node}:19100",nodename="{node}"'
        fixture.extend([f"      - series: 'telemetry_gpu_utilization_percent{{{labels},gpu=\"0\"}}'",
                        "        values: '42+0x1'",
                        f"      - series: 'telemetry_gpu_sample_timestamp_seconds{{{labels}}}'",
                        f"        values: '{stamp}+0x1'"])
    fixture.extend(["    promql_expr_test:", f"      - expr: '{query}'", "        eval_time: 1m",
                    "        exp_samples:",
                    '          - labels: \'telemetry_gpu_utilization_percent{job="telemetry",cluster="lab",instance="fresh:19100",nodename="fresh",gpu="0"}\'',
                    "            value: 42"])
    path = tmp_path / "gpu-freshness.yaml"
    path.write_text("\n".join(fixture) + "\n")
    result = subprocess.run([tool, "test", "rules", str(path)], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("existing_snapshot", [False, True])
def test_wrapper_retains_final_bridge_failure_and_workload_status(tmp_path, existing_snapshot):
    proxy = tmp_path / "telemetry-python"
    proxy.write_text("#!" + sys.executable + "\n" +
        "import os,runpy,sys\n"
        "if sys.argv[1:3] == ['-m', 'xlayer_telemetry.adapters.verl'] and '--follow' not in sys.argv:\n"
        " from xlayer_telemetry.metrics import MetricEmitter\n"
        " def fail(item): raise OSError('injected final write failure')\n"
        " MetricEmitter._persist = staticmethod(fail)\n"
        " sys.argv = [sys.argv[2]] + sys.argv[3:]\n"
        " runpy.run_module('xlayer_telemetry.adapters.verl', run_name='__main__')\n"
        "else: os.execv(sys.executable, [sys.executable] + sys.argv[1:])\n")
    proxy.chmod(0o755)
    command = "import os,pathlib; pathlib.Path(os.environ['VERL_FILE_LOGGER_PATH']).write_text('{\"step\":1,\"data\":{\"timing_s/step\":1}}\\n')"
    if existing_snapshot:
        command += "; pathlib.Path(os.environ['TELEMETRY_METRICS_DIR'], 'verl-trainer-driver.json').write_text('{}')"
    root = tmp_path / "run"
    result = subprocess.run(["bash", str(WRAPPER), "--output", str(root), "--", sys.executable, "-c", command],
        env=os.environ | {"TELEMETRY_PYTHON": str(proxy)}, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    health = json.loads((root / "telemetry-health.json").read_text())
    assert health["workload"]["exit_code"] == 0
    assert health["final_export"]["bridge"] == "failed"
    assert health["status"] == "partial"
