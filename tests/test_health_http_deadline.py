"""Health and source discovery bound complete HTTP exchanges, not idle reads."""

from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from tests._process_helpers import child_processes
from xlayer_telemetry.operations.health import probe
from xlayer_telemetry.source_discovery import build_file_discovery
from xlayer_telemetry.subsystems import inspect_sources


@pytest.fixture
def backend():
    body = json.dumps({"status": "success", "data": {"activeTargets": []}}).encode()
    state = {"mode": "ok", "requests": [], "size": 4 * 1024 * 1024 + 1}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state["requests"].append(self.path)
            mode = "ok" if self.path == "/ok" else state["mode"]
            try:
                if mode == "status":
                    self.wfile.write(b"private-token invalid HTTP status\r\n\r\n")
                    return
                if mode == "headers":
                    self.wfile.write(b"HTTP/1.0 200 OK\r\nX-Private: ")
                    for _ in range(60):
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(.05)
                    self.wfile.write(b"\r\n\r\n" + body)
                    return
                self.send_response(503 if mode == "error" else 302 if mode == "redirect" else 200)
                if mode == "chunks":
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(b"2\r\n{}\r\nprivate-token\r\n")
                    return
                if mode == "redirect":
                    self.send_header("Location", "/ok")
                payload = (b" " * state["size"] if mode in {"oversized", "redirect"} else body)
                size = len(payload) + (60 if mode == "trickle" else 1 if mode == "truncated" else 0)
                self.send_header("Content-Length", str(size))
                self.end_headers()
                if mode == "trickle":
                    for _ in range(60):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(.05)
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def sources(url, timeout=2):
    groups = build_file_discovery({"schema_version": 1, "sources": [
        {"name": "engine", "kind": "vllm", "target": "node:8000"}]})
    return inspect_sources(groups, url, "http://grafana", "training", timeout=timeout)


@pytest.mark.parametrize("json_body", [False, True])
def test_health_trickling_body_has_total_deadline_and_recovers(backend, json_body):
    state, url = backend
    state["mode"] = "trickle"
    before = {child.pid for child in child_processes(os.getpid())}
    started = time.monotonic()
    result = probe(url, json_body=json_body)
    assert time.monotonic() - started < 2.8
    assert result == {"health": "unreachable", "data": None}
    assert {child.pid for child in child_processes(os.getpid())} <= before
    assert probe(url + "/ok", json_body=json_body)["health"] == "healthy"


@pytest.mark.parametrize("stage", ["trickle", "headers"])
def test_source_discovery_has_total_deadline_and_recovers(backend, stage):
    state, url = backend
    state["mode"] = stage
    before = {child.pid for child in child_processes(os.getpid())}
    started = time.monotonic()
    result = sources(url, timeout=.4)
    assert time.monotonic() - started < 1.2
    assert result["backend_error"] == "TimeoutError"
    assert result["sources"][0]["status"] == "unavailable"
    assert {child.pid for child in child_processes(os.getpid())} <= before
    state["mode"] = "ok"
    assert sources(url)["backend_error"] is None


@pytest.mark.parametrize("mode", ["status", "chunks", "truncated"])
def test_malformed_http_never_reports_healthy_or_escapes(backend, mode):
    state, url = backend
    state["mode"] = mode
    assert probe(url, json_body=False) == {"health": "unreachable", "data": None}
    assert probe(url, json_body=True) == {"health": "unreachable", "data": None}
    result = sources(url)
    assert result["backend_error"] in {"ConnectionError", "OSError"}
    assert result["sources"][0]["status"] == "unavailable"
    assert "private-token" not in json.dumps(result)


@pytest.mark.parametrize("mode", ["oversized", "redirect"])
def test_health_and_discovery_keep_four_mib_response_cap(backend, mode):
    state, url = backend
    state["mode"] = mode
    assert probe(url)["health"] == "unreachable"
    result = sources(url)
    assert result["sources"][0]["status"] == "unavailable"
    assert result["backend_error"] == "ValueError"
    assert "/ok" not in state["requests"]
    state["mode"] = "ok"
    assert probe(url)["health"] == "healthy"


def test_concurrent_health_probes_keep_independent_deadlines(backend):
    state, url = backend
    state["mode"] = "trickle"
    before = {child.pid for child in child_processes(os.getpid())}
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(probe, [url, url + "/ok", url]))
    assert time.monotonic() - started < 2.8
    assert [result["health"] for result in results] == ["unreachable", "healthy", "unreachable"]
    assert {child.pid for child in child_processes(os.getpid())} <= before


def test_health_imports_remain_lightweight():
    result = subprocess.run([sys.executable, "-c", "import sys; "
        "import xlayer_telemetry; assert 'xlayer_telemetry._http_transport' not in sys.modules; "
        "import xlayer_telemetry.operations.health; import xlayer_telemetry.subsystems; "
        "assert not any(name.startswith('xlayer_telemetry.analysis') for name in sys.modules)"],
        capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_spawn_failure_is_an_unavailable_health_result(monkeypatch):
    from xlayer_telemetry import _http_transport as transport

    def unavailable(*args, **kwargs):
        raise OSError("private process failure")
    monkeypatch.setattr(transport.subprocess, "Popen", unavailable)
    assert probe("http://unused")["health"] == "unreachable"
    result = sources("http://unused")
    assert result["backend_error"] == "OSError"
    assert "private process failure" not in json.dumps(result)


def test_unreaped_worker_blocks_health_replacement_and_recovers(monkeypatch, backend):
    from xlayer_telemetry import _http_transport as transport

    class Process:
        exited = False
        def poll(self):
            return 0 if self.exited else None
    worker = Process()
    monkeypatch.setattr(transport, "_UNREAPED", [worker])
    state, url = backend
    assert probe(url)["health"] == "unreachable"
    assert sources(url)["backend_error"] == "RuntimeError"
    assert state["requests"] == []
    worker.exited = True
    assert probe(url)["health"] == "healthy"
    assert sources(url)["backend_error"] is None
    assert transport._UNREAPED == []


def test_http_error_keeps_status_code_without_private_response_data(backend):
    from urllib.error import HTTPError
    from xlayer_telemetry._http_transport import request_bytes

    state, url = backend
    state["mode"] = "error"
    with pytest.raises(HTTPError) as caught:
        request_bytes(url + "/private-token", None, 2)
    assert caught.value.code == 503
    assert "private-token" not in str(caught.value)
    assert caught.value.url == ""
    assert not caught.value.headers
    assert sources(url)["backend_error"] == "HTTPError"


@pytest.mark.parametrize("mode", ["status", "chunks", "truncated"])
def test_protocol_exceptions_are_sanitized_os_errors(backend, mode):
    from xlayer_telemetry._http_transport import request_bytes

    state, url = backend
    state["mode"] = mode
    with pytest.raises(OSError) as caught:
        request_bytes(url + "/private-token", None, 2)
    assert str(caught.value) == "HTTP transport failed"
    assert caught.value.__cause__ is None
    assert not hasattr(caught.value, "partial")


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_source_deadline_is_unavailable_without_request(backend, timeout):
    state, url = backend
    assert sources(url, timeout=timeout)["backend_error"] == "TimeoutError"
    assert state["requests"] == []


def test_blocked_dns_is_terminated_at_deadline(monkeypatch, tmp_path):
    from xlayer_telemetry import _http_transport as transport

    # Inject a stuck resolver in the isolated interpreter, not the caller.
    (tmp_path / "sitecustomize.py").write_text("import socket, time\n"
        "def blocked(*args, **kwargs):\n    time.sleep(30)\n"
        "socket.getaddrinfo = blocked\n")
    popen = transport.subprocess.Popen
    def slow_dns(argv, **kwargs):
        kwargs["env"] = os.environ | {"PYTHONPATH": str(tmp_path) + os.pathsep + os.getcwd()}
        return popen(argv, **kwargs)
    monkeypatch.setattr(transport.subprocess, "Popen", slow_dns)
    before = {child.pid for child in child_processes(os.getpid())}
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="deadline"):
        transport.request_bytes("http://xlayer.invalid", None, .4)
    assert time.monotonic() - started < 1.2
    assert {child.pid for child in child_processes(os.getpid())} <= before
