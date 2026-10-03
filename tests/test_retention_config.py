"""Retain evidence for longer runs without changing the existing default."""

import json
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys

import pytest

from xlayer_telemetry.operations.config import ConfigError, load_config, snapshot


ROOT = Path(__file__).parents[1]


@pytest.fixture(autouse=True)
def isolated_retention(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_RETENTION", raising=False)


@pytest.mark.parametrize("extension", ["toml", "conf"])
def test_retention_default_and_environment_precedence(tmp_path, monkeypatch, extension):
    path = tmp_path / ("config." + extension)
    path.write_text("[telemetry]\n" if extension == "toml" else "")
    config, _ = load_config(path)
    assert config["PROMETHEUS_RETENTION"] == "1d"

    path.write_text('[telemetry]\nPROMETHEUS_RETENTION = "7d"\n' if extension == "toml"
                    else "PROMETHEUS_RETENTION=7d\n")
    assert load_config(path)[0]["PROMETHEUS_RETENTION"] == "7d"
    monkeypatch.setenv("PROMETHEUS_RETENTION", "10h")
    config, _ = load_config(path)
    assert config["PROMETHEUS_RETENTION"] == "10h"
    config["TELEMETRY_HOME"] = str(tmp_path)
    assert "PROMETHEUS_RETENTION=10h\n" in snapshot(config).read_text()


@pytest.mark.parametrize("value", ["10h", "7d", "1h30m", "2w3d4h", "1500ms"])
def test_retention_accepts_prometheus_duration(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text('[telemetry]\nPROMETHEUS_RETENTION = ' + json.dumps(value) + '\n')
    assert load_config(path)[0]["PROMETHEUS_RETENTION"] == value


@pytest.mark.parametrize("value", ["", "0", "0d", "-1d", "1.5d", "7", "1m1h", "1h1h", "300y", "1d\n"])
def test_invalid_retention_rejected_before_runtime(tmp_path, monkeypatch, value):
    path = tmp_path / "config.toml"
    path.write_text("[telemetry]\n")
    monkeypatch.setenv("PROMETHEUS_RETENTION", value)
    with pytest.raises(ConfigError, match="PROMETHEUS_RETENTION.*7d"):
        load_config(path)


@pytest.mark.parametrize("command", [["config", "validate"], ["doctor", "--json"]])
def test_cli_reports_invalid_retention_with_fix(tmp_path, command):
    path = tmp_path / "config.toml"
    path.write_text('[telemetry]\nPROMETHEUS_RETENTION = "1h1d"\n')
    result = subprocess.run([sys.executable, "-m", "xlayer_telemetry.cli", "--config", str(path), *command],
                            cwd=ROOT, text=True, capture_output=True, timeout=10)
    assert result.returncode == 2
    assert "PROMETHEUS_RETENTION" in result.stderr and "7d" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("mode,value", [("default", "1d"), ("direct", "1h30m"), ("legacy", "7d")])
def test_server_passes_retention_to_prometheus_binary(tmp_path, mode, value):
    tools = tmp_path / "tools"
    arch = "arm64" if platform.machine() in {"aarch64", "arm64"} else "amd64"
    prometheus = tools / f"prometheus-3.5.0.linux-{arch}/prometheus"
    grafana = tools / "grafana-v12.1.0/bin/grafana"
    argv = tmp_path / "prometheus-argv.json"
    prometheus.parent.mkdir(parents=True)
    grafana.parent.mkdir(parents=True)
    prometheus.write_text(f"#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\n"
                          f"Path({str(argv)!r}).write_text(json.dumps(sys.argv[1:]))\n")
    grafana.write_text("#!/usr/bin/env bash\nexit 0\n")
    prometheus.chmod(0o755)
    grafana.chmod(0o755)
    python = tmp_path / "python"
    capture_wait = ("import sys,time\nfrom pathlib import Path\n"
                    f"path=Path({str(argv)!r})\n"
                    "for _ in range(200):\n"
                    "    if path.exists(): break\n"
                    "    time.sleep(.01)\n"
                    "sys.exit(17)\n")
    python.write_text("#!/usr/bin/env bash\n"
                      'if [[ "$1" == -m && "$2" == xlayer_telemetry.stack ]]; then\n'
                      f"  exec {shlex.quote(sys.executable)} -c {shlex.quote(capture_wait)}\nfi\n"
                      f"exec {shlex.quote(sys.executable)} \"$@\"\n")
    python.chmod(0o755)
    config = tmp_path / "server.conf"
    config.write_text(f"RUN_ID=fixture\nTELEMETRY_HOME={shlex.quote(str(tmp_path))}\n"
                      f"TOOLS_DIR={shlex.quote(str(tools))}\nENABLE_LOGS=0\n"
                      f"TELEMETRY_PYTHON={shlex.quote(str(python))}\n"
                      + (f"PROMETHEUS_RETENTION={value}\n" if mode != "default" else ""))
    environment = os.environ | {"PYTHON": str(python), "OUTPUT_DIR": str(tmp_path / "output"),
                                "TELEMETRY_TARGETS": "fixture=127.0.0.1"}
    environment.pop("SERVER_CONFIG_ONLY", None)
    command = ["bash", str(ROOT / "scripts/run_telemetry.sh"), "server", "--config", str(config)]
    if mode == "legacy":
        command = ["bash", str(ROOT / "scripts/verl_local.sh"), "--config", str(config), "server"]
    result = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True, timeout=15)
    # The fake readiness probe deliberately stops the launcher after argv capture.
    assert result.returncode == 17, result.stderr
    assert f"--storage.tsdb.retention.time={value}" in json.loads(argv.read_text())


def test_legacy_server_rejects_invalid_retention_before_startup(tmp_path):
    environment = os.environ | {"PYTHON": sys.executable, "SERVER_CONFIG_ONLY": "1",
                                "OUTPUT_DIR": str(tmp_path), "PROMETHEUS_RETENTION": "1d1d",
                                "TELEMETRY_TARGETS": "fixture=127.0.0.1"}
    result = subprocess.run(["bash", str(ROOT / "scripts/run_telemetry.sh"), "server"],
                            cwd=ROOT, env=environment, text=True, capture_output=True, timeout=10)
    assert result.returncode == 2
    assert "PROMETHEUS_RETENTION" in result.stderr and "7d" in result.stderr
    assert not (tmp_path / "prometheus.yml").exists()
