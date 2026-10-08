"""Live control-plane checks must bound the complete HTTP exchange."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests._process_helpers import child_processes
from xlayer_telemetry import stack


@pytest.fixture
def endpoint():
    state = {"mode": "ok", "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state["requests"].append((self.path, self.headers.get("User-Agent")))
            self.send_response(503 if state["mode"] == "unavailable" else 200)
            self.end_headers()
            try:
                if state["mode"] == "trickle":
                    for _ in range(30):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(.03)
                body = b"x" * (8 * 1024 * 1024 + 1) if state["mode"] == "oversized" else b'{"database":"ok"}'
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        assert not worker.is_alive()


def test_readiness_trickle_respects_total_budget_and_reaps_worker(endpoint):
    state, url = endpoint
    state["mode"] = "trickle"
    before = {p.pid for p in child_processes(os.getpid())}
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="endpoint did not become ready"):
        stack._wait_until_ready(url + "/-/ready", .3)
    assert time.monotonic() - started < .8
    assert state["requests"]
    assert {p.pid for p in child_processes(os.getpid())} <= before
    state["mode"] = "ok"
    stack._wait_until_ready(url + "/-/ready", 3)
    assert state["requests"][-1][1] == "xlayer-telemetry-validation/1"


def test_readiness_preserves_http_error_and_bounds_attempt_timeout(endpoint):
    state, url = endpoint
    state["mode"] = "unavailable"
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="endpoint did not become ready.*503"):
        stack._wait_until_ready(url, .3)
    assert time.monotonic() - started < .8


def test_stack_request_caps_body_and_json_reads_use_bounded_transport(endpoint):
    state, url = endpoint
    assert stack._read_json(url) == {"database": "ok"}
    state["mode"] = "oversized"
    with pytest.raises(RuntimeError, match="response exceeds"):
        stack._request(url, 3)


def test_stack_backend_size_failure_still_writes_summary(tmp_path, monkeypatch):
    from argparse import Namespace
    monkeypatch.setattr(stack, "_wait_until_ready", lambda *args: None)
    def read(url):
        if "/api/health" in url:
            return {"database": "ok"}
        raise RuntimeError("HTTP response exceeds byte limit")
    monkeypatch.setattr(stack, "_read_json", read)
    output = tmp_path / "summary.json"
    args = Namespace(target_dir=None, prometheus_url="http://prom", grafana_url="http://grafana",
                     output=output, timeout=.01, require_targets_up=False)
    assert stack.validate_stack(args) == 1
    validation = json.loads(output.read_text())["validation"]
    assert not validation["stack_valid"]
    assert validation["errors"] == ["HTTP response exceeds byte limit"]


def test_standalone_clock_quality_uses_deadlines_without_changing_rule_client(endpoint, monkeypatch, capsys):
    import sys
    from xlayer_telemetry import prometheus
    from xlayer_telemetry.analysis import clock_quality

    state, url = endpoint
    state["mode"] = "trickle"
    # Shorten the existing client timeout without adding a public CLI option.
    initialize = prometheus.PrometheusClient.__init__
    monkeypatch.setattr(prometheus.PrometheusClient, "__init__",
                        lambda self, url: initialize(self, url, timeout=.3))
    monkeypatch.setattr(sys, "argv", ["clock-quality", "--prometheus-url", url,
                                    "--cluster", "synthetic", "--node", "fixture"])
    started = time.monotonic()
    with pytest.raises(SystemExit) as exit:
        clock_quality.main()
    assert exit.value.code == 1
    assert time.monotonic() - started < 1.8
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "unknown"
    assert "offset_seconds:TimeoutError" in report["nodes"]["fixture"]["issues"]
    assert len(state["requests"]) == 5
    # The rule engine remains under IsolatedAnalyzer's existing boundary.
    from xlayer_telemetry.analysis import diagnostics
    assert diagnostics.PrometheusClient is prometheus.PrometheusClient


@pytest.mark.parametrize("timeout", [float("inf"), float("nan"), -1, 0, True])
def test_readiness_rejects_invalid_budget_before_attempt(monkeypatch, timeout):
    monkeypatch.setattr(stack, "_request", lambda *args, **kwargs: pytest.fail("invalid budget started a request"))
    with pytest.raises(ValueError, match="timeout must be finite and positive"):
        stack._wait_until_ready("http://unused", timeout)
