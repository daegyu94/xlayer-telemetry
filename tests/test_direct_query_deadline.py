"""Direct packet/3FS queries bound DNS and complete bodies without rule workers."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from tests._process_helpers import child_processes
from xlayer_telemetry import _http_transport as transport
from xlayer_telemetry.analysis import llm_diagnosis as llm
from xlayer_telemetry.subsystems import inspect_threefs


def source_config(url, timeout=3):
    return {'prometheus_url': url, 'timeout_seconds': timeout,
            'current_interval': {'start': 90, 'end': 100}, 'baseline_interval': None,
            'queries': [{'signal': 'latency', 'query': 'latency', 'unit': 'seconds', 'scope': 'node'}]}


def inspect(url, timeout=3):
    return inspect_threefs({'threefs': {'url': url, 'timeout_seconds': timeout,
                                     'filters': {'mount_name': "training'雪"}}}, now=130, seconds=10)


@pytest.fixture
def backend():
    state = {'mode': 'ok', 'requests': [], 'counter_mode': None}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.reply(None)

        def do_POST(self):
            self.reply(self.rfile.read(int(self.headers.get('Content-Length', 0))))

        def reply(self, body):
            state['requests'].append((self.path, body, self.headers.get('Authorization'),
                                      self.headers.get('Content-Type')))
            counter = body is not None and b'.counters ' in body
            mode = state['counter_mode'] if counter and state['counter_mode'] else state['mode']
            if self.path.startswith('/fast/'):
                mode = 'ok'
            if mode.startswith('error'):
                self.send_response(int(mode[5:]), 'private-backend-message')
                self.send_header('Private-Token', 'secret')
                self.end_headers()
                return
            if body is None:
                payload = {'status': 'success', 'data': {'resultType': 'matrix', 'result': [
                    {'metric': {'node': 'n'}, 'values': [[90, '1'], [100, '2']]}]}}
            elif counter:
                payload = {'metricName': 'read.bytes', 'host': 'n', 'tag': '', 'mount_name': 'training',
                           'instance': 'fuse', 'io': 'read', 'uid': '1000', 'pod': '', 'thread': '0',
                           'statusCode': 'OK', 'sample_count': '2', 'min': '1', 'max': '2', 'last': '2',
                           'first_observed_at': '90', 'last_observed_at': '99'}
            else:
                payload = {'metricName': 'read_latency', 'sample_count': '2', 'weighted_mean': 1.5,
                           'max_value': 2, 'max_observed_p99': 2,
                           'first_observed_at': '90', 'last_observed_at': '99'}
            data = json.dumps(payload).encode()
            if mode == 'oversized':
                data = b' ' * (transport.MAX_RESPONSE_BYTES + 1)
            if mode == "headers":
                time.sleep(1.5)
            self.send_response(200)
            self.send_header('Content-Length', str(len(data) + (50 if mode in {'trickle', 'truncated'} else 0)))
            self.end_headers()
            try:
                if mode == 'trickle':
                    for _ in range(50):
                        self.wfile.write(b' ')
                        self.wfile.flush()
                        time.sleep(.03)
                self.wfile.write(data)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def failed_call(source, url, timeout):
    if source == 'packet':
        packet = llm.collect_packet(source_config(url, timeout))
        assert packet['observations'] == []
        assert packet['missing_sources'] == ['current:latency:TimeoutError']
    else:
        with pytest.raises(TimeoutError, match='deadline'):
            inspect(url, timeout)


@pytest.mark.parametrize('source', ['packet', 'threefs'])
@pytest.mark.parametrize('mode', ['trickle', 'headers'])
def test_direct_query_total_deadline_interrupts_trickling_body(backend, source, mode):
    state, url = backend
    state['mode'] = mode
    before = {child.pid for child in child_processes(os.getpid())}
    started = time.monotonic()
    failed_call(source, url, .3)
    assert time.monotonic() - started < 1.0
    assert {child.pid for child in child_processes(os.getpid())} <= before
    state['mode'] = 'ok'
    if source == 'packet':
        assert llm.collect_packet(source_config(url))['observations'][0]['current']['mean'] == 1.5
    else:
        assert inspect(url)['status'] == 'observed'


@pytest.mark.parametrize('source', ['packet', 'threefs'])
def test_direct_query_total_deadline_interrupts_dns(monkeypatch, tmp_path, backend, source):
    _, url = backend
    # A raw caller stalls in its resolver; an isolated caller stalls in its own
    # interpreter. Test both paths so this regression fails before the fix.
    original_dns = socket.getaddrinfo
    def slow_dns(*args, **kwargs):
        time.sleep(1.5)
        return original_dns(*args, **kwargs)
    monkeypatch.setattr(socket, 'getaddrinfo', slow_dns)
    (tmp_path / 'sitecustomize.py').write_text('import socket, time\n'
        'def blocked(*args, **kwargs):\n    time.sleep(30)\n'
        'socket.getaddrinfo = blocked\n')
    popen = transport.subprocess.Popen
    def isolated_dns(argv, **kwargs):
        kwargs['env'] = os.environ | {'PYTHONPATH': str(tmp_path) + os.pathsep + str(Path(__file__).resolve().parents[1])}
        return popen(argv, **kwargs)
    monkeypatch.setattr(transport.subprocess, 'Popen', isolated_dns)
    before = {child.pid for child in child_processes(os.getpid())}
    started = time.monotonic()
    failed_call(source, url, .3)
    assert time.monotonic() - started < 1.0
    assert {child.pid for child in child_processes(os.getpid())} <= before


def test_direct_counter_timeout_preserves_available_distributions(backend):
    state, url = backend
    state['counter_mode'] = 'trickle'
    started = time.monotonic()
    result = inspect(url, .3)
    assert time.monotonic() - started < 1.0
    assert result['metrics'][0]['count'] == 2
    assert result['counter_status'] == 'unavailable'
    assert result['missing_sources'] == ['threefs:counters:TimeoutError']


def direct_client(source, url, timeout=3):
    if source == 'packet':
        return lambda: llm.PrometheusClient(url, timeout).query_range_detail('latency', 90, 100, 5)
    from xlayer_telemetry.analysis.diagnostics import _DeadlineThreeFSClient
    return lambda: _DeadlineThreeFSClient(url, timeout=timeout).query_window(90, 100)


@pytest.mark.parametrize('source', ['packet', 'threefs'])
@pytest.mark.parametrize('code', [400, 401, 429, 503])
def test_direct_status_errors_keep_code_and_remove_private_response(backend, source, code):
    state, url = backend
    state['mode'] = f'error{code}'
    with pytest.raises(HTTPError) as caught:
        direct_client(source, url + '/private-path')()
    assert caught.value.code == code
    assert caught.value.url == '' and not caught.value.headers
    assert 'private' not in str(caught.value)
    assert 'secret' not in str(caught.value)


@pytest.mark.parametrize('source', ['packet', 'threefs'])
def test_direct_body_limit_retains_established_error_type(backend, source):
    state, url = backend
    state['mode'] = 'oversized'
    with pytest.raises(RuntimeError if source == 'packet' else ValueError, match='8 MiB'):
        direct_client(source, url)()


@pytest.mark.parametrize('source', ['packet', 'threefs'])
def test_direct_incomplete_content_length_is_not_valid_evidence(backend, source):
    state, url = backend
    state['mode'] = 'truncated'
    with pytest.raises(ConnectionError, match='HTTP transport failed'):
        direct_client(source, url)()


def test_direct_clients_preserve_raw_payload_auth_and_result_parity(backend, monkeypatch):
    from xlayer_telemetry.analysis.diagnostics import ThreeFSClient, _DeadlineThreeFSClient
    from xlayer_telemetry.prometheus import PrometheusClient

    state, url = backend
    monkeypatch.setenv('TEST_QUERY_USER', 'reader')
    monkeypatch.setenv('TEST_QUERY_PASSWORD', 'private:雪')
    settings = dict(filters={'mount_name': "training'雪"}, user_env='TEST_QUERY_USER',
                    password_env='TEST_QUERY_PASSWORD')
    raw, direct = ThreeFSClient(url, **settings), _DeadlineThreeFSClient(url, **settings)
    assert raw.query_window(90, 100) == direct.query_window(90, 100)
    assert raw.query_counters(90, 100) == direct.query_counters(90, 100)
    requests = state['requests']
    assert requests[0] == requests[1] and requests[2] == requests[3]
    assert requests[0][2] == 'Basic ' + base64.b64encode('reader:private:雪'.encode()).decode()
    assert requests[0][3] == 'application/x-www-form-urlencoded'
    assert b'FORMAT JSONEachRow' in requests[0][1]
    assert PrometheusClient(url).query_range_detail('latency', 90, 100, 5) == direct_client('packet', url)()
    assert requests[4] == requests[5]
    query = parse_qs(urlsplit(requests[5][0]).query)
    assert query == {'query': ['latency'], 'start': ['90'], 'end': ['100'], 'step': ['5']}


def test_rule_clients_retain_raw_loaders_without_nested_processes(backend, monkeypatch):
    from xlayer_telemetry.analysis.diagnostics import ThreeFSClient
    from xlayer_telemetry.prometheus import PrometheusClient

    _, url = backend
    monkeypatch.setattr(transport.subprocess, 'Popen', lambda *a, **k: pytest.fail('rule client started a worker'))
    assert PrometheusClient(url).query_range('latency', 90, 100, 5)['mean'] == 1.5
    assert ThreeFSClient(url).query_window(90, 100)[0]['count'] == 2


def test_concurrent_direct_callers_keep_independent_deadlines_and_cleanup(backend):
    state, url = backend
    state['mode'] = 'trickle'
    before = {child.pid for child in child_processes(os.getpid())}
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=3) as pool:
        slow_packet = pool.submit(llm.collect_packet, source_config(url, .6))
        fast_packet = pool.submit(llm.collect_packet, source_config(url + '/fast', 3))
        slow_threefs = pool.submit(inspect, url, .6)
        assert slow_packet.result()['missing_sources'] == ['current:latency:TimeoutError']
        assert fast_packet.result()['observations'][0]['current']['mean'] == 1.5
        with pytest.raises(TimeoutError):
            slow_threefs.result()
    assert time.monotonic() - started < 1.3
    assert {child.pid for child in child_processes(os.getpid())} <= before


def test_spawn_failure_and_unreaped_worker_remain_source_failures(monkeypatch, backend):
    state, url = backend
    popen = transport.subprocess.Popen
    def unavailable(*args, **kwargs):
        raise OSError('private process failure')
    monkeypatch.setattr(transport.subprocess, 'Popen', unavailable)
    assert llm.collect_packet(source_config(url))['missing_sources'] == ['current:latency:OSError']
    with pytest.raises(OSError):
        inspect(url)
    monkeypatch.setattr(transport.subprocess, 'Popen', popen)
    class Process:
        exited = False
        def poll(self):
            return 0 if self.exited else None
    worker = Process()
    monkeypatch.setattr(transport, '_UNREAPED', [worker])
    assert llm.collect_packet(source_config(url))['missing_sources'] == ['current:latency:RuntimeError']
    with pytest.raises(RuntimeError, match='no replacement'):
        inspect(url)
    assert state['requests'] == []
    worker.exited = True
    assert inspect(url)['status'] == 'observed'
    assert transport._UNREAPED == []


def test_direct_clients_support_unguarded_standalone_caller(backend, tmp_path):
    _, url = backend
    script = tmp_path / 'direct.py'
    script.write_text('import json\nfrom xlayer_telemetry.analysis.llm_diagnosis import collect_packet\n'
                      'from xlayer_telemetry.subsystems import inspect_threefs\n'
                      f'print(json.dumps([collect_packet({source_config(url)!r}), '
                      f'inspect_threefs({{"threefs": {{"url": {url!r}}}}}, now=130, seconds=10)]))\n')
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path,
                            env=os.environ | {'PYTHONPATH': str(Path(__file__).resolve().parents[1])},
                            capture_output=True, text=True, timeout=8)
    assert result.returncode == 0, result.stderr
    packet, threefs = json.loads(result.stdout)
    assert packet['observations'][0]['current']['mean'] == 1.5
    assert threefs['counter_status'] == 'observed'


def test_raw_body_and_authorization_travel_only_over_private_stdin(monkeypatch):
    captured = {}
    class Process:
        returncode = 0
        stdin = stdout = None
        def communicate(self, command, **kwargs):
            captured['request'] = json.loads(command)
            return b'{"ok":true}\nresponse', None
        def poll(self):
            return 0
        def terminate(self):
            pytest.fail('completed worker must not be signaled')
        kill = terminate
    def popen(argv, **kwargs):
        captured['argv'] = argv
        captured['options'] = kwargs
        return Process()
    monkeypatch.setattr(transport.subprocess, 'Popen', popen)
    data = b'SELECT private-filter\x00\xff'
    headers = {'Authorization': 'Basic private-token', 'Content-Type': 'text/plain'}
    assert transport.request_bytes('http://private-path', None, 2, data=data, headers=headers) == b'response'
    assert base64.b64decode(captured['request']['data']) == data
    assert captured['request']['headers'] == headers
    assert captured['request']['payload'] is None
    assert len(captured['argv']) == 5
    assert 'private' not in ' '.join(captured['argv'])
    assert captured['options']['stderr'] == subprocess.DEVNULL


@pytest.mark.parametrize('options', [
    {'data': 'private'}, {'data': bytearray(b'private')}, {'data': b'raw', 'payload': {}},
    {'data': b'x' * (256 * 1024 + 1)}, {'data': b'x' * (200 * 1024)},
    {'headers': []}, {'headers': {'': 'private'}}, {'headers': {1: 'private'}},
    {'headers': {'Authorization': 1}}, {'headers': {'Bad\nName': 'private'}},
    {'headers': {'Authorization': 'private\r\nInjected: value'}},
    {'headers': {'Authorization': 'private\x00'}}, {'headers': {'Authorization': '雪'}},
])
def test_invalid_raw_request_rejected_before_starting_worker(monkeypatch, options):
    monkeypatch.setattr(transport.subprocess, 'Popen', lambda *a, **k: pytest.fail('invalid request spawned'))
    options = {'payload': None, **options}
    with pytest.raises(ValueError) as caught:
        transport.request_bytes('http://unused', timeout=3, **options)
    assert 'private' not in str(caught.value)


@pytest.mark.parametrize('extra', [
    {'data': 'not base64 private'}, {'data': 7}, {'data': '', 'payload': {}},
    {'headers': {'Authorization': 'private\r\nInjected: value'}},
])
def test_worker_validates_raw_stdin_without_exposing_secrets(extra):
    command = {'url': 'http://unused', 'payload': None, **extra}
    result = subprocess.run([sys.executable, '-m', 'xlayer_telemetry._http_transport',
                             str(time.monotonic() + 2), str(os.getpid())],
                            input=json.dumps(command).encode(), capture_output=True, timeout=3)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {'ok': False, 'type': 'ValueError', 'message': 'HTTP transport failed'}
    assert result.stderr == b''


def test_empty_raw_body_remains_post_and_preserves_custom_headers(backend):
    state, url = backend
    transport.request_bytes(url, None, 3, data=b'', headers={'Content-Type': 'text/plain'})
    assert state['requests'][0][1] == b''
    assert state['requests'][0][3] == 'text/plain'
