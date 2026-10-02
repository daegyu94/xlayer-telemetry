"""CPU-only public CLI workflows, ownership boundaries, and failure regression."""

import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from xlayer_telemetry import cli
from xlayer_telemetry.operations.config import ConfigError, config_path, initialize, load_config, snapshot
from xlayer_telemetry.operations.health import process_identity, status


ROOT = Path(__file__).parents[1]


def invoke(config, *args, **kwargs):
    return subprocess.run([sys.executable, "-m", "xlayer_telemetry.cli", "--config", str(config), *args],
                          cwd=ROOT, capture_output=True, text=True, timeout=20, **kwargs)


@pytest.mark.parametrize("args", [[], ["--help"], ["up", "--help"], ["run", "--help"], ["config", "--help"]])
def test_help_does_not_require_config(args):
    result = invoke("/missing/config", *args)
    assert result.returncode == 0
    assert "usage:" in result.stdout


def test_init_keeps_existing_and_config_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / "private/config.conf"
    monkeypatch.setenv("XLAYER_CONFIG", str(config))
    assert config_path() == config
    assert config_path(str(tmp_path / "explicit")) != config
    assert cli.main(["init"]) == 0
    initial = config.read_text()
    assert not initialize(config)
    assert config.read_text() == initial
    assert config.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "telemetry/runs").is_dir()
    config.write_text(initial + "NODE_NAME=config-node\nGF_FEATURE_TOGGLES_ENABLE=''\n")
    monkeypatch.setenv("NODE_NAME", "env-node")
    values, command = load_config(config)
    assert values["NODE_NAME"] == "env-node"
    assert values["GF_FEATURE_TOGGLES_ENABLE"] == ""
    assert command == []
    assert cli.main(["config", "validate"]) == 0


@pytest.mark.parametrize("content", ["RUN_ID='bad space'\n", "ENABLE_LOGS=maybe\n", "RUN_ROOT=relative\n",
                                     "VERL_COMMAND=not-an-array\n", "if broken syntax\n",
                                     "EXECUTION_MODE=wrong\n", "TELEMETRY_METRICS_MAX_AGE_SECONDS=nan\n"])
def test_invalid_config_is_actionable_and_json_safe(tmp_path, content):
    path = tmp_path / "config"
    path.write_text(content)
    result = invoke(path, "doctor", "--json")
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "error"
    assert "xltel:" in result.stderr
    assert "Traceback" not in result.stderr


def test_missing_config_and_config_stdout_do_not_pollute_json(tmp_path):
    missing = invoke(tmp_path / "missing", "status", "--json")
    assert missing.returncode == 2
    assert "xltel init" in missing.stderr
    path = tmp_path / "config"
    path.write_text(f"echo secret-output\nTELEMETRY_HOME='{tmp_path}'\nENABLE_GPU_METRICS=0\n")
    result = invoke(path, "doctor", "--json")
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert any(c["action"] == "Run xltel install-tools." for c in report["checks"])
    assert "secret-output" not in result.stdout + result.stderr


def test_config_snapshot_quotes_values_and_has_private_permissions(tmp_path):
    config = tmp_path / "config"
    config.write_text(f"TELEMETRY_HOME={str(tmp_path)!r}\nVERL_COMMAND=('python' 'arg with spaces')\n")
    values, command = load_config(config)
    values["TELEMETRY_LOG_ROOTS"] = 'verl=/tmp/a b;$(echo must-not-run)'
    saved = snapshot(values)
    restored, empty = load_config(saved)
    assert restored == values
    assert command == ["python", "arg with spaces"]
    assert empty == []
    assert saved.stat().st_mode & 0o777 == 0o600


def test_status_checks_identity_health_and_freshness(tmp_path, monkeypatch):
    config_path_ = tmp_path / "config"
    config_path_.write_text(f"TELEMETRY_HOME='{tmp_path}'\nENABLE_GPU_METRICS=0\n")
    config, _ = load_config(config_path_)
    directory = tmp_path / "state/verl-local"
    directory.mkdir(parents=True)
    fields = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(") ", 1)[1].split()
    identity = f"{os.getpid()} {fields[19]} {Path('/proc/sys/kernel/random/boot_id').read_text().strip()}"
    for role in ("server", "node"):
        (directory / f"{role}.pid").write_text(identity)
    assert process_identity(directory / "server.pid")["process"] == "running"
    run = tmp_path / "runs/r1"
    (run / "telemetry-metrics").mkdir(parents=True)
    (run / "telemetry-manifest.json").write_text(json.dumps({"run_id": "r1", "configuration": {"execution_mode": "async"}}))
    (run / "telemetry-metrics/verl-trainer-driver.json").write_text(json.dumps({"step": 124, "observed_at": time.time()-3600}))
    from xlayer_telemetry.operations import health
    monkeypatch.setattr(health, "probe", lambda url, **kw: {"health": "healthy", "data":
        {"database": "ok", "data": {"activeTargets": [{"labels": {"job": "telemetry", "cluster": "training-cluster", "nodename": "gpu-local"}, "health": "up"}]}}})
    result = status(config)
    assert result["status"] == "healthy"
    assert result["metrics"]["verl"]["health"] == "stale"
    assert result["latest_run"]["step"] == 124
    (directory / "server.pid").write_text(f"{os.getpid()} {fields[19]} old-boot")
    assert status(config)["status"] == "degraded"
    assert process_identity(directory / "server.pid")["process"] == "stopped"
    monkeypatch.setattr(health, "probe", lambda *a, **kw: {"health": "unreachable", "data": None})
    assert status(config)["services"]["node"]["health"] == "unreachable"


def test_actual_cli_run_inspect_exit_code_and_argv(tmp_path):
    config = tmp_path / "config"
    config.write_text(f"TELEMETRY_HOME='{tmp_path}'\nNODE_NAME=cpu-test\n")
    workload = tmp_path / "workload.py"
    workload.write_text("import os,sys,json\n"
                        "open(os.environ['VERL_FILE_LOGGER_PATH'],'w').write(json.dumps({'step':7,'data':{'timing_s/gen':2.4,'perf/time_per_step':8.0}})+'\\n')\n"
                        "assert sys.argv[1:] == ['literal;$(echo bad)', 'with space']\n"
                        "sys.exit(7)\n")
    result = invoke(config, "run", "--mode", "async", "--run-id", "smoke", "--", sys.executable, str(workload),
                    "literal;$(echo bad)", "with space")
    assert result.returncode == 7, result.stderr
    run = tmp_path / "runs/smoke"
    manifest = json.loads((run / "telemetry-manifest.json").read_text())
    assert manifest["configuration"]["execution_mode"] == "async"
    sample = json.loads(next((run / "telemetry-metrics").glob("*.json")).read_text())
    assert sample["step"] == 7
    assert (run / "telemetry-health.json").is_file()
    inspected = invoke(config, "inspect")
    assert inspected.returncode == 0, inspected.stderr
    assert "smoke" in inspected.stdout
    assert invoke(config, "inspect", "smoke").returncode == 0
    collision = invoke(config, "run", "--run-id", "smoke", "--", sys.executable, "-c", "pass")
    assert collision.returncode != 0


@pytest.mark.parametrize("sig,exit_code", [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_cli_run_signal_cleanup_preserves_unrelated_process(tmp_path, sig, exit_code):
    config = tmp_path / "config"
    config.write_text(f"TELEMETRY_HOME='{tmp_path}'\n")
    marker = tmp_path / "pid"
    launcher = tmp_path / "launcher.py"
    launcher.write_text("import subprocess,time\nfrom pathlib import Path\n"
                        f"p=subprocess.Popen(['sleep','60']);Path({str(marker)!r}).write_text(str(p.pid))\ntime.sleep(60)\n")
    unrelated = subprocess.Popen(["sleep", "60"])
    output = tmp_path / "output.log"
    with output.open("w") as log:
        child = subprocess.Popen([sys.executable, "-m", "xlayer_telemetry.cli", "--config", str(config), "run", "--run-id", "signal", "--", sys.executable, str(launcher)],
                                 cwd=ROOT, stdout=log, stderr=log)
        try:
            deadline = time.monotonic()+10
            while not marker.exists() and time.monotonic()<deadline:
                time.sleep(.05)
            assert marker.exists(), output.read_text()
            child.send_signal(sig)
            assert child.wait(timeout=10) == exit_code, output.read_text()
            assert unrelated.poll() is None
            proc = Path(f"/proc/{marker.read_text()}/stat")
            assert not proc.exists() or proc.read_text().rsplit(") ", 1)[1].split()[0] == "Z"
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            unrelated.terminate()
            unrelated.wait()


def test_cli_lifecycle_is_idempotent_and_handles_partial_failure(tmp_path, monkeypatch, capsys):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "verl_local.sh"
    shutil.copy2(ROOT / "scripts/verl_local.sh", script)
    (scripts / "run_telemetry.sh").write_text('''#!/usr/bin/env bash
role=$1
if [[ $role == node && ${FAKE_NODE_FAIL:-0} == 1 ]]; then exit 1; fi
touch "$MARKER_DIR/$role.ready"
if [[ $role == server ]]; then echo 'Monitoring server ready:'; fi
trap 'touch "$MARKER_DIR/$role.stopped"; exit 0' TERM
while true; do sleep 0.1; done
''')
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text('#!/usr/bin/env bash\n[[ -f "$MARKER_DIR/node.ready" ]]\n')
    curl.chmod(0o755)
    config_path_ = tmp_path / "config"
    config_path_.write_text(f"RUN_ID=background-test\nTELEMETRY_HOME='{tmp_path / 'telemetry'}'\n")
    monkeypatch.setenv("TELEMETRY_PYTHON", sys.executable)
    monkeypatch.setenv("MARKER_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setattr(cli, "assets_root", lambda: script.parent.parent)
    config_path_.write_text(config_path_.read_text() + "ENABLE_GPU_METRICS=0\n")
    class Backend(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = {"database": "ok", "status": "success", "data": {"activeTargets": [
                {"labels": {"job": "telemetry", "cluster": "training-cluster", "nodename": "gpu-local"}, "health": "up"}]}}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
        def log_message(self, *args):
            pass
    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{backend.server_port}"
    config_path_.write_text(config_path_.read_text() + f"PROMETHEUS_URL={url}\nGRAFANA_URL={url}\n")
    arguments = ["--config", str(config_path_)]
    try:
        assert cli.main(arguments + ["up"]) == 0
        assert cli.main(arguments + ["up"]) == 0
        capsys.readouterr()
        assert cli.main(arguments + ["status", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "healthy"
        assert cli.main(arguments + ["restart"]) == 0
        assert cli.main(arguments + ["down"]) == 0
        assert cli.main(arguments + ["down"]) == 0
        assert not (tmp_path / "telemetry/state/verl-local/server.pid").exists()
        # A reused PID record must never terminate this unrelated pytest process.
        pid = tmp_path / "telemetry/state/verl-local/server.pid"
        pid.write_text(f"{os.getpid()} 0 stale-boot\n")
        assert cli.main(arguments + ["down"]) == 0
        assert not pid.exists()
        monkeypatch.setenv("FAKE_NODE_FAIL", "1")
        assert cli.main(arguments + ["up"]) == 1
        assert not (tmp_path / "telemetry/state/verl-local/server.pid").exists()
    finally:
        cli.main(arguments + ["down"])
        backend.shutdown()
        backend.server_close()
        thread.join()


def test_configured_command_redaction_and_custom_output_inspection(tmp_path):
    path = tmp_path / "config"
    command = "import os,json;open(os.environ['VERL_FILE_LOGGER_PATH'],'w').write(json.dumps({'step':2,'data':{'perf/time_per_step':1}})+'\\n')"
    import shlex
    path.write_text(f"TELEMETRY_HOME={shlex.quote(str(tmp_path))}\n"
                    f"VERL_COMMAND=({shlex.quote(sys.executable)} -c {shlex.quote(command)})\n")
    shown = invoke(path, "config", "show")
    assert "VERL_FILE_LOGGER_PATH" not in shown.stdout
    assert json.loads(shown.stdout)["VERL_COMMAND"].startswith("<3 arguments")
    output = tmp_path / "custom/output"
    assert invoke(path, "run", "--output", str(output)).returncode == 0
    inspected = invoke(path, "inspect")
    assert inspected.returncode == 0
    assert str(output) in inspected.stdout
    assert invoke(path, "sources", "--json").returncode == 0


def test_trusted_config_exports_reach_children_but_are_not_disclosed(tmp_path):
    path = tmp_path / "config"
    path.write_text(f"TELEMETRY_HOME='{tmp_path}'\nexport XLAYER_TEST_PRIVATE_VALUE=secret-for-test\n")
    values, _ = load_config(path)
    assert values["XLAYER_TEST_PRIVATE_VALUE"] == "secret-for-test"
    assert "secret-for-test" not in snapshot(values).read_text()
    assert "secret-for-test" not in invoke(path, "config", "show").stdout
    program = "import os; assert os.environ['XLAYER_TEST_PRIVATE_VALUE']=='secret-for-test'"
    result = invoke(path, "run", "--", sys.executable, "-c", program)
    assert result.returncode == 0, result.stderr


def test_status_json_with_real_unavailable_backend_and_logs(tmp_path):
    config = tmp_path / "config"
    config.write_text(f"TELEMETRY_HOME='{tmp_path}'\nENABLE_GPU_METRICS=0\nPROMETHEUS_URL=http://127.0.0.1:1\nGRAFANA_URL=http://127.0.0.1:1\n")
    result = invoke(config, "status", "--json")
    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data["services"]["prometheus"]["health"] == "unreachable"
    assert data["metrics"]["gpu"]["health"] == "disabled"
    assert invoke(config, "logs").returncode == 2
    logs = tmp_path / "state/verl-local"
    logs.mkdir(parents=True)
    (logs / "server.log").write_text("one\ntwo\nthree\n")
    result = invoke(config, "logs", "server", "-n", "2")
    assert result.returncode == 0
    assert result.stdout == "two\nthree\n"


def test_status_json_real_http_backend(tmp_path):
    class Backend(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = {"database": "ok", "status": "success", "data": {"activeTargets": []}}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        config = tmp_path / "config"
        config.write_text(f"TELEMETRY_HOME='{tmp_path}'\nENABLE_GPU_METRICS=0\nPROMETHEUS_URL={url}\nGRAFANA_URL={url}\n")
        result = invoke(config, "status", "--json")
        assert result.returncode == 1  # reachable services are not managed process ownership
        assert json.loads(result.stdout)["services"]["grafana"]["health"] == "healthy"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
