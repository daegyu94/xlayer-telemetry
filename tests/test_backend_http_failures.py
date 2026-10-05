"""Malformed HTTP framing remains a bounded, source-local evidence failure."""
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from xlayer_telemetry import prometheus
from xlayer_telemetry.analysis import diagnostics
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, ThreeFSClient
from xlayer_telemetry.prometheus import PrometheusClient


@pytest.fixture
def backend():
    state = {'broken': '', 'posts': 0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def broken_chunk(self):
            self.send_response(200)
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            # The incomplete frame includes backend text that must not leak
            # into an exception message or a diagnostic report.
            self.wfile.write(b'100\r\nprivate-backend-payload')
            self.wfile.flush()
            self.close_connection = True

        def json_body(self, body):
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query).get('query', [''])[0]
            if state['broken'] == 'all' or (state['broken'] == 'prometheus' and 'num_preemptions_total' in query):
                self.broken_chunk()
                return
            self.json_body(json.dumps({'status': 'success', 'data': {
                'resultType': 'matrix', 'result': [
                    {'metric': {'instance': 'n'}, 'values': [[90, '50'], [100, '50']]},
                ],
            }}).encode())

        def do_POST(self):
            state['posts'] += 1
            if state['broken'] == 'all' or (state['broken'] == 'threefs' and state['posts'] == 2):
                self.broken_chunk()
                return
            self.json_body(json.dumps({'metricName': 'read_latency', 'sample_count': 3,
                                      'weighted_mean': 1, 'max_value': 2, 'max_observed_p99': 2}).encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('source', ['prometheus', 'threefs'])
def test_truncated_http_chunk_is_a_sanitized_connection_error(backend, source):
    state, url = backend
    state['broken'] = 'all'
    with pytest.raises(ConnectionError) as caught:
        if source == 'prometheus':
            PrometheusClient(url).query_range('up', 90, 100, 1)
        else:
            ThreeFSClient(url).query_window(90, 100)
    assert 'private-backend-payload' not in str(caught.value)
    assert url not in str(caught.value)


@pytest.mark.parametrize('source', ['prometheus', 'threefs'])
def test_truncated_optional_backend_preserves_other_evidence(backend, source):
    state, url = backend
    state['broken'] = source
    report = DiagnosticEngine({'prometheus': {'url': url}, 'threefs': {'url': url}},
                              clock=lambda: 100).analyze(None, [])
    assert report['evidence']['gpu_utilization_percent']['mean'] == 50
    assert report['evidence']['threefs_distributions'][0]['count'] == 3
    assert any(source in item and item.endswith(':ConnectionError') for item in report['missing_sources'])
    execution = report['query_execution']['sources'][source]
    assert execution['failed'] == 1 and execution['unavailable']
    assert 'private-backend-payload' not in json.dumps(report)
    assert url not in json.dumps(report)
    if source == 'prometheus':
        assert 'vllm_preemptions_total' not in report['evidence']
        assert execution['skipped'] > 0
    else:
        assert 'threefs_baseline_distributions' not in report['evidence']


@pytest.mark.parametrize('source', ['prometheus', 'threefs'])
@pytest.mark.parametrize('code', [400, 429, 503])
def test_http_status_errors_retain_their_status_for_query_budget(monkeypatch, source, code):
    error = HTTPError('http://unused', code, 'failure', {}, io.BytesIO())
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(prometheus if source == 'prometheus' else diagnostics, 'urlopen', fail)
    with pytest.raises(HTTPError) as caught:
        if source == 'prometheus':
            PrometheusClient('http://unused').query_range('up', 90, 100, 1)
        else:
            ThreeFSClient('http://unused').query_window(90, 100)
    assert caught.value is error and caught.value.code == code
