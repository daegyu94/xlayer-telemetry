"""Managed monitoring shutdown stays bounded and signals only live owned jobs."""

import os
from pathlib import Path
import platform
import shlex
import signal
import subprocess
import sys
import time

import pytest

from tests._process_helpers import read_process, process_running, signal_process

ROOT = Path(__file__).parents[1]


def _stubborn_exporter(tmp_path):
    arch = "arm64" if platform.machine() in {"aarch64", "arm64"} else "amd64"
    tools = tmp_path / "tools"
    exporter = tools / f"node_exporter-1.9.1.linux-{arch}/node_exporter"
    exporter.parent.mkdir(parents=True)
    marker = tmp_path / "exporter.pid"
    exporter.write_text(f"#!{sys.executable}\n"
        "import os, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "from pathlib import Path\n"
        f"marker=Path({str(marker)!r}); temporary=marker.with_suffix('.tmp')\n"
        "temporary.write_text(str(os.getpid())); temporary.replace(marker)\n"
        "while True: time.sleep(60)\n")
    exporter.chmod(0o755)
    return tools, marker


def _wait_exporter(marker):
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert marker.exists(), "fixture exporter did not start"
    return read_process(int(marker.read_text()))


@pytest.mark.parametrize("stopped", [False, True])
def test_node_shutdown_kills_unresponsive_owned_exporter(tmp_path, stopped):
    tools, marker = _stubborn_exporter(tmp_path)
    output = tmp_path / "output"
    unrelated = subprocess.Popen(["sleep", "30"])
    wrapper = subprocess.Popen(["bash", str(ROOT / "scripts/run_telemetry.sh"), "node"],
        env=os.environ | {"TOOLS_DIR": str(tools), "OUTPUT_DIR": str(output),
                          "NODE_ADDR": "127.0.0.1", "ENABLE_GPU_METRICS": "0"},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    exporter = None
    try:
        exporter = _wait_exporter(marker)
        if stopped:
            signal_process(exporter, signal.SIGSTOP)
        wrapper.terminate()
        assert wrapper.wait(timeout=6) == 143
        assert not process_running(exporter)
        assert unrelated.poll() is None
        assert not (output / "textfile/collector.prom").exists()
    finally:
        if exporter:
            signal_process(exporter, signal.SIGKILL)
        try:
            wrapper.wait(timeout=3)
        except subprocess.TimeoutExpired:
            wrapper.kill()
            wrapper.wait(timeout=3)
        wrapper.stderr.close()
        unrelated.terminate()
        unrelated.wait(timeout=3)


def test_completed_collector_does_not_leave_unresponsive_sibling(tmp_path):
    tools, marker = _stubborn_exporter(tmp_path)
    wrapper = subprocess.Popen(["bash", str(ROOT / "scripts/run_telemetry.sh"), "node"],
        env=os.environ | {"TOOLS_DIR": str(tools), "OUTPUT_DIR": str(tmp_path / "output"),
                          "NODE_ADDR": "127.0.0.1", "ENABLE_GPU_METRICS": "0", "DURATION": "0.2"},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    exporter = None
    try:
        exporter = _wait_exporter(marker)
        assert wrapper.wait(timeout=6) == 0
        assert not process_running(exporter)
    finally:
        if exporter:
            signal_process(exporter, signal.SIGKILL)
        try:
            wrapper.wait(timeout=3)
        except subprocess.TimeoutExpired:
            wrapper.kill()
            wrapper.wait(timeout=3)
        wrapper.stderr.close()


def test_managed_down_finishes_after_stubborn_exporter(tmp_path):
    tools, marker = _stubborn_exporter(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text(f"#!/bin/sh\ntest -f {shlex.quote(str(marker))}\n")
    curl.chmod(0o755)
    config = tmp_path / "config.toml"
    import json
    config.write_text('[telemetry]\n' +
        f'TELEMETRY_HOME={json.dumps(str(tmp_path / "state"))}\n' +
        f'TOOLS_DIR={json.dumps(str(tools))}\nENABLE_GPU_METRICS=false\n')
    env = os.environ | {"PATH": str(fake_bin) + ":" + os.environ["PATH"]}
    command = [sys.executable, "-m", "xlayer_telemetry.cli", "--config", str(config)]
    exporter = None
    try:
        up = subprocess.run(command + ["up", "--role", "node"], env=env,
                            capture_output=True, text=True, timeout=10)
        assert up.returncode == 0, up.stdout + up.stderr
        exporter = _wait_exporter(marker)
        down = subprocess.run(command + ["down", "--role", "node"], env=env,
                              capture_output=True, text=True, timeout=7)
        assert down.returncode == 0, down.stdout + down.stderr
        assert not process_running(exporter)
        assert not (tmp_path / "state/state/verl-local/node.pid").exists()
        assert config.exists()
    finally:
        if exporter:
            signal_process(exporter, signal.SIGKILL)
        subprocess.run(command + ["down", "--role", "node"], env=env,
                       capture_output=True, text=True, timeout=15)


def test_managed_cleanup_ignores_completed_job_pid(tmp_path):
    source = (ROOT / "scripts/run_telemetry.sh").read_text()
    helpers = source[source.index("pids=()"):source.index("trap cleanup EXIT")]
    unrelated = subprocess.Popen(["sleep", "30"])
    harness = tmp_path / "cleanup.sh"
    harness.write_text("set -euo pipefail\nrole=server\n" + helpers +
        f"\npids=({unrelated.pid})\n" +
        # A retained completion notice is not a live child, even if its PID
        # now identifies some other process. No actual PID reuse is needed.
        f'jobs() {{ if [[ "$1" == -p ]]; then echo {unrelated.pid}; fi; return 0; }}\n' +
        "cleanup\n")
    try:
        result = subprocess.run(["bash", str(harness)], capture_output=True, text=True, timeout=3)
        assert result.returncode == 0, result.stderr
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)


def test_failed_collector_keeps_exit_code_during_sibling_cleanup(tmp_path):
    tools, marker = _stubborn_exporter(tmp_path)
    failed_python = tmp_path / "failed-python"
    failed_python.write_text('#!/usr/bin/env bash\n' +
        f'while [[ ! -f {shlex.quote(str(marker))} ]]; do sleep .01; done\nexit 7\n')
    failed_python.chmod(0o755)
    wrapper = subprocess.Popen(["bash", str(ROOT / "scripts/run_telemetry.sh"), "node"],
        env=os.environ | {"TOOLS_DIR": str(tools), "OUTPUT_DIR": str(tmp_path / "output"),
                          "NODE_ADDR": "127.0.0.1", "ENABLE_GPU_METRICS": "0",
                          "TELEMETRY_RUNS_ROOT": str(tmp_path / "runs"), "PYTHON": str(failed_python)},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    exporter = None
    try:
        exporter = _wait_exporter(marker)
        assert wrapper.wait(timeout=6) == 7
        assert not process_running(exporter)
    finally:
        if exporter:
            signal_process(exporter, signal.SIGKILL)
        try:
            wrapper.wait(timeout=3)
        except subprocess.TimeoutExpired:
            wrapper.kill()
            wrapper.wait(timeout=3)
        wrapper.stderr.close()
