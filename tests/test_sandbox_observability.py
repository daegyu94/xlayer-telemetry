"""Exercise cgroup parsing, label policy, lifecycle correlation and diagnosis."""

import json
import subprocess
import sys

import pytest

from xlayer_telemetry.diagnosis_analysis import evaluate_rules
from xlayer_telemetry.diagnostics import DiagnosticEngine, load_config, tool_span_window
from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.metrics.prometheus import format_gauges
from xlayer_telemetry.sandbox import SandboxRecorder
from xlayer_telemetry.sandbox_sampler import (
    parse_io_stat, parse_pressure, pressure_ratio, read_cgroup, samples,
)


def test_cgroup_v2_fixture_and_missing_sources(tmp_path):
    (tmp_path / "io.stat").write_text("259:0 rbytes=1024 wbytes=4096 rios=2 wios=4 dbytes=0\n259:1 rbytes=32 wbytes=64 rios=1 wios=1\n")
    (tmp_path / "io.pressure").write_text("some avg10=1.00 avg60=0.50 avg300=0.10 total=250000\nfull avg10=0.00 total=10\n")
    (tmp_path / "cpu.stat").write_text("usage_usec 2000000\nuser_usec 1500000\n")
    (tmp_path / "memory.current").write_text("8192\n")
    (tmp_path / "memory.peak").write_text("16384\n")
    (tmp_path / "memory.events").write_text("low 0\nhigh 1\noom 2\noom_kill 1\n")
    values = read_cgroup(tmp_path)
    assert values["rbytes"] == 1056
    assert values["wios"] == 5
    assert values["io_some_total_usec"] == 250000
    assert values["memory_current"] == 8192
    assert values["memory_peak"] == 16384
    assert values["memory_event_oom"] == 2
    assert "cpu_pressure_some_total_usec" not in values
    assert parse_io_stat("") == {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0}
    assert parse_pressure("full avg10=0 total=9") == {}


def test_metric_output_pressure_delta_and_cardinality():
    labels = {"node": "sandbox-2", "role": "sandbox", "runtime": "containerd",
              "filesystem": "overlayfs", "deployment": "dedicated"}
    before = {"io_some_total_usec": 100000}
    current = {"rbytes": 100, "wbytes": 200, "rios": 1, "wios": 2,
               "io_some_total_usec": 350000, "cpu_usage_usec": 3000000,
               "memory_current": 1000, "memory_peak": 2000, "memory_event_oom": 1}
    exposed = samples(current, previous=before, elapsed_seconds=1, labels=labels)
    text = format_gauges(exposed)
    assert 'sandbox_io_pressure_ratio{deployment="dedicated"' in text
    assert text.endswith("\n")
    assert any(item.name == "sandbox_io_pressure_ratio" and item.value == 0.25 for item in exposed)
    assert any(item.name == "sandbox_cpu_usage_seconds_total" and item.value == 3 for item in exposed)
    assert all("sandbox_id" not in item.labels and "trajectory_id" not in item.labels for item in exposed)
    with pytest.raises(ValueError):
        samples(current, previous=before, elapsed_seconds=1,
                labels=labels | {"sandbox_id": "sb-1"})
    assert pressure_ratio(current, None, 1, "io_some_total_usec") is None
    assert pressure_ratio({"io_some_total_usec": 0}, before, 1, "io_some_total_usec") is None


def test_sampler_cli_writes_node_exporter_textfile(tmp_path):
    cgroup = tmp_path / "worker"
    cgroup.mkdir()
    (cgroup / "io.stat").write_text("259:0 rbytes=1024 wbytes=2048 rios=2 wios=3\n")
    textfile = tmp_path / "textfile"
    subprocess.run([
        sys.executable, "-m", "xlayer_telemetry.sandbox_sampler",
        "--cgroup", str(cgroup), "--textfile-dir", str(textfile),
        "--node", "gpu-0", "--runtime", "docker",
        "--filesystem", "overlayfs", "--deployment", "colocated", "--once",
    ], check=True)
    output = (textfile / "sandbox.prom").read_text()
    assert "sandbox_io_write_bytes_total" in output
    assert "sandbox_sample_timestamp_seconds" in output
    assert "sandbox_id" not in output and "trajectory_id" not in output


@pytest.mark.parametrize("deployment,node", [("colocated", "gpu-0"), ("dedicated", "sandbox-2")])
def test_sandbox_lifecycle_reuses_trace_without_prometheus_ids(tmp_path, deployment, node):
    events = EventRecorder(tmp_path, CorrelationContext(
        run_id="run-1", producer="sandbox", role="sandbox", worker_id="worker-1", node=node))
    sandbox = SandboxRecorder(events, runtime="containerd", filesystem="overlayfs",
                              deployment=deployment, sandbox_node=node)
    with events.span("tool.call", phase="tool_interaction", step=7) as tool:
        with sandbox.span("exec", step=7, sandbox_id="sb-17", trajectory_id="traj-17",
                          trace_id=tool.trace_id, parent_span_id=tool.span_id):
            pass
    records = [json.loads(line) for line in events.path.read_text().splitlines()]
    child, parent = records
    assert child["trace_id"] == parent["trace_id"]
    assert child["parent_span_id"] == parent["span_id"]
    assert child["attributes"]["sandbox_id"] == "sb-17"
    assert child["attributes"]["deployment"] == deployment
    assert child["node"] == node
    assert child["role"] == "sandbox"


def test_individual_sandbox_io_delta_stays_in_correlated_event(tmp_path):
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    (cgroup / "io.stat").write_text("259:0 rbytes=100 wbytes=100 rios=1 wios=1\n")
    (cgroup / "io.pressure").write_text("some total=100\n")
    events = EventRecorder(tmp_path / "events", CorrelationContext(
        run_id="run-1", producer="sandbox", role="sandbox", worker_id="worker-1", node="sandbox-2"))
    sandbox = SandboxRecorder(events, runtime="docker", filesystem="overlayfs",
                              deployment="dedicated", sandbox_node="sandbox-2")
    with sandbox.span("exec", step=7, sandbox_id="sb-17", trajectory_id="traj-17",
                      trace_id="trace-17", cgroup=cgroup) as span:
        (cgroup / "io.stat").write_text("259:0 rbytes=300 wbytes=1400 rios=3 wios=5\n")
        (cgroup / "io.pressure").write_text("some total=200\n")
    records = [json.loads(line) for line in events.path.read_text().splitlines()]
    observation, lifecycle = records
    assert observation["name"] == "sandbox.resource_sample"
    assert observation["span_id"] == span.span_id
    assert observation["trace_id"] == lifecycle["trace_id"]
    assert observation["attributes"]["io_write_bytes_delta"] == 1300
    assert observation["attributes"]["io_write_ops_delta"] == 4
    assert observation["attributes"]["observation_scope"] == "cgroup"
    assert observation["attributes"]["sandbox_id"] == "sb-17"


def test_sandbox_candidate_preserves_scope_and_missing_evidence():
    baseline = {"tool_duration_seconds": 4}
    context = {"sandbox_node": "sandbox-2", "sandbox_device": "nvme0n1"}
    symptom = {"tool_duration_seconds": 9}
    partial = next(item for item in evaluate_rules(symptom, baseline, thresholds={}, context=context)
                   if item["id"] == "sandbox_local_storage_pressure")
    assert partial["state"] == "weak_signal"
    assert partial["missing_evidence"] == ["sandbox_io_pressure_ratio", "sandbox_device_busy_ratio"]
    observed = symptom | {"sandbox_io_pressure_ratio": 0.35, "sandbox_device_busy_ratio": 0.97}
    full = next(item for item in evaluate_rules(observed, baseline, thresholds={}, context=context)
                if item["id"] == "sandbox_local_storage_pressure")
    assert full["state"] == "supporting_signal"
    assert {item["observation_scope"] for item in full["evidence"]} == {"application", "cgroup", "device"}
    assert full["related_nodes"] == ["sandbox-2"]
    assert full["related_devices"] == ["nvme0n1"]


def test_dedicated_node_diagnosis_queries_the_sandbox_device():
    class Prometheus:
        def __init__(self):
            self.queries = []

        def query_range(self, query, start, end, step):
            self.queries.append(query)
            if query.startswith("agent_tool_call_duration_seconds"):
                return {"max": 9 if start == 20 else 4}
            if query.startswith("sandbox_io_pressure_ratio"):
                return {"max": 0.4 if start == 20 else 0.05}
            if query.startswith("rate(node_disk_io_time_seconds_total") and 'device="nvme0n1"' in query:
                return {"max": 0.95 if start == 20 else 0.2}
            return None

    prom = Prometheus()
    engine = DiagnosticEngine(
        {"schema_version": 1, "prometheus": {"url": "http://prometheus"},
         "sandbox": {"enabled": True, "node": "sandbox-2", "device": "nvme0n1"}},
        prometheus=prom, clock=lambda: 31,
    )
    baseline = {"run_id": "run-1", "worker_id": "driver", "boundary_scope": "rl_step",
                "observed_at": 10, "step_duration_seconds": 10,
                "analysis_window": {"start": 0, "end": 10}}
    current = {"run_id": "run-1", "node": "gpu-0", "worker_id": "driver",
               "boundary_scope": "rl_step", "observed_at": 30, "step_duration_seconds": 20,
               "analysis_window": {"start": 20, "end": 30}}
    report = engine.analyze(current, [baseline])
    candidate = next(item for item in report["candidates"]
                     if item["id"] == "sandbox_local_storage_pressure")
    assert candidate["state"] == "supporting_signal"
    assert candidate["related_nodes"] == ["sandbox-2"]
    assert any('nodename="sandbox-2"' in query and "sandbox_io_pressure_ratio" in query
               for query in prom.queries)
    assert any('nodename="sandbox-2"' in query and 'device="nvme0n1"' in query
               for query in prom.queries)
    assert any('run_id="run-1"' in query and "agent_tool_call_duration_seconds" in query
               and "nodename" not in query
               for query in prom.queries)


def test_sandbox_config_requires_explicit_types(tmp_path):
    path = tmp_path / "diagnostics.json"
    path.write_text(json.dumps({"schema_version": 1, "prometheus": {"url": "http://prometheus"},
                                "sandbox": {"enabled": "yes"}}))
    with pytest.raises(ValueError, match="sandbox"):
        load_config(path)
    path.write_text(json.dumps({"schema_version": 1, "prometheus": {"url": "http://prometheus"},
                                "sandbox": {"enabled": True, "events_dir": 42}}))
    with pytest.raises(ValueError, match="sandbox.events_dir"):
        load_config(path)


def test_exact_tool_spans_supply_duration_when_prometheus_tool_metric_is_missing(tmp_path):
    directory = tmp_path / "events"
    for run_id, times in (("run-1", [1, 2, 21, 24]),
                          ("other-run", [22, 23])):
        ticks = iter(int(second * 1e9) for second in times)
        recorder = EventRecorder(directory, CorrelationContext(
            run_id=run_id, producer="agent", role="rollout",
            worker_id=run_id, node="gpu-0"), clock_ns=lambda: next(ticks))
        for _ in range(len(times) // 2):
            with recorder.span("tool.call", phase="tool_interaction",
                               attributes={"tool": "pytest"}):
                pass
    (directory / "agent-rollout-broken.jsonl").write_text("{not-json}\n")
    assert tool_span_window(directory, "run-1", 0, 10)["max"] == 1
    assert tool_span_window(directory, "run-1", 20, 30)["max"] == 3
    assert tool_span_window(directory, "run-1", 2, 10) is None
    assert tool_span_window(directory, "run-1", 0, 10, tool_name="different-tool") is None

    class Prometheus:
        def query_range(self, query, start, end, step):
            if query.startswith("sandbox_io_pressure_ratio"):
                return {"max": 0.4}
            if query.startswith("rate(node_disk_io_time_seconds_total") and 'device="nvme0n1"' in query:
                return {"max": 0.95}
            return None

    engine = DiagnosticEngine(
        {"schema_version": 1, "prometheus": {"url": "http://prometheus"},
         "sandbox": {"enabled": True, "events_dir": str(directory),
                     "node": "sandbox-2", "device": "nvme0n1"}},
        prometheus=Prometheus(), clock=lambda: 31,
    )
    baseline = {"run_id": "run-1", "worker_id": "driver", "boundary_scope": "rl_step",
                "observed_at": 10, "step_duration_seconds": 10,
                "analysis_window": {"start": 0, "end": 10}}
    current = {"run_id": "run-1", "node": "gpu-0", "worker_id": "driver",
               "boundary_scope": "rl_step", "observed_at": 30, "step_duration_seconds": 20,
               "analysis_window": {"start": 20, "end": 30}}
    report = engine.analyze(current, [baseline])
    candidate = next(item for item in report["candidates"]
                     if item["id"] == "sandbox_local_storage_pressure")
    assert candidate["state"] == "supporting_signal"
    assert candidate["related_spans"] == [tool_span_window(directory, "run-1", 20, 30)["related_span"]]
    duration = next(item for item in candidate["evidence"]
                    if item["signal"] == "tool_duration_seconds")
    assert duration["source"] == "event_span_time_window"
    assert duration["query"] is None
    assert duration["value"] == 3
    assert duration["baseline"] == 1
    assert "prometheus:tool_duration_seconds" not in report["missing_sources"]


def test_tool_span_baseline_does_not_compare_different_operations(tmp_path):
    directory = tmp_path / "events"
    ticks = iter(int(second * 1e9) for second in (1, 2, 21, 25))
    events = EventRecorder(directory, CorrelationContext(
        run_id="run-1", producer="agent", role="rollout",
        worker_id="worker-0", node="gpu-0"), clock_ns=lambda: next(ticks))
    for tool in ("read_source", "edit_and_test"):
        with events.span("tool.call", phase="tool_interaction", attributes={"tool": tool}):
            pass

    class Prometheus:
        def query_range(self, query, start, end, step):
            if query.startswith("agent_tool_call_duration_seconds"):
                return {"max": 1}
            return None

    engine = DiagnosticEngine(
        {"schema_version": 1, "prometheus": {"url": "http://prometheus"},
         "sandbox": {"enabled": True, "events_dir": str(directory)}},
        prometheus=Prometheus(), clock=lambda: 30,
    )
    baseline = {"run_id": "run-1", "worker_id": "driver", "boundary_scope": "rl_step",
                "observed_at": 10, "step_duration_seconds": 10,
                "analysis_window": {"start": 0, "end": 10}}
    current = {"run_id": "run-1", "node": "gpu-0", "worker_id": "driver",
               "boundary_scope": "rl_step", "observed_at": 30, "step_duration_seconds": 20,
               "analysis_window": {"start": 20, "end": 30}}
    report = engine.analyze(current, [baseline])
    duration = next(item for item in report["comparison"]["signals"]
                    if item["signal"] == "tool_duration_seconds")
    assert duration["current"] == 4
    assert duration["baseline"] is None
    assert not any(item["id"] == "sandbox_local_storage_pressure"
                   for item in report["candidates"])
