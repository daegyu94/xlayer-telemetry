import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "verl_local.sh"


def test_local_config_generates_server_target_and_collects_verl_step(tmp_path: Path) -> None:
    fake_verl = tmp_path / "fake-verl"
    fake_verl.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' '{\"step\":7,\"data\":{\"timing_s/gen\":2.4,\"perf/time_per_step\":8.0}}' > \"$VERL_FILE_LOGGER_PATH\"\n",
        encoding="utf-8",
    )
    fake_verl.chmod(0o755)
    state = tmp_path / "state"
    run = tmp_path / "runs" / "grpo-001"
    config = tmp_path / "verl-local.conf"
    config.write_text(
        f"RUN_ID='grpo-001'\n"
        f"RUN_ROOT='{run}'\n"
        f"TELEMETRY_HOME='{state}'\n"
        f"NODE_NAME='gpu-local'\n"
        f"VERL_COMMAND=('{fake_verl}' -m verl.trainer.main_ppo)\n",
        encoding="utf-8",
    )
    env = os.environ | {
        "TELEMETRY_PYTHON": sys.executable,
        "SERVER_CONFIG_ONLY": "1",
    }
    server = subprocess.run(
        ["bash", str(SCRIPT), "--config", str(config), "server"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert server.returncode == 0, server.stderr
    generated = (state / "state" / "server" / "prometheus.yml").read_text()
    assert "gpu-local" in generated
    assert "127.0.0.1:19100" in generated

    result = subprocess.run(
        ["bash", str(SCRIPT), "--config", str(config), "run"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(
        (run / "telemetry-metrics" / "verl-trainer-driver.json").read_text()
    )
    assert snapshot["step"] == 7
    assert snapshot["run_id"] == "grpo-001"
    assert snapshot["node"] == "gpu-local"
    assert (run / "telemetry-events" / "verl-steps.jsonl").is_file()


def test_local_config_rejects_placeholder_command(tmp_path: Path) -> None:
    config = tmp_path / "verl-local.conf"
    config.write_text(
        "RUN_ID='grpo-001'\nVERL_COMMAND=(/path/to/verl-env/bin/python)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(SCRIPT), "--config", str(config), "run"],
        cwd=ROOT,
        env=os.environ | {"TELEMETRY_PYTHON": sys.executable},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Replace VERL_COMMAND" in result.stderr


def test_local_config_keeps_metric_and_log_roots_together(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    arch = "arm64" if platform.machine() in {"aarch64", "arm64"} else "amd64"
    alloy = tools / f"alloy-linux-{arch}"
    alloy.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    alloy.chmod(0o755)
    run = tmp_path / "runs" / "grpo-001"
    config = tmp_path / "verl-local.conf"
    config.write_text(
        f"RUN_ID='grpo-001'\nRUN_ROOT='{run}'\n"
        f"TELEMETRY_HOME='{tmp_path / 'telemetry'}'\n"
        f"TOOLS_DIR='{tools}'\nENABLE_LOGS=1\n"
        "VERL_COMMAND=(/path/to/verl-env/bin/python)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(SCRIPT), "--config", str(config), "node"],
        cwd=ROOT,
        env=os.environ
        | {
            "TELEMETRY_PYTHON": sys.executable,
            "NODE_CONFIG_ONLY": "1",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    alloy_config = (tmp_path / "telemetry" / "state" / "node" / "alloy.alloy").read_text()
    assert f"{run.parent}/*/telemetry-events/verl-steps*.jsonl" in alloy_config
    assert f"{run.parent}/*/logs/**/*.log" in alloy_config
    assert 'node = "gpu-local"' in alloy_config


@pytest.mark.parametrize("action", ["server", "node"])
def test_background_manager_owns_wrapper_pid(tmp_path: Path, action: str) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(SCRIPT, scripts / "verl_local.sh")
    manager = scripts / "run_telemetry.sh"
    manager.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$$\" > \"$PID_MARKER\"\n"
        "trap 'printf stopped > \"$STOP_MARKER\"; exit 0' TERM\n"
        "while true; do sleep 0.1; done\n",
        encoding="utf-8",
    )
    config = tmp_path / "verl-local.conf"
    config.write_text("RUN_ID='background-test'\n", encoding="utf-8")
    pid_marker = tmp_path / "manager.pid"
    stop_marker = tmp_path / "stopped"
    process = subprocess.Popen(
        ["bash", str(scripts / "verl_local.sh"), "--config", str(config), action],
        cwd=tmp_path,
        env=os.environ
        | {
            "TELEMETRY_PYTHON": sys.executable,
            "PID_MARKER": str(pid_marker),
            "STOP_MARKER": str(stop_marker),
        },
    )
    try:
        deadline = time.monotonic() + 5
        while not pid_marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert pid_marker.exists()
        assert int(pid_marker.read_text()) == process.pid
        process.terminate()
        assert process.wait(timeout=5) == 0
        assert stop_marker.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
