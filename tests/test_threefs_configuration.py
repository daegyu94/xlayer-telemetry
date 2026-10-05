"""3FS config validation and isolated public CLI authentication boundaries."""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading

import pytest

from xlayer_telemetry import cli
from xlayer_telemetry.analysis.diagnostics import ThreeFSClient, _DeadlineThreeFSClient

ROOT = Path(__file__).parents[1]
USER_ENV = "XLAYER_TEST_3FS_USER"
PASSWORD_ENV = "XLAYER_TEST_3FS_PASSWORD"


def write_config(root, kind="toml", credentials=None):
    diagnosis = root / "diagnostics.json"
    settings = {"TELEMETRY_HOME": str(root / "home"), "ENABLE_GPU_METRICS": "0",
                "DIAGNOSTICS_CONFIG": str(diagnosis)}
    config = root / ("config.toml" if kind == "toml" else "config.conf")
    if kind == "toml":
        content = "[telemetry]\n" + "".join(f"{k}={json.dumps(v)}\n" for k, v in settings.items())
        content += "[environment]\n" + "".join(f"{k}={json.dumps(v)}\n" for k, v in (credentials or {}).items())
    else:
        content = "".join(f"{k}={shlex.quote(v)}\n" for k, v in settings.items())
        content += "".join(f"export {k}={shlex.quote(v)}\n" for k, v in (credentials or {}).items())
    config.write_text(content)
    return config, diagnosis


def write_diagnosis(path, settings):
    path.write_text(json.dumps({"schema_version": 1, "prometheus": {"url": "http://unused"}, **settings}))


def invoke(config, *args, environment=None):
    # No personal config, credentials, endpoint overrides, or run state.
    env = {"PATH": os.environ["PATH"], "HOME": str(config.parent),
           "PYTHONPATH": str(ROOT), "LANG": "C.UTF-8"}
    return subprocess.run([sys.executable, "-m", "xlayer_telemetry.cli", "--config", str(config), *args],
                          cwd=config.parent, env=env | (environment or {}), capture_output=True,
                          text=True, timeout=15)


@pytest.fixture
def authenticated_backend():
    state = {"expected": "", "authorized": [], "queries": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["queries"].append(self.rfile.read(int(self.headers["Content-Length"])).decode())
            authorized = self.headers.get("Authorization") == state["expected"]
            state["authorized"].append(authorized)
            self.send_response(200 if authorized else 401)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("kind", ["toml", "bash"])
@pytest.mark.parametrize("terminal", [False, True, "empty_password"])
def test_threefs_cli_uses_resolved_credentials(tmp_path, authenticated_backend, kind, terminal):
    url, state = authenticated_backend
    credentials = {USER_ENV: "fixture-config-reader", PASSWORD_ENV: "fixture-config-secret"}
    environment = ({USER_ENV: "fixture-terminal-reader", PASSWORD_ENV: "fixture-terminal-secret"}
                   if terminal else {})
    if terminal == "empty_password":
        environment[PASSWORD_ENV] = ""
    expected = credentials | environment
    state["expected"] = "Basic " + base64.b64encode(
        f"{expected[USER_ENV]}:{expected[PASSWORD_ENV]}".encode()).decode()
    config, diagnosis = write_config(tmp_path, kind, credentials)
    write_diagnosis(diagnosis, {"threefs": {"url": url, "user_env": USER_ENV, "password_env": PASSWORD_ENV}})

    result = invoke(config, "sources", "--json", "threefs", environment=environment)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == payload["counter_status"] == "no_data"
    assert payload["missing_sources"] == []
    assert state["authorized"] == [True, True]
    assert "FROM 3fs.distributions" in state["queries"][0]
    assert "FROM 3fs.counters" in state["queries"][1]
    shown = invoke(config, "config", "show", environment=environment)
    assert shown.returncode == 0
    for secret in (*credentials.values(), *environment.values(), state["expected"]):
        if secret:
            assert secret not in result.stdout + result.stderr + shown.stdout + shown.stderr
    assert not (tmp_path / "home").exists()


def test_threefs_cli_does_not_mutate_process_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(USER_ENV, raising=False)
    monkeypatch.delenv(PASSWORD_ENV, raising=False)
    credentials = {USER_ENV: "fixture-reader", PASSWORD_ENV: "fixture-secret"}
    config, diagnosis = write_config(tmp_path, credentials=credentials)
    write_diagnosis(diagnosis, {"threefs": {"url": "http://unused", "user_env": USER_ENV,
                                            "password_env": PASSWORD_ENV}})
    requests = []

    def read_body(self, request):
        requests.append(request.get_header("Authorization"))
        assert "fixture-secret" not in repr(self)
        return b""

    monkeypatch.setattr(_DeadlineThreeFSClient, "_read_body", read_body)
    before = dict(os.environ)
    assert cli.main(["--config", str(config), "sources", "--json", "threefs"]) == 0
    # Compare keys only in assertion diagnostics, never unrelated secret values.
    assert {key for key in set(os.environ) | set(before)
            if os.environ.get(key) != before.get(key)} == set()
    expected = "Basic " + base64.b64encode(b"fixture-reader:fixture-secret").decode()
    assert requests == [expected, expected]
    assert "fixture-secret" not in capsys.readouterr().out


@pytest.mark.parametrize("settings", [
    {"url": None}, {"url": 123}, {"url": " "},
    {"database": 123}, {"database": []}, {"database": "bad-name"},
    {"filters": []}, {"filters": ["host"]}, {"filters": "host"},
    {"filters": {"host": 123}}, {"filters": {"host": "ok", 1: "invalid"}},
    {"user_env": []}, {"user_env": None}, {"user_env": ""}, {"user_env": "invalid name"},
    {"password_env": []}, {"password_env": "bad=name"},
    {"timeout": 0}, {"timeout": "1"}, {"timeout": float("inf")},
])
def test_threefs_client_rejects_invalid_shapes_as_value_error(settings):
    with pytest.raises(ValueError):
        ThreeFSClient(**({"url": "http://unused"} | settings))


@pytest.mark.parametrize("settings", [
    {"database": "training"}, {"url": 123}, {"url": ""},
    {"url": "http://unused", "filters": []},
    {"url": "http://unused", "filters": ["host"]},
    {"url": "http://unused", "filters": {"host": 1}},
    {"url": "http://unused", "filters": {"private-fixture-key": "private-fixture-value"}},
    {"url": "http://unused", "user_env": []},
    {"url": "http://unused", "user_env": "invalid name"},
    {"url": "http://unused", "password_env": None},
    {"url": "http://unused", "database": []},
    {"url": "http://unused", "timeout_seconds": "5"},
    [],
])
def test_threefs_cli_rejects_invalid_config_before_query(tmp_path, settings):
    config, diagnosis = write_config(tmp_path)
    write_diagnosis(diagnosis, {"threefs": settings})
    for args in [("config", "validate"), ("sources", "--json", "threefs")]:
        result = invoke(config, *args)
        assert result.returncode == 2, result.stdout + result.stderr
        assert "ValueError" in result.stderr
        assert "Traceback" not in result.stderr
        assert "private-fixture" not in result.stdout + result.stderr
        if "--json" in args:
            assert json.loads(result.stdout)["status"] == "error"


@pytest.mark.parametrize("settings", [{}, {"threefs": None}, {"threefs": {}}])
def test_threefs_cli_preserves_unconfigured_optional_source(tmp_path, settings):
    config, diagnosis = write_config(tmp_path)
    write_diagnosis(diagnosis, settings)
    assert invoke(config, "config", "validate").returncode == 0
    result = invoke(config, "sources", "--json", "threefs")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"status": "not_configured"}
