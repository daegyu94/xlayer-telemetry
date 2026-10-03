"""Declarative config, static completion and independently owned host roles."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from xlayer_telemetry import cli
from xlayer_telemetry.operations import health
from xlayer_telemetry.operations.completion import generate
from xlayer_telemetry.operations.config import KEYS, ConfigError, config_path, initialize, load_config, migrate, snapshot

ROOT = Path(__file__).parents[1]


def test_default_toml_and_legacy_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XLAYER_CONFIG", raising=False)
    path = config_path()
    assert path.name == "config.toml"
    assert initialize(path)
    values, command = load_config(path)
    assert values["TELEMETRY_HOME"] == str(tmp_path / "telemetry")
    assert command == []
    assert not initialize(path)
    legacy = path.with_suffix(".conf")
    legacy.write_text("RUN_ID=legacy\n")
    assert config_path() == legacy
    monkeypatch.setenv("XLAYER_CONFIG", str(path))
    assert config_path() == path
    assert config_path(str(legacy)) == legacy


def test_toml_is_declarative_and_preserves_argv_exports(tmp_path, monkeypatch, capsys):
    path = tmp_path / "config.toml"
    sentinel = tmp_path / "should-not-exist"
    command = [sys.executable, "-c", f"assert __import__('os').environ['XLAYER_TEST_ENV']=='parent'", f"$(touch {sentinel})"]
    path.write_text(f'[telemetry]\nTELEMETRY_HOME = {json.dumps(str(tmp_path))}\n'
                    'ENABLE_GPU_METRICS = false\nTELEMETRY_METRICS_MAX_AGE_SECONDS = 12.5\n'
                    'GF_FEATURE_TOGGLES_ENABLE = ""\n'
                    f'[workload]\ncommand = {json.dumps(command)}\n'
                    '[environment]\nXLAYER_TEST_ENV = "private"\n')
    monkeypatch.setenv("XLAYER_TEST_ENV", "parent")
    monkeypatch.setenv("NODE_NAME", "env-node")
    config, argv = load_config(path)
    assert config["NODE_NAME"] == "env-node"
    assert config["ENABLE_GPU_METRICS"] == "0"
    assert config["TELEMETRY_METRICS_MAX_AGE_SECONDS"] == "12.5"
    assert argv == command
    assert "private" not in snapshot(config).read_text()
    assert cli.main(["--config", str(path), "config", "show"]) == 0
    assert "private" not in capsys.readouterr().out
    result = subprocess.run([sys.executable, "-m", "xlayer_telemetry.cli", "--config", str(path), "run"],
                            cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert not sentinel.exists()
    assert list((tmp_path / "runs").glob("*/telemetry-manifest.json"))


@pytest.mark.parametrize("content", [
    '[unknown]\nx=1', '[telemetry]\nBOGUS="value"', '[telemetry]\nNODE_NAME=123',
    '[telemetry]\nENABLE_LOGS=2', '[telemetry]\nENABLE_LOGS=[true]',
    '[telemetry]\nRUN_ROOT="relative"', '[telemetry]\nTELEMETRY_METRICS_MAX_AGE_SECONDS=nan',
    '[workload]\ncommand="python"', '[workload]\ncommand=["python",1]',
    '[workload]\nother=[]', '[environment]\nNODE_NAME="bad"', '[environment]\nTOKEN=3',
    '[environment]\n"bad-key"="value"', '[telemetry]\nNODE_NAME="a"\nNODE_NAME="b"',
    '[telemetry]\nTELEMETRY_TARGETS="a=10.0.0.1,a=10.0.0.2"',
    '[telemetry]\nTELEMETRY_TARGETS="bad yaml=10.0.0.1"',
    '[telemetry]\nLOKI_LISTEN_ADDR="host:123"',
    'telemetry="string"', '[workload]\ncommand=["\\u0000"]',
])
def test_toml_invalid_schema_is_actionable(tmp_path, content):
    path = tmp_path / "config.toml"
    path.write_text(content)
    with pytest.raises(ConfigError):
        load_config(path)


def test_migration_preserves_legacy_and_resolved_values_not_secrets(tmp_path):
    path = tmp_path / "old.conf"
    original = (f"TELEMETRY_HOME={shlex.quote(str(tmp_path / 'home with space'))}\n"
                "ENABLE_GPU_METRICS=0\nVERL_COMMAND=('python' 'arg with spaces' 'literal $()')\n"
                "export XLAYER_TEST_TOKEN=secret\n")
    path.write_text(original)
    output = tmp_path / "config.toml"
    migrate(path, output)
    config, command = load_config(output)
    old, old_command = load_config(path)
    assert command == old_command
    assert all(config[k] == v for k, v in old.items() if k in KEYS)
    assert "secret" not in output.read_text()
    assert path.read_text() == original
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ConfigError, match="already exists"):
        migrate(path, output)
    with pytest.raises(ConfigError, match=".toml"):
        migrate(path, tmp_path / "wrong.conf")


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_completion_requires_no_config_and_has_nested_commands(shell):
    result = subprocess.run([sys.executable, "-m", "xlayer_telemetry.cli", "--config", "/missing/config", "completion", shell],
                            capture_output=True, text=True, cwd=ROOT, timeout=5)
    assert result.returncode == 0, result.stderr
    assert "migrate" in result.stdout and "refresh" in result.stdout and "--role" in result.stdout
    executable = shutil.which(shell)
    if executable:
        assert subprocess.run([executable, "-n"], input=result.stdout, text=True, capture_output=True).returncode == 0


@pytest.mark.parametrize("words,expected,absent", [
    (["xltel", ""], "up", "migrate"),
    (["xltel", "--config", "config", "config", ""], "migrate", "up"),
    (["xltel", "sources", ""], "refresh", "migrate"),
    (["xltel", "up", "--role", ""], "node", "refresh"),
    (["xltel", "run", "--mode", ""], "async", "node"),
    (["xltel", "run", "--", "python", ""], "", "up"),
])
def test_bash_completion_context(words, expected, absent):
    text = generate(cli.parser(), "bash")
    script = text + '\nCOMP_WORDS=(' + ' '.join(shlex.quote(w) for w in words) + ')\n'
    script += f'COMP_CWORD={len(words)-1}\n_xltel\nprintf "%s\\n" "${{COMPREPLY[@]}}"\n'
    result = subprocess.run(["bash"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout.splitlines()
    assert absent not in result.stdout.splitlines()


def test_role_specific_health_and_missing_remote_target(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(f'[telemetry]\nTELEMETRY_HOME={json.dumps(str(tmp_path))}\n'
                    'ENABLE_GPU_METRICS=false\nTELEMETRY_TARGETS="gpu-a=10.0.0.1,sandbox-a=10.0.0.2"\n'
                    'NODE_NAME="gpu-a"\n')
    config, _ = load_config(path)
    state = tmp_path / "state/verl-local"
    state.mkdir(parents=True)
    stat = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(") ", 1)[1].split()
    identity = f"{os.getpid()} {stat[19]} {Path('/proc/sys/kernel/random/boot_id').read_text().strip()}"
    (state / "server.pid").write_text(identity)
    targets = [{"labels": {"job": "telemetry", "cluster": "training-cluster", "nodename": name}, "health": "up"}
               for name in ("gpu-a", "sandbox-a")]
    monkeypatch.setattr(health, "probe", lambda *a, **kw: {"health": "healthy", "data":
                          {"database": "ok", "data": {"activeTargets": targets}}})
    result = health.status(config, role="server")
    assert result["status"] == "healthy"
    assert result["metrics"]["gpu"]["health"] == "not_applicable"
    assert health.status(config, role="all")["status"] == "degraded"
    targets.pop()
    result = health.status(config, role="server")
    assert result["status"] == "degraded"
    assert result["collector_targets"][1]["health"] == "not_discovered"
    (state / "server.pid").unlink()
    (state / "node.pid").write_text(identity)
    assert health.status(config, role="node")["status"] == "healthy"
    assert health.status(config, role="all")["status"] == "degraded"
    (state / "server.pid").write_text(identity)
    assert health.status(config, role="all")["status"] == "degraded"  # remote sandbox target missing
    monkeypatch.setattr(health, "assets_root", lambda: ROOT)
    result = health.doctor(config, role="node")
    names = {c["component"] for c in result["checks"]}
    assert "Node Exporter" in names and "Grafana" not in names


def test_toml_age_numeric_notation_and_size_limit(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[telemetry]\nTELEMETRY_METRICS_MAX_AGE_SECONDS=1e-7\n')
    config, _ = load_config(path)
    assert float(config["TELEMETRY_METRICS_MAX_AGE_SECONDS"]) == 1e-7
    path.write_text('#' + 'a' * (1024 * 1024))
    with pytest.raises(ConfigError, match="1 MiB"):
        load_config(path)


def test_server_ports_derive_urls_and_allow_explicit_remote_override(tmp_path, monkeypatch):
    path = tmp_path / 'config.toml'
    path.write_text('[telemetry]\nPROMETHEUS_PORT=39090\nGRAFANA_PORT=33000\nLOKI_PORT=33100\nALLOY_PORT=22345\n')
    config, _ = load_config(path)
    assert config['PROMETHEUS_URL'] == 'http://127.0.0.1:39090'
    assert config['GRAFANA_URL'] == 'http://127.0.0.1:33000'
    assert config['LOKI_URL'] == 'http://127.0.0.1:33100'
    assert config['ALLOY_PORT'] == '22345'
    monkeypatch.setenv('PROMETHEUS_URL', 'http://monitor.private:19090')
    assert load_config(path)[0]['PROMETHEUS_URL'] == 'http://monitor.private:19090'
    for value in ('0', '65536', '-1', 'true', '1.5'):
        path.write_text('[telemetry]\nPROMETHEUS_PORT=' + value + '\n')
        with pytest.raises(ConfigError):
            load_config(path)


def test_independent_role_lifecycle_and_rollback(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "scripts/verl_local.sh", scripts)
    (scripts / "run_telemetry.sh").write_text('''#!/usr/bin/env bash
role=$1
if [[ $role == node && ${FAKE_NODE_FAIL:-0} == 1 ]]; then exit 1; fi
printf '%s\\n' "${TELEMETRY_TARGETS:-}" "${LOKI_PUSH_URL:-}" > "$MARKER_DIR/$role.env"
touch "$MARKER_DIR/$role.ready"
if [[ $role == server ]]; then echo 'Monitoring server ready:'; fi
trap 'exit 0' TERM
while true; do sleep .1; done
''')
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text('#!/usr/bin/env bash\n[[ -f "$MARKER_DIR/node.ready" ]]\n')
    curl.chmod(0o755)
    path = tmp_path / "config.toml"
    path.write_text(f'[telemetry]\nTELEMETRY_HOME={json.dumps(str(tmp_path))}\n'
                    'ENABLE_GPU_METRICS=false\nENABLE_LOGS=true\nLOKI_URL="http://monitor.internal:13100"\n'
                    'TELEMETRY_TARGETS="a=10.0.0.1,b=10.0.0.2"\n')
    monkeypatch.setenv("MARKER_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setattr(cli, "assets_root", lambda: tmp_path)
    args = ["--config", str(path)]
    state = tmp_path / "state/verl-local"
    try:
        assert cli.main(args + ["up", "--role", "server"]) == 0
        assert (state / "server.pid").exists() and not (state / "node.pid").exists()
        server_id = (state / "server.pid").read_text()
        assert "a=10.0.0.1,b=10.0.0.2" in (tmp_path / "server.env").read_text()
        monkeypatch.setenv("FAKE_NODE_FAIL", "1")
        assert cli.main(args + ["up"]) == 1
        assert (state / "server.pid").read_text() == server_id
        assert not (state / "node.pid").exists()
        monkeypatch.delenv("FAKE_NODE_FAIL")
        assert cli.main(args + ["up", "--role", "node"]) == 0
        assert cli.main(args + ["up", "--role", "node"]) == 0
        assert "http://monitor.internal:13100/loki/api/v1/push" in (tmp_path / "node.env").read_text()
        assert cli.main(args + ["restart", "--role", "node"]) == 0
        assert (state / "server.pid").read_text() == server_id
        assert cli.main(args + ["down", "--role", "node"]) == 0
        assert (state / "server.pid").read_text() == server_id
        (state / "node.pid").write_text(f"{os.getpid()} 0 wrong-boot")
        assert cli.main(args + ["down", "--role", "node"]) == 0
        assert not (state / "node.pid").exists()
        assert cli.main(args + ["down", "--role", "server"]) == 0
    finally:
        cli.main(args + ["down"])


@pytest.mark.parametrize("value", ["0", "65536", "true", "1.5"])
def test_alloy_port_rejects_invalid_config(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text("[telemetry]\nALLOY_PORT=" + value + "\n")
    with pytest.raises(ConfigError, match="ALLOY_PORT"):
        load_config(path)


def test_unknown_toml_key_identifies_the_setting(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[telemetry]\nGRAFANA_PRT = 23000\n')
    with pytest.raises(ConfigError, match="GRAFANA_PRT"):
        load_config(path)
