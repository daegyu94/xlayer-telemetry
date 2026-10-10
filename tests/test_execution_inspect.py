"""CPU cooperative SDK -> saved artifacts -> CLI/analysis, with partial writes."""

import asyncio
import json

from examples.research.trajectory_dependency_demo import run
from tests.test_cli import invoke
from xlayer_telemetry.operations.config import initialize
from xlayer_telemetry.operations.execution import inspect_execution


def test_async_sdk_artifact_graph_path_and_existing_cli(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('TELEMETRY_HOME', str(tmp_path/'telemetry'))
    output = tmp_path/'run'
    result = asyncio.run(run(output))
    path = result['critical_path']
    assert result['data_origin'] == 'synthetic_cpu_workload'
    assert path['status'] == 'observed_path', path
    assert len(path['path']) == 4
    assert path['measured_wait_seconds'] > 0
    assert path['unattributed_seconds'] > 0  # Actual SDK/I/O/scheduling gaps.
    assert path['delay_candidates'][0]['worker_id'] == '1'
    assert result['behavior_signature']['interpretation'].startswith('observed span sums')
    config = tmp_path/'config.toml'
    initialize(config)
    response = invoke(config, 'inspect', str(output), '--execution-graph', '--root-span', result['root_argument'])
    assert response.returncode == 0, response.stderr
    assert json.loads(response.stdout)['critical_path']['path'] == path['path']


def test_partial_or_invalid_record_never_certifies_complete_path(tmp_path):
    output = tmp_path/'run'
    result = asyncio.run(run(output))
    file = next((output/'telemetry-events').glob('*.jsonl'))
    with file.open('a') as stream: stream.write('{"partial":')
    analyzed = inspect_execution(output, root=result['root_argument'].split('/'))
    assert analyzed['critical_path']['status'] == 'unknown'
    assert analyzed['graph']['quality']['invalid_records'] == 1
