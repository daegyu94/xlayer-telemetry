"""Configured backend credentials must not travel to a redirected origin."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError

import pytest

from xlayer_telemetry.analysis.diagnostics import ThreeFSClient, _DeadlineThreeFSClient


@pytest.fixture
def redirects():
    state = {'requests': [], 'same_origin': False, 'code': 302}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            self.do_GET()
        def do_GET(self):
            if self.path.startswith('/?'):
                self.send_response(state['code'])
                target = '/result' if state['same_origin'] else foreign_url + '/result'
                self.send_header('Location', target)
                self.send_header('Content-Length', '0')
                self.end_headers()
            else:
                state['requests'].append((self.server.server_port, self.headers.get('Authorization')))
                body = b'{"metricName":"latency","sample_count":1}\n'
                self.send_response(200); self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
    foreign = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    foreign_url = f'http://127.0.0.1:{foreign.server_port}'
    origin = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    workers = [threading.Thread(target=server.serve_forever, daemon=True) for server in (foreign, origin)]
    for worker in workers: worker.start()
    try:
        yield state, f'http://127.0.0.1:{origin.server_port}'
    finally:
        for server in (origin, foreign): server.shutdown(); server.server_close()
        for worker in workers: worker.join(timeout=2)


@pytest.mark.parametrize('client_type', [ThreeFSClient, _DeadlineThreeFSClient])
@pytest.mark.parametrize('code', [301, 302, 303])
def test_cross_origin_redirect_cannot_receive_backend_credentials(redirects, monkeypatch, client_type, code):
    state, url = redirects; state['code'] = code
    monkeypatch.setenv('XLT_TEST_REDIRECT_USER', 'fixture-user')
    monkeypatch.setenv('XLT_TEST_REDIRECT_PASSWORD', 'fixture-password')
    client = client_type(url, timeout=2, user_env='XLT_TEST_REDIRECT_USER', password_env='XLT_TEST_REDIRECT_PASSWORD')
    with pytest.raises(HTTPError) as caught:
        client.query_window(1, 2)
    assert state['requests'] == []
    assert caught.value.code == code
    assert 'fixture' not in str(caught.value)


@pytest.mark.parametrize('client_type', [ThreeFSClient, _DeadlineThreeFSClient])
@pytest.mark.parametrize('authenticated,same_origin', [(True, True), (False, False)])
def test_supported_redirects_retain_existing_behavior(redirects, monkeypatch, client_type, authenticated, same_origin):
    state, url = redirects; state['same_origin'] = same_origin
    monkeypatch.delenv('XLT_TEST_REDIRECT_USER', raising=False)
    if authenticated: monkeypatch.setenv('XLT_TEST_REDIRECT_USER', 'fixture-user')
    client = client_type(url, timeout=2, user_env='XLT_TEST_REDIRECT_USER', password_env='XLT_TEST_REDIRECT_PASSWORD')
    assert client.query_window(1, 2)[0]['count'] == 1
    assert len(state['requests']) == 1
    assert bool(state['requests'][0][1]) == authenticated


@pytest.mark.parametrize('destination', ['http://service.example/result', 'https://other.example/result',
                                        'https://service.example:444/result'])
def test_authenticated_redirect_rejects_scheme_host_or_port_change(destination):
    from io import BytesIO
    from urllib.request import Request
    from xlayer_telemetry._http_redirects import _CredentialSafeRedirectHandler
    request = Request('https://service.example/source', headers={'Authorization': 'Basic fixture'})
    response = BytesIO()
    with pytest.raises(HTTPError):
        _CredentialSafeRedirectHandler().redirect_request(request, response, 302, 'Found', {}, destination)
    assert response.closed


def test_same_origin_normalizes_hostname_and_default_port():
    from io import BytesIO
    from urllib.request import Request
    from xlayer_telemetry._http_redirects import _CredentialSafeRedirectHandler
    request = Request('https://SERVICE.example:443/source', headers={'Authorization': 'Basic fixture'})
    redirected = _CredentialSafeRedirectHandler().redirect_request(
        request, BytesIO(), 302, 'Found', {}, 'https://service.example/result')
    assert redirected.get_header('Authorization') == 'Basic fixture'


@pytest.mark.parametrize('destination', ['http://127.0.0.1:fixture-user:fixture-password/result',
                                        'https://service.example:99999/result',
                                        'https://[invalid/result'])
def test_invalid_authenticated_redirect_origin_is_closed_and_redacted(destination):
    from io import BytesIO
    from urllib.request import Request
    from xlayer_telemetry._http_redirects import _CredentialSafeRedirectHandler
    request = Request('https://service.example/source', headers={'Authorization': 'Basic fixture'})
    response = BytesIO()
    with pytest.raises(HTTPError) as caught:
        _CredentialSafeRedirectHandler().redirect_request(request, response, 302, 'Found', {}, destination)
    assert response.closed
    assert 'fixture' not in str(caught.value)
    assert caught.value.__suppress_context__


def test_zero_port_is_not_the_default_origin_port():
    from xlayer_telemetry._http_redirects import _origin
    assert _origin('http://service.example:0') != _origin('http://service.example')
