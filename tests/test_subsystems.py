import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from xlayer_telemetry.source_discovery import build_file_discovery
from xlayer_telemetry.subsystems import explore_url, inspect_sources, inspect_threefs

ROOT = Path(__file__).parents[1]


@pytest.fixture
def backend():
    requests = []
    state = {'payload': {'status': 'success', 'data': {'activeTargets': []}}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(state['payload']).encode())

        def do_POST(self):
            requests.append(self.rfile.read(int(self.headers['Content-Length'])).decode())
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"metricName":"readLatency","sample_count":4,"weighted_mean":2,"max_value":9,"max_observed_p99":8}\n')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', state, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_native_status_matches_cluster_component_and_endpoint(backend):
    url, state, requests = backend
    groups = build_file_discovery({'schema_version': 1, 'sources': [
        {'name': name, 'kind': 'ray', 'target': f'node:{8000 + i}'}
        for i, name in enumerate(['up', 'down', 'missing'])]})
    groups[0]['labels']['cluster'] = 'ignored-by-server-relabel'
    state['payload']['data']['activeTargets'] = [
        {'labels': {'job': 'native', 'cluster': cluster, 'component': name, 'telemetry_source': 'ray',
                    'instance': f'node:{port}'}, 'health': health, 'lastScrape': '2026-10-02T00:00:00Z'}
        for cluster, name, port, health in [('a', 'up', 8000, 'up'), ('a', 'down', 8001, 'down'),
                                          ('b', 'missing', 8002, 'up')]]
    result = inspect_sources(groups, url, 'http://grafana/subpath', 'a')
    assert [r['status'] for r in result['sources']] == ['up', 'down', 'not_discovered']
    assert len(requests) == 1  # One target request regardless of source count.
    pane = json.loads(parse_qs(urlsplit(result['sources'][0]['metrics_url']).query)['panes'][0])['A']
    assert 'cluster="a"' in pane['queries'][0]['expr']
    assert 'run_id' not in pane['queries'][0]['expr']
    assert pane['queries'][0]['instant'] and not pane['queries'][0]['range']
    assert result['sources'][0]['last_scrape'] == ['2026-10-02T00:00:00Z']
    state['payload']['data']['activeTargets'][0]['health'] = 'unknown'
    assert inspect_sources(groups, url, 'http://grafana', 'a')['sources'][0]['status'] == 'unknown'


def test_explore_link_preserves_special_characters():
    expr = '{component="ray-head",cluster="quote\\\" & test"}'
    url = explore_url('http://grafana', expr)
    pane = json.loads(parse_qs(urlsplit(url).query)['panes'][0])['A']
    assert pane['queries'][0]['expr'] == expr


def test_bad_backend_is_not_reported_as_missing_target(backend):
    url, state, _ = backend
    state['payload'] = []
    groups = build_file_discovery({'schema_version': 1, 'sources': [
        {'name': 'engine', 'kind': 'vllm', 'target': 'node:8000'}]})
    result = inspect_sources(groups, url, 'http://grafana', 'a')
    assert result['backend_error'] == 'ValueError'
    assert result['sources'][0]['status'] == 'unavailable'


def test_threefs_standalone_window_reuses_filters_and_settle(backend):
    url, _, requests = backend
    result = inspect_threefs({'threefs': {'url': url, 'filters': {'mount_name': 'training'},
                                          'settle_seconds': 30}}, now=1000, seconds=300)
    assert result['scope'] == 'shared-service'
    assert (result['start'], result['end']) == (670, 970)
    assert result['metrics'][0]['max_observed_p99'] == 8
    assert 'mount_name = \'training\'' in requests[0]
    assert 'toDateTime(670)' in requests[0] and 'toDateTime(970)' in requests[0]
    assert inspect_threefs({}) == {'status': 'not_configured'}
    with pytest.raises(ValueError, match='window'):
        inspect_threefs({}, seconds=float('nan'))


def test_local_sources_and_refresh_use_existing_config_without_run_artifacts(tmp_path, backend):
    url, _, _ = backend
    sources = tmp_path / 'sources.json'
    sources.write_text(json.dumps({'schema_version': 1, 'sources': [
        {'name': 'engine', 'kind': 'vllm', 'target': 'node:8000'}]}))
    output = tmp_path / 'server'
    config = tmp_path / 'local.conf'
    run = tmp_path / 'nonexistent-run'
    config.write_text(f"RUN_ID=test\nRUN_ROOT='{run}'\nTELEMETRY_SOURCES_FILE='{sources}'\n"
                      f"SERVER_OUTPUT_DIR='{output}'\nPROMETHEUS_URL='{url}'\n")
    command = ['bash', str(ROOT / 'scripts/verl_local.sh'), '--config', str(config)]
    env = os.environ | {'TELEMETRY_PYTHON': sys.executable}
    result = subprocess.run(command + ['sources'], env=env, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['sources'][0]['status'] == 'not_discovered'
    assert not run.exists()
    result = subprocess.run(command + ['refresh-sources'], env=env, capture_output=True, text=True)
    assert result.returncode == 2 and 'not initialized' in result.stderr
    output.mkdir()
    target = output / 'native-targets.json'
    target.write_text('[]')
    result = subprocess.run(command + ['refresh-sources'], env=env, capture_output=True, text=True)
    assert result.returncode == 2  # A stale target file is not an enabled native job.
    (output / 'prometheus.yml').write_text('scrape_configs:\n  - job_name: native\n')
    subprocess.run(command + ['refresh-sources'], env=env, capture_output=True, text=True, check=True)
    assert json.loads(target.read_text())[0]['targets'] == ['node:8000']
    sources.write_text('{broken')
    previous = target.read_text()
    result = subprocess.run(command + ['refresh-sources'], env=env, capture_output=True, text=True)
    assert result.returncode != 0 and target.read_text() == previous


def test_threefs_cli_and_backend_failure_do_not_require_training(tmp_path, backend):
    url, _, _ = backend
    config = tmp_path / 'diagnostics.json'
    config.write_text(json.dumps({'schema_version': 1, 'prometheus': {'url': 'http://unused'},
                                 'threefs': {'url': url}}))
    result = subprocess.run([sys.executable, '-m', 'xlayer_telemetry.subsystems',
                             '--threefs', '--diagnostics-config', str(config)],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['metrics'][0]['metricName'] == 'readLatency'
    assert not list(tmp_path.glob('**/diagnostics.jsonl'))
    sources = tmp_path / 'sources.json'
    sources.write_text(json.dumps({'schema_version': 1, 'sources': [
        {'name': 'engine', 'kind': 'vllm', 'target': 'node:8000'}]}))
    result = subprocess.run([sys.executable, '-m', 'xlayer_telemetry.subsystems',
                             '--sources', str(sources), '--prometheus', 'http://127.0.0.1:0'],
                            cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert json.loads(result.stdout)['sources'][0]['status'] == 'unavailable'
