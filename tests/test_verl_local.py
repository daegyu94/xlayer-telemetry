import json
import os
from pathlib import Path
import platform
import subprocess
import sys


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
