"""HTTP proxy selection is applied to real backend requests."""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request

import pytest

from xlayer_telemetry._http_transport import request_bytes
from xlayer_telemetry.analysis.diagnostics import ThreeFSClient
from xlayer_telemetry.prometheus import _read_json


@pytest.fixture
def endpoints():
    seen = {"backend": [], "proxy": []}

    def server(kind):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen[kind].append(self.path)
                if self.path == "/partial":
                    self.send_response(200)
                    self.send_header("Content-Length", "100")
                    self.end_headers()
                    self.wfile.write(b"{}")
                    self.close_connection = True
                    return
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://backend.invalid/after-redirect")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.dumps({"via": kind}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)

    backend, proxy = server("backend"), server("proxy")
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (backend, proxy)]
    for thread in threads:
        thread.start()
    try:
        yield (f"http://127.0.0.1:{backend.server_port}",
               f"http://127.0.0.1:{proxy.server_port}", seen)
    finally:
        for s in (backend, proxy):
            s.shutdown()
            s.server_close()
        for thread in threads:
            thread.join(timeout=2)


def test_loopback_reaches_backend_even_with_unusable_proxy(endpoints, monkeypatch):
    backend, _, seen = endpoints
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    assert _read_json(Request(backend), 2) == {"via": "backend"}
    assert json.loads(request_bytes(backend, None, 2)) == {"via": "backend"}
    assert ThreeFSClient(backend)._read_body(Request(backend)) == b'{"via": "backend"}'
    assert len(seen["backend"]) == 3


def test_nonmatching_no_proxy_uses_user_proxy(endpoints, monkeypatch):
    _, proxy, seen = endpoints
    monkeypatch.setenv("HTTP_PROXY", proxy)
    monkeypatch.setenv("http_proxy", proxy)
    monkeypatch.setenv("ALL_PROXY", proxy)
    monkeypatch.setenv("NO_PROXY", "other.example,192.0.2.0/24")
    monkeypatch.setenv("no_proxy", "other.example,192.0.2.0/24")
    target = "http://backend.invalid/resource"
    assert _read_json(Request(target), 2) == {"via": "proxy"}
    assert json.loads(request_bytes(target, None, 2)) == {"via": "proxy"}
    assert ThreeFSClient(target)._read_body(Request(target)) == b'{"via": "proxy"}'
    assert seen["proxy"] == [target] * 3


def test_redirect_uses_destination_proxy_policy(endpoints, monkeypatch):
    backend, proxy, seen = endpoints
    monkeypatch.setenv("HTTP_PROXY", proxy)
    monkeypatch.setenv("http_proxy", proxy)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    assert json.loads(request_bytes(backend + "/redirect", None, 2)) == {"via": "proxy"}
    assert seen["backend"] == ["/redirect"]
    assert seen["proxy"] == ["http://backend.invalid/after-redirect"]


def test_localhost_hostname_is_direct(endpoints, monkeypatch):
    backend, _, seen = endpoints
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    assert _read_json(Request(backend.replace("127.0.0.1", "localhost")), 2) == {"via": "backend"}
    assert seen["backend"] == ["/"]


def test_bounded_transport_rejects_partial_body(endpoints, monkeypatch):
    backend, _, _ = endpoints
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    with pytest.raises(ConnectionError, match="HTTP transport failed"):
        request_bytes(backend + "/partial", None, 2)


def test_ipv6_loopback_is_direct_when_available(monkeypatch):
    class IPv6Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    try:
        server = IPv6Server(("::1", 0), Handler)
    except OSError:
        pytest.skip("IPv6 loopback is unavailable")
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")
        assert _read_json(Request(f"http://[::1]:{server.server_port}/"), 2) == {}
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("host,no_proxy,expected", [
    ("localhost:1234", "", True),
    ("127.0.0.2:1234", "", True),
    ("[::1]:1234", "", True),
    ("192.0.2.42:1234", "192.0.2.0/24", True),
    ("192.0.2.42:1234", "198.51.100.0/24", False),
    ("metrics.example:1234", "metrics.example", True),
    ("a.metrics.example:1234", ".metrics.example", True),
    ("metrics.example:1234", "metrics.example:1234", True),
    ("metrics.example:1234", "metrics.example:4321", False),
])
def test_proxy_bypass_policy(host, no_proxy, expected):
    from xlayer_telemetry._http_proxy import _bypass_proxy
    assert _bypass_proxy(host, no_proxy) is expected
