"""Budgets are enforced in both CLI config and generated Prometheus jobs."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from xlayer_telemetry.operations.config import ConfigError, defaults, load_config, validate_collection_budgets

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize('key,value', [
    ('PROMETHEUS_SAMPLE_LIMIT', '0'), ('PROMETHEUS_SAMPLE_LIMIT', '1000001'),
    ('PROMETHEUS_TARGET_LIMIT', '-1'), ('PROMETHEUS_LABEL_LIMIT', '0'),
    ('PROMETHEUS_LABEL_LIMIT', '1025'), ('PROMETHEUS_LABEL_LIMIT', 'nan'), ('PROMETHEUS_BODY_SIZE_LIMIT_MB', '1MB'),
    ('NATIVE_SCRAPE_INTERVAL_SECONDS', 'nan'), ('NATIVE_SCRAPE_TIMEOUT_SECONDS', '6'),
    ('GPU_PROCESS_METRICS', 'true'), ('GPU_MAX_PROCESSES', '0'),
])
def test_collection_budgets_reject_invalid_values(key, value):
    with pytest.raises(ConfigError, match=key):
        validate_collection_budgets(defaults() | {key: value})


def test_toml_and_legacy_config_preserve_collection_budgets(tmp_path):
    custom = {'PROMETHEUS_LABEL_LIMIT': '96', 'PROMETHEUS_SAMPLE_LIMIT': '50000', 'NATIVE_SCRAPE_INTERVAL_SECONDS': '10',
              'NATIVE_SCRAPE_TIMEOUT_SECONDS': '8', 'GPU_PROCESS_METRICS': '1', 'GPU_MAX_PROCESSES': '32'}
    for suffix in ('toml', 'conf'):
        path = tmp_path / f'config.{suffix}'
        path.write_text(('[telemetry]\n' if suffix == 'toml' else '') +
                        '\n'.join(f'{key}={json.dumps(value)}' for key, value in custom.items()))
        config, _ = load_config(path)
        assert all(config[key] == value for key, value in custom.items())


def test_launcher_generates_bounded_host_and_native_scrapes(tmp_path):
    sources = tmp_path / 'sources.json'
    sources.write_text(json.dumps({'schema_version': 1, 'sources': [
        {'name': 'dcgm-node', 'kind': 'dcgm', 'target': 'node:9400', 'labels': {'node': 'node'}}]}))
    output = tmp_path / 'server'
    env = os.environ | {'SERVER_CONFIG_ONLY': '1', 'OUTPUT_DIR': str(output),
        'TELEMETRY_TARGETS': 'node=127.0.0.1',
        'TELEMETRY_SOURCES_FILE': str(sources), 'PROMETHEUS_SAMPLE_LIMIT': '12000',
        'NATIVE_SCRAPE_INTERVAL_SECONDS': '10', 'NATIVE_SCRAPE_TIMEOUT_SECONDS': '8'}
    result = subprocess.run(['bash', str(ROOT / 'scripts/run_telemetry.sh'), 'server'],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    text = (output / 'prometheus.yml').read_text()
    for fragment in ('sample_limit: 12000', 'target_limit: 1024', 'body_size_limit: 16MB',
                     'label_limit: 64', 'label_name_length_limit: 128', 'label_value_length_limit: 1024',
                     'scrape_interval: 2s', 'scrape_timeout: 2s',
                     'scrape_interval: 10s', 'scrape_timeout: 8s'):
        assert fragment in text
    bad = subprocess.run(['bash', str(ROOT / 'scripts/run_telemetry.sh'), 'server'],
                         env=env | {'OUTPUT_DIR': str(tmp_path / 'bad'), 'PROMETHEUS_SAMPLE_LIMIT': '0'},
                         capture_output=True, text=True, timeout=10)
    assert bad.returncode == 2
    assert not (tmp_path / 'bad/prometheus.yml').exists()


@pytest.mark.parametrize('limit,expected_up', [(40, '0'), (None, '1'), (96, '1')])
def test_wide_info_metric_scrape_obeys_generated_label_budget(tmp_path, limit, expected_up):
    import shutil
    import socket
    import sys
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlencode
    from urllib.request import urlopen

    sibling = Path(os.environ['PROMTOOL']).with_name('prometheus') if os.environ.get('PROMTOOL') else None
    tool = str(sibling) if sibling and sibling.is_file() else shutil.which('prometheus')
    if not tool:
        pytest.skip('set PROMTOOL to run the real wide-label scrape regression')
    labels = ','.join(f'config_{i}="value"' for i in range(42))
    body = (f'vllm:cache_config_info{{{labels}}} 1\nwide_label_control 7\n').encode()

    class Exporter(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Exporter)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    process = None
    log = (tmp_path / 'prometheus.log').open('wb')
    try:
        sources = tmp_path / 'sources.json'
        sources.write_text(json.dumps({'schema_version': 1, 'sources': [{
            'name': 'wide-vllm', 'kind': 'vllm',
            'target': f'127.0.0.1:{server.server_port}', 'labels': {'node': 'gpu01'}}]}))
        output = tmp_path / 'server'
        env = os.environ | {'PYTHON': sys.executable, 'SERVER_CONFIG_ONLY': '1',
            'OUTPUT_DIR': str(output), 'TELEMETRY_TARGETS': 'node=127.0.0.1',
            'TELEMETRY_SOURCES_FILE': str(sources),
            'NATIVE_SCRAPE_INTERVAL_SECONDS': '2', 'NATIVE_SCRAPE_TIMEOUT_SECONDS': '1'}
        env.pop('PROMETHEUS_LABEL_LIMIT', None)
        if limit is not None:
            env['PROMETHEUS_LABEL_LIMIT'] = str(limit)
        generated = subprocess.run(['bash', str(ROOT / 'scripts/run_telemetry.sh'), 'server'],
            env=env, capture_output=True, text=True, timeout=10)
        assert generated.returncode == 0, generated.stderr
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        process = subprocess.Popen([tool, f'--config.file={output / "prometheus.yml"}',
            f'--storage.tsdb.path={tmp_path / "tsdb"}', f'--web.listen-address=127.0.0.1:{port}'],
            stdout=log, stderr=log)
        deadline = time.monotonic() + 20
        targets = []
        while time.monotonic() < deadline:
            assert process.poll() is None, (tmp_path / 'prometheus.log').read_text()[-4000:]
            try:
                with urlopen(base + '/api/v1/targets', timeout=2) as response:
                    targets = [target for target in json.load(response)['data']['activeTargets']
                               if target['labels'].get('job') == 'native']
                if len(targets) == 1 and not targets[0]['lastScrape'].startswith('0001-'):
                    break
            except OSError:
                pass
            time.sleep(.05)
        else:
            pytest.fail(f'native scrape did not complete: {targets}')
        target = targets[0]
        assert target['health'] == ('up' if expected_up == '1' else 'down'), target
        if expected_up == '0':
            assert 'label' in target['lastError'].lower(), target
        else:
            assert target['lastError'] == ''
        def query(expression):
            with urlopen(base + '/api/v1/query?' + urlencode({'query': expression}), timeout=2) as response:
                return json.load(response)['data']['result']
        assert query('up{job="native"}')[0]['value'][1] == expected_up
        for metric in ('vllm:cache_config_info', 'wide_label_control'):
            rows = query(metric + '{job="native"}')
            assert bool(rows) == (expected_up == '1'), rows
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        log.close()
