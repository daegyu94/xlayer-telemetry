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
        next((run / "telemetry-metrics").glob("verl-trainer-driver*.json")).read_text()
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


def test_config_async_launcher_and_inspect_use_the_same_run(tmp_path: Path) -> None:
    launcher = tmp_path / "launcher.sh"
    launcher.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' '{\"step\":3,\"data\":{\"timing_s/step\":9.0}}' > \"$VERL_FILE_LOGGER_PATH\"\n"
    )
    run = tmp_path / "run"
    config = tmp_path / "config"
    config.write_text(
        f"RUN_ID='hidden-async'\nRUN_ROOT='{run}'\nEXECUTION_MODE=async\n"
        f"VERL_COMMAND=(bash '{launcher}')\n"
    )
    command = ["bash", str(SCRIPT), "--config", str(config)]
    env = os.environ | {"TELEMETRY_PYTHON": sys.executable}
    before = subprocess.run(command + ["inspect"], env=env, capture_output=True, text=True, check=True)
    assert "No run artifacts yet" in before.stdout
    assert not run.exists()
    subprocess.run(command + ["run"], env=env, capture_output=True, text=True, check=True)
    manifest = json.loads((run / "telemetry-manifest.json").read_text())
    assert manifest["configuration"]["execution_mode"] == "async"
    history = json.loads((run / "telemetry-events" / "verl-steps.jsonl").read_text().splitlines()[0])
    assert history["execution_mode"] == "async"
    after = subprocess.run(command + ["inspect"], env=env, capture_output=True, text=True, check=True)
    assert "training_step_time_seconds{phase=rl_step}=9.0" in after.stdout
    assert "Native source registration: not configured" in after.stdout
    assert "Configuration is not proof" in after.stdout


@pytest.mark.parametrize("extra_logs", [False, True])
def test_local_config_keeps_metric_and_log_roots_together(tmp_path: Path, extra_logs: bool) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    arch = "arm64" if platform.machine() in {"aarch64", "arm64"} else "amd64"
    alloy = tools / f"alloy-linux-{arch}"
    alloy.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    alloy.chmod(0o755)
    run = tmp_path / "runs" / "grpo-001"
    ray_root = tmp_path / "ray-logs"
    ray_root.mkdir()
    config = tmp_path / "verl-local.conf"
    config.write_text(
        f"RUN_ID='grpo-001'\nRUN_ROOT='{run}'\n"
        f"TELEMETRY_HOME='{tmp_path / 'telemetry'}'\n"
        f"TOOLS_DIR='{tools}'\nENABLE_LOGS=1\n"
        "VERL_COMMAND=(/path/to/verl-env/bin/python)\n",
        encoding="utf-8",
    )
    if extra_logs:
        with config.open("a") as stream:
            stream.write(f'TELEMETRY_LOG_ROOTS="verl={run.parent},ray={ray_root}"\n')
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

    if extra_logs:
        assert f'{ray_root}/*/logs/**/*.log' in alloy_config
        assert 'workload = "ray"' in alloy_config


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


def _fake_local_stack(tmp_path: Path, *, node_fails: bool = False) -> tuple[Path, Path, dict[str, str]]:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(SCRIPT, scripts / "verl_local.sh")
    manager = scripts / "run_telemetry.sh"
    manager.write_text(
        "#!/usr/bin/env bash\n"
        "role=$1\n"
        "if [[ $role == node && ${FAKE_NODE_FAIL:-0} == 1 ]]; then exit 1; fi\n"
        "touch \"$MARKER_DIR/$role.ready\"\n"
        "if [[ $role == server ]]; then echo 'Monitoring server ready:'; fi\n"
        "trap 'touch \"$MARKER_DIR/$role.stopped\"; exit 0' TERM\n"
        "while true; do sleep 0.1; done\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "[[ -f \"$MARKER_DIR/node.ready\" ]]\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    state = tmp_path / "telemetry"
    config = tmp_path / "verl-local.conf"
    config.write_text(
        f"RUN_ID='background-test'\nTELEMETRY_HOME='{state}'\n",
        encoding="utf-8",
    )
    env = os.environ | {
        "TELEMETRY_PYTHON": sys.executable,
        "MARKER_DIR": str(tmp_path),
        "FAKE_NODE_FAIL": "1" if node_fails else "0",
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    return scripts / "verl_local.sh", config, env


def test_cpu_only_config_reaches_node_collector(tmp_path: Path) -> None:
    script, config, env = _fake_local_stack(tmp_path)
    config.write_text(config.read_text() + "ENABLE_GPU_METRICS=0\n")
    (script.parent / "run_telemetry.sh").write_text(
        '#!/usr/bin/env bash\n'
        '[[ "$1" == node && "$ENABLE_GPU_METRICS" == 0 ]]\n'
    )
    result = subprocess.run(["bash", str(script), "--config", str(config), "node"],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_local_up_and_down_manage_only_the_started_stack(tmp_path: Path) -> None:
    script, config, env = _fake_local_stack(tmp_path)
    command = ["bash", str(script), "--config", str(config)]
    stack_dir = tmp_path / "telemetry" / "state" / "verl-local"
    try:
        up = subprocess.run(command + ["up"], env=env, capture_output=True, text=True, timeout=10)
        assert up.returncode == 0, up.stderr
        assert "Monitoring ready" in up.stdout
        assert (stack_dir / "server.pid").exists()
        assert (stack_dir / "node.pid").exists()

        status = subprocess.run(command + ["status"], env=env | {"TELEMETRY_PYTHON": "/missing/python"},
                                capture_output=True, text=True, timeout=5)
        assert status.returncode == 0, status.stderr
        assert "server: running" in status.stdout and "node: running" in status.stdout
        assert "Process status only" in status.stdout
        assert (stack_dir / "server.pid").read_text().split()[2] == Path("/proc/sys/kernel/random/boot_id").read_text().strip()

        duplicate = subprocess.run(command + ["up"], env=env, capture_output=True, text=True, timeout=5)
        assert duplicate.returncode == 1
        assert "already running" in duplicate.stderr
        assert not (tmp_path / "server.stopped").exists()

        down = subprocess.run(
            command + ["down"],
            env=env | {"TELEMETRY_PYTHON": "/missing/python"},
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert down.returncode == 0, down.stderr
        assert (tmp_path / "server.stopped").exists()
        assert (tmp_path / "node.stopped").exists()
        assert not (stack_dir / "server.pid").exists()
        assert not (stack_dir / "node.pid").exists()
        status = subprocess.run(command + ["status"], env=env, capture_output=True, text=True, timeout=5)
        assert status.returncode == 1
        assert "server: not_running" in status.stdout and "node: not_running" in status.stdout
    finally:
        subprocess.run(command + ["down"], env=env, capture_output=True, timeout=15)


def test_local_up_cleans_server_when_node_fails(tmp_path: Path) -> None:
    script, config, env = _fake_local_stack(tmp_path, node_fails=True)
    command = ["bash", str(script), "--config", str(config)]
    up = subprocess.run(command + ["up"], env=env, capture_output=True, text=True, timeout=10)
    assert up.returncode == 1
    assert "Monitoring did not become ready" in up.stderr or "exited before" in up.stderr
    assert (tmp_path / "server.stopped").exists()
    stack_dir = tmp_path / "telemetry" / "state" / "verl-local"
    assert not (stack_dir / "server.pid").exists()


def test_local_down_ignores_reused_pid_record(tmp_path: Path) -> None:
    script, config, env = _fake_local_stack(tmp_path)
    stack_dir = tmp_path / "telemetry" / "state" / "verl-local"
    stack_dir.mkdir(parents=True)
    (stack_dir / "server.pid").write_text(f"{os.getpid()} 0\n", encoding="utf-8")
    down = subprocess.run(
        ["bash", str(script), "--config", str(config), "down"],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert down.returncode == 0, down.stderr
    assert not (stack_dir / "server.pid").exists()


def test_local_down_ignores_previous_boot_and_lifecycle_lock(tmp_path: Path) -> None:
    import fcntl

    script, config, env = _fake_local_stack(tmp_path)
    stack_dir = tmp_path / "telemetry" / "state" / "verl-local"
    stack_dir.mkdir(parents=True)
    # Even a matching PID/start time must not be trusted from a different boot.
    stat = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(") ", 1)[1].split()
    (stack_dir / "server.pid").write_text(f"{os.getpid()} {stat[19]} previous-boot\n")
    command = ["bash", str(script), "--config", str(config)]
    with (stack_dir / "lifecycle.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        blocked = subprocess.run(command + ["down"], env=env, capture_output=True, text=True, timeout=5)
        assert blocked.returncode == 1
        assert "Another monitoring up/down command" in blocked.stderr
        assert (stack_dir / "server.pid").exists()
    down = subprocess.run(command + ["down"], env=env, capture_output=True, text=True, timeout=5)
    assert down.returncode == 0, down.stderr
    assert not (stack_dir / "server.pid").exists()


@pytest.mark.parametrize('theme,expected', [('sapphiredusk', 0), ('gloom', 0), ('desertbloom', 0), ('unknown-theme', 2)])
def test_local_server_theme_configuration(tmp_path, theme, expected):
    config = tmp_path / 'local.conf'
    config.write_text(f'RUN_ID=test\nTELEMETRY_HOME="{tmp_path}/state"\nGF_USERS_DEFAULT_THEME={theme}\nGF_FEATURE_TOGGLES_ENABLE="extraThemes"\n')
    result = subprocess.run(['bash', str(SCRIPT), '--config', str(config), 'server'],
                            cwd=ROOT, env=os.environ | {'TELEMETRY_PYTHON': sys.executable,
                                                       'SERVER_CONFIG_ONLY': '1'},
                            capture_output=True, text=True)
    assert result.returncode == expected, result.stderr
    if expected:
        assert 'Unsupported GF_USERS_DEFAULT_THEME' in result.stderr
