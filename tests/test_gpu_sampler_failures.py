"""CPU-only fault injection for the optional GPU collector and real node launcher."""

import csv
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import textwrap
import time
from types import SimpleNamespace

import pytest

from tests._process_helpers import child_processes, process_running, signal_process
from xlayer_telemetry.collectors import gpu_sampler


ROOT = Path(__file__).parents[1]
DEVICE = ["0", "47", "100", "45", "900", "1024", "8192", "GPU-example"]
MEMINFO = "\n".join(f"{name}: 0 kB" for name in (
    "MemTotal", "MemAvailable", "MemFree", "Buffers", "Cached", "SwapTotal", "SwapFree",
))


def mock_meminfo(monkeypatch, value):
    original = Path.read_text

    def read_text(path, *args, **kwargs):
        if path == Path("/proc/meminfo"):
            if isinstance(value, Exception):
                raise value
            return value
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)


@pytest.mark.parametrize("failure", [
    PermissionError("denied optional meminfo"),
    FileNotFoundError("missing optional meminfo"),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid optional meminfo"),
])
def test_unreadable_optional_meminfo_preserves_device_observations(monkeypatch, failure):
    monkeypatch.setattr(gpu_sampler, "query", lambda *args: [DEVICE])
    mock_meminfo(monkeypatch, failure)
    value = gpu_sampler.snapshot()
    assert value["gpus"][0]["utilization.gpu"] == 47
    assert value["collection_success"] is True
    assert value["host_memory"] == {}
    assert value["host_memory_collection_success"] is False
    assert value["host_memory_error"] == type(failure).__name__
    assert "MemTotal_bytes" in value["host_memory_unavailable_fields"]


@pytest.mark.parametrize("bad_line", [
    "MemAvailable:", "MemAvailable: not-a-counter kB", "MemAvailable: -1 kB",
    "MemAvailable: 1 MB", "MemAvailable: 1", "malformed line", "",
])
def test_partial_optional_meminfo_omits_only_unavailable_values(monkeypatch, bad_line):
    monkeypatch.setattr(gpu_sampler, "query", lambda *args: [DEVICE])
    mock_meminfo(monkeypatch, f"MemTotal: 1024 kB\nMemFree: 0 kB\n{bad_line}\n")
    value = gpu_sampler.snapshot()
    assert value["gpus"][0]["uuid"] == "GPU-example"
    assert value["host_memory"] == {"MemTotal_bytes": 1024**2, "MemFree_bytes": 0}
    assert value["host_memory_collection_success"] is False
    assert value["host_memory_error"] == "invalid_or_missing_fields"
    assert "MemAvailable_bytes" in value["host_memory_unavailable_fields"]
    assert "MemTotal_bytes" not in value["host_memory_unavailable_fields"]


def test_complete_optional_meminfo_is_healthy_and_preserves_zero(monkeypatch):
    monkeypatch.setattr(gpu_sampler, "query", lambda *args: [DEVICE])
    mock_meminfo(monkeypatch, MEMINFO)
    value = gpu_sampler.snapshot()
    assert value["host_memory_collection_success"] is True
    assert value["host_memory_unavailable_fields"] == []
    assert "host_memory_error" not in value
    assert len(value["host_memory"]) == 7
    assert all(measurement == 0 for measurement in value["host_memory"].values())


def test_unavailable_host_memory_exports_health_without_losing_gpu_values(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_sampler, "query", lambda *args: [DEVICE])
    mock_meminfo(monkeypatch, PermissionError("optional meminfo denied"))
    monkeypatch.setattr(gpu_sampler, "time", SimpleNamespace(
        monotonic=iter((0, 0, 0, 2)).__next__, time=lambda: 123, sleep=lambda _: None,
    ))
    monkeypatch.setattr(sys, "argv", ["gpu_sampler", "--output", str(tmp_path / "gpu.jsonl"),
                                     "--textfile-dir", str(tmp_path), "--duration", "1"])
    gpu_sampler.main()
    text = (tmp_path / "gpu.prom").read_text()
    assert "telemetry_gpu_collection_success 1" in text
    assert "telemetry_gpu_host_memory_collection_success 0" in text
    assert "telemetry_gpu_sample_timestamp_seconds 123" in text
    assert 'telemetry_gpu_memory_used_bytes{gpu="0",gpu_uuid="GPU-example"} 1073741824' in text


@pytest.mark.parametrize("failure", [
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid optional process output"), csv.Error("bad CSV"),
])
def test_optional_process_decoding_failure_preserves_device_observations(monkeypatch, failure):
    def query(kind, fields):
        if kind == "gpu":
            return [DEVICE]
        raise failure
    monkeypatch.setattr(gpu_sampler, "query", query)
    value = gpu_sampler.snapshot(include_processes=True)
    assert value["gpus"][0]["uuid"] == "GPU-example"
    assert value["collection_success"] is True
    assert value["compute_processes"] == []
    assert value["compute_processes_error"] == type(failure).__name__


@pytest.mark.parametrize("device_rows", [
    [], [["N/A"], ["-1"], ["0.5"], []], [["0"]],
    [["0", "N/A", "N/A", "N/A", "N/A", "N/A", "N/A", "GPU-example"]],
])
@pytest.mark.parametrize("process_metrics", [False, True])
def test_no_device_observations_do_not_advance_freshness_but_preserve_processes(
        tmp_path, monkeypatch, device_rows, process_metrics):
    def query(kind, fields):
        if kind == "gpu":
            return device_rows
        return [["GPU-example", "123", "python", "512"]]
    monkeypatch.setattr(gpu_sampler, "query", query)
    monkeypatch.setattr(gpu_sampler, "time", SimpleNamespace(
        monotonic=iter((0, 0, 0, 2)).__next__, time=lambda: 123, sleep=lambda _: None,
    ))
    monkeypatch.setattr(sys, "argv", ["gpu_sampler", "--output", str(tmp_path / "gpu.jsonl"),
                                     "--textfile-dir", str(tmp_path), "--duration", "1"]
                        + (["--process-metrics"] if process_metrics else []))
    (tmp_path / "gpu.prom").write_text("telemetry_gpu_sample_timestamp_seconds 100\n"
                                       'telemetry_gpu_utilization_percent{gpu="0"} 47\n')
    gpu_sampler.main()
    row = json.loads((tmp_path / "gpu.jsonl").read_text())
    text = (tmp_path / "gpu.prom").read_text()
    assert row["collection_success"] is False
    assert row["collection_error"] == "no_device_observations"
    assert "telemetry_gpu_collection_success 0" in text
    assert "telemetry_gpu_sample_timestamp_seconds" not in text
    assert "telemetry_gpu_utilization_percent" not in text
    assert "telemetry_gpu_memory_used_bytes" not in text
    if process_metrics:
        assert row["compute_processes"][0]["used_gpu_memory_mib"] == 512
        assert 'gpu_uuid="GPU-example",pid="123"} 536870912' in text
        assert "telemetry_gpu_process_samples_truncated 0" in text
    else:
        assert "telemetry_gpu_process_memory_bytes" not in text


def test_partial_device_observation_including_zero_remains_successful(monkeypatch):
    monkeypatch.setattr(gpu_sampler, "query", lambda *args: [["N/A"], ["0", "0"]])
    value = gpu_sampler.snapshot()
    assert value["collection_success"] is True
    assert "collection_error" not in value
    assert value["gpus"][0]["utilization.gpu"] == 0


@pytest.mark.parametrize("failure", [
    subprocess.CalledProcessError(15, ["nvidia-smi", "private-argument"], stderr="private-error"),
    subprocess.TimeoutExpired("nvidia-smi", 10),
    FileNotFoundError("missing nvidia-smi"),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "malformed GPU output"),
    csv.Error("malformed CSV"),
])
def test_failed_sample_clears_stale_exposition_and_recovers_at_interval(tmp_path, monkeypatch, failure):
    outcomes = iter(([DEVICE], failure, [DEVICE]))

    def query(kind, fields):
        if kind == "compute-apps":
            return [["GPU-example", "123", "python", "512"]]
        value = next(outcomes)
        if isinstance(value, Exception):
            raise value
        return value

    clock = SimpleNamespace(now=0)
    expositions = []
    sleeps = []

    def sleep(seconds):
        expositions.append((tmp_path / "gpu.prom").read_text())
        sleeps.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(gpu_sampler, "query", query)
    mock_meminfo(monkeypatch, MEMINFO)
    monkeypatch.setattr(gpu_sampler, "time", SimpleNamespace(
        monotonic=lambda: clock.now, time=lambda: 100 + clock.now, sleep=sleep,
    ))
    monkeypatch.setattr(sys, "argv", [
        "gpu_sampler", "--output", str(tmp_path / "gpu.jsonl"),
        "--textfile-dir", str(tmp_path), "--duration", "3", "--interval", "1", "--process-metrics",
    ])
    gpu_sampler.main()

    rows = [json.loads(line) for line in (tmp_path / "gpu.jsonl").read_text().splitlines()]
    assert [row["collection_success"] for row in rows] == [True, False, True]
    assert rows[1]["collection_error"] == type(failure).__name__
    assert rows[1]["gpus"] == [] and rows[1]["compute_processes"] == []
    assert "private-" not in json.dumps(rows[1])
    assert sleeps == [1, 1, 1]
    for index, timestamp in ((0, 100), (2, 102)):
        assert "telemetry_gpu_collection_success 1" in expositions[index]
        assert f"telemetry_gpu_sample_timestamp_seconds {timestamp}" in expositions[index]
        assert 'telemetry_gpu_utilization_percent{gpu="0",gpu_uuid="GPU-example"} 47' in expositions[index]
        assert "telemetry_gpu_process_memory_bytes" in expositions[index]
        assert "telemetry_gpu_host_memory_collection_success 1" in expositions[index]
    assert "telemetry_gpu_collection_success 0" in expositions[1]
    assert "telemetry_gpu_process_collection_enabled 1" in expositions[1]
    for name in ("sample_timestamp_seconds", "utilization_percent", "memory_used_bytes",
                 "process_memory_bytes", "process_sample_timestamp_seconds", "process_samples_truncated",
                 "host_memory_collection_success"):
        assert f"telemetry_gpu_{name}" not in expositions[1]


def test_configuration_and_exposition_errors_remain_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gpu_sampler", "--output", str(tmp_path / "gpu.jsonl"),
                                     "--textfile-dir", str(tmp_path)])
    def invalid_configuration(**kwargs):
        raise ValueError("invalid collection configuration")
    monkeypatch.setattr(gpu_sampler, "snapshot", invalid_configuration)
    with pytest.raises(ValueError, match="invalid collection configuration"):
        gpu_sampler.main()
    (tmp_path / "gpu.jsonl").unlink()
    monkeypatch.setattr(gpu_sampler, "snapshot", lambda **kwargs: {
        "timestamp": 1, "collection_success": True, "gpus": [], "compute_processes": [],
    })
    def denied_exposition(*args):
        raise PermissionError("unwritable textfile directory")
    monkeypatch.setattr(gpu_sampler, "write_gauges", denied_exposition)
    with pytest.raises(PermissionError, match="unwritable textfile directory"):
        gpu_sampler.main()


@pytest.mark.skipif(sys.platform != "linux", reason="node launcher requires Linux")
def test_real_node_launcher_survives_gpu_failures_recovers_and_stops_owned_children(tmp_path):
    # Only fixture-owned subprocesses; no GPU, network service, or existing exporter is used.
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    if arch is None:
        pytest.skip("node launcher supports ARM64 and x86_64")
    tools = tmp_path / "tools"
    exporter = tools / f"node_exporter-1.9.1.linux-{arch}" / "node_exporter"
    exporter.parent.mkdir(parents=True)
    marker = tmp_path / "node-events"
    exporter.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''
        import os, signal, time
        marker = os.environ["TEST_NODE_MARKER"]
        def event(value):
            with open(marker, "a") as stream:
                stream.write(value + "\\n")
        def stop(*args):
            event("terminated")
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, stop)
        event("started")
        while True:
            time.sleep(.05)
    '''))
    exporter.chmod(0o755)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    mode = tmp_path / "gpu-mode"
    mode.write_text("failure")
    calls = tmp_path / "gpu-calls"
    smi = bindir / "nvidia-smi"
    smi.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''
        import os, pathlib, sys, time
        mode = pathlib.Path(os.environ["TEST_GPU_MODE"]).read_text()
        with open(os.environ["TEST_GPU_CALLS"], "a") as stream:
            stream.write(str(time.monotonic()) + " " + mode + "\\n")
        if mode == "failure":
            print("synthetic device disappeared", file=sys.stderr)
            raise SystemExit(15)
        print("0, 47, 100, 45, 900, 1024, 8192, GPU-example")
    '''))
    smi.chmod(0o755)
    output = tmp_path / "state"
    env = os.environ | {
        "TOOLS_DIR": str(tools), "OUTPUT_DIR": str(output), "NODE_ADDR": "127.0.0.1",
        "ENABLE_GPU_METRICS": "1", "PYTHON": sys.executable,
        "TEST_NODE_MARKER": str(marker), "TEST_GPU_MODE": str(mode), "TEST_GPU_CALLS": str(calls),
        "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
        "TELEMETRY_METRICS_DIR": "", "TELEMETRY_RUNS_ROOT": "", "TOPOLOGY_DIR": "",
        "LOKI_PUSH_URL": "", "TELEMETRY_LOG_ROOTS": "", "ENABLE_SSD_HEALTH": "0", "DURATION": "",
        "GPU_PROCESS_METRICS": "0", "GPU_MAX_PROCESSES": "256",
    }
    unrelated = subprocess.Popen(["sleep", "30"], start_new_session=True)
    launcher = subprocess.Popen(["bash", str(ROOT / "scripts/run_telemetry.sh"), "node"],
                                cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
    children = []
    def await_status(success, after=0):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            assert launcher.poll() is None, "optional GPU failure terminated the node launcher"
            try:
                text = (output / "textfile/gpu.prom").read_text()
                attempts = calls.read_text().splitlines()
                if f"telemetry_gpu_collection_success {success}\n" in text and len(attempts) > after:
                    return text, attempts
            except FileNotFoundError:
                pass
            time.sleep(.02)
        pytest.fail(f"GPU collector did not reach success={success}")

    try:
        _, first_calls = await_status(0)
        failed_text, failed_calls = await_status(0, after=len(first_calls))
        assert len(failed_calls) >= 2
        assert float(failed_calls[1].split()[0]) - float(failed_calls[0].split()[0]) >= .9
        assert "telemetry_gpu_sample_timestamp_seconds" not in failed_text
        assert marker.read_text() == "started\n"
        children = child_processes(launcher.pid)
        assert len(children) == 2 and all(process_running(child) for child in children)
        mode.write_text("success")
        recovered, successful_calls = await_status(1, after=len(failed_calls))
        assert 'telemetry_gpu_utilization_percent{gpu="0",gpu_uuid="GPU-example"} 47' in recovered
        mode.write_text("failure")
        failed_again, repeated_calls = await_status(0, after=len(successful_calls))
        assert "telemetry_gpu_utilization_percent" not in failed_again
        assert "telemetry_gpu_sample_timestamp_seconds" not in failed_again
        assert marker.read_text() == "started\n"
        assert all(process_running(child) for child in children)
        assert unrelated.poll() is None
        mode.write_text("success")
        await_status(1, after=len(repeated_calls))
        rows = [json.loads(line) for path in output.glob("gpu-*.jsonl") for line in path.read_text().splitlines()]
        assert rows[-1]["collection_success"] is True
        assert any(row.get("collection_error") == "CalledProcessError" for row in rows)
        launcher.terminate()
        assert launcher.wait(timeout=6) == 143
        assert marker.read_text() == "started\nterminated\n"
        assert all(not process_running(child) for child in children)
        assert not (output / "textfile/gpu.prom").exists()
        assert unrelated.poll() is None
    finally:
        if launcher.poll() is None:
            launcher.terminate()
            try:
                launcher.wait(timeout=6)
            except subprocess.TimeoutExpired:
                for child in child_processes(launcher.pid):
                    signal_process(child, signal.SIGKILL)
                launcher.kill()
                launcher.wait(timeout=3)
        for child in children:
            signal_process(child, signal.SIGKILL)
        if launcher.stderr is not None:
            launcher.stderr.close()
        unrelated.terminate()
        unrelated.wait(timeout=3)
