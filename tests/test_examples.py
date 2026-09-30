"""Checkout example regressions: actual SDK chain and Compose provisioning."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_cpu_sdk_example_preserves_context_without_resource_attribution(tmp_path):
    run = tmp_path / "run"
    result = subprocess.run([
        sys.executable, "-m", "examples.application.quickstart", "--output", str(run),
        "--node", "cpu-example", "--run-id", "example-run",
    ], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for path in (run / "telemetry-events").glob("*.jsonl")
              for line in path.read_text().splitlines()]
    assert {event['step'] for event in events} == {1, 2}
    for step in (1, 2):
        spans = {event['name']: event for event in events if event['step'] == step}
        iteration, tool, sandbox = (spans[name] for name in ('iteration', 'tool.call', 'sandbox.exec'))
        assert sandbox['parent_span_id'] == tool['span_id']
        assert tool['parent_span_id'] == iteration['span_id']
        assert len({span['trace_id'] for span in (iteration, tool, sandbox)}) == 1
        assert sandbox['role'] == 'sandbox' and sandbox['node'] == 'cpu-example'
        assert sandbox['attributes']['deployment'] == 'colocated'
        assert sandbox['attributes']['trajectory_id'] == tool['attributes']['trajectory_id']
        assert all(span['status'] == 'ok' for span in spans.values())
    snapshots = [json.loads(path.read_text()) for path in (run / 'telemetry-metrics').glob('*.json')]
    assert len(snapshots) == 1 and snapshots[0]['step'] == 2
    labels = {key for sample in snapshots[0]['samples'] for key in sample.get('labels', {})}
    assert not labels & {'trajectory_id', 'sandbox_id', 'trace_id', 'span_id'}
    assert not any(event['name'] == 'sandbox.resource_sample' for event in events)
    assert not list((run / 'workspace').iterdir())
    rerun = subprocess.run([sys.executable, '-m', 'examples.application.quickstart',
                            '--output', str(run)], cwd=ROOT, capture_output=True, text=True)
    assert rerun.returncode != 0  # Preserve an existing run instead of overwriting it.


def test_compose_uses_current_metrics_dashboards_and_selected_mounts(tmp_path):
    docker = shutil.which('docker')
    if not docker or subprocess.run([docker, 'compose', 'version'], capture_output=True).returncode:
        pytest.skip('Docker Compose is not installed')
    dashboards = tmp_path / 'dashboards'
    subprocess.run([sys.executable, str(ROOT / 'scripts/provision_dashboards.py'),
                    '--output', str(dashboards)], check=True)
    env = os.environ | {'DASHBOARDS_DIR': str(dashboards), 'TARGET_DIR': str(tmp_path),
                        'PROMETHEUS_PORT': '29090', 'GRAFANA_PORT': '23000'}
    result = subprocess.run([docker, 'compose', '-f', str(ROOT / 'examples/dashboards/compose.yaml'),
                             'config', '--format', 'json'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)['services']
    assert any(m['source'] == str(dashboards) and m['target'] == '/var/lib/grafana/dashboards'
               for m in config['grafana']['volumes'])
    assert any(m['source'] == str(tmp_path) and m['target'] == '/etc/prometheus/targets'
               for m in config['prometheus']['volumes'])
    assert config['grafana']['ports'][0]['published'] == '23000'
    assert config['prometheus']['ports'][0]['published'] == '29090'
    files = list(dashboards.glob('*.json'))
    assert len(files) == 5
    for path in files:
        assert 'telemetry-loki' not in path.read_text()
    datasource = (ROOT / 'examples/dashboards/grafana/provisioning/datasources/prometheus.yaml').read_text()
    assert 'uid: telemetry-prometheus' in datasource


@pytest.mark.parametrize('filename', ['native.json', 'storage.json'])
def test_compose_optional_target_files_are_validated(tmp_path, filename):
    from xlayer_telemetry.stack import validate_target_files

    for path in (ROOT / 'examples/dashboards/targets').glob('*.json'):
        shutil.copyfile(path, tmp_path / path.name)
    assert validate_target_files(tmp_path)['files'] == 5
    (tmp_path / filename).write_text(json.dumps([{
        'targets': ['engine.internal:8000'], 'labels': {'cluster': None},
    }]))
    with pytest.raises(ValueError, match='labels must be strings'):
        validate_target_files(tmp_path)
