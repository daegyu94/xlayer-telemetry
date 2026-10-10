"""Inventory compilation preserves existing deployment and observation contracts."""
from copy import deepcopy
import json
from pathlib import Path
import stat

import pytest

from xlayer_telemetry.cluster_inventory import compile_inventory, load_inventory, render_inventory
from xlayer_telemetry.collectors.topology_textfile import build_gauges
from xlayer_telemetry.operations.config import KEYS, load_config
from xlayer_telemetry.source_discovery import load_file_discovery


def inventory():
    return {'schema_version': 1, 'cluster': {'name': 'training-poc', 'monitoring_node': 'monitoring-0'},
        'nodes': [
            {'name': 'monitoring-0', 'address': '127.0.0.1', 'roles': ['monitoring']},
            {'name': 'gpu-a', 'address': '10.0.0.2', 'roles': ['trainer', 'rollout'],
             'gpus': [0, 1], 'interfaces': ['ens5'], 'devices': ['nvme0n1']},
            {'name': 'storage-a', 'address': 'storage-a.internal', 'roles': ['ds'],
             'interfaces': ['ens3'], 'devices': ['nvme0n1']},
        ],
        'services': [{'name': 'vllm-a', 'kind': 'vllm', 'node': 'gpu-a', 'target': '10.0.0.2:8000',
                      'labels': {'engine': '0', 'role': 'rollout'}}],
        'components': [{'kind': 'compute', 'id': 'roce-fabric', 'role': 'fabric'},
                       {'kind': 'storage', 'id': 'roce-fabric', 'role': 'fabric'}],
        'edges': [{'kind': 'compute', 'source': 'gpu-a', 'destination': 'roce-fabric', 'relation': 'network'},
                  {'kind': 'storage', 'source': 'roce-fabric', 'destination': 'storage-a', 'relation': 'network'}],
        'storage': {'backend': '3fs', 'filesystem': '/mnt/3fs'}}


def write_inventory(tmp_path, data=None):
    path = tmp_path / 'inventory.json'
    path.write_text(json.dumps(data or inventory()))
    return path


def test_compiler_keeps_declared_namespace_identity_and_never_invents_network_hops():
    compiled = compile_inventory(inventory())
    assert compiled['telemetry_targets'] == 'gpu-a=10.0.0.2,monitoring-0=127.0.0.1,storage-a=storage-a.internal'
    compute, storage = compiled['topology']['compute'], compiled['topology']['storage']
    assert any(row['id'] == 'roce-fabric' for row in compute['components'])
    assert any(row['id'] == 'roce-fabric' for row in storage['components'])
    gpu = next(row for row in compute['components'] if row.get('gpu') == '0')
    assert gpu['resource_node'] == 'gpu-a'
    local = next(row for row in compute['components'] if row.get('device') == 'nvme0n1')
    assert 'storage_system' not in local, 'A descriptive global backend must not attribute local Sandbox SSD I/O to 3FS'
    assert [row for row in compute['edges'] if row['relation'] == 'network'] == [
        {'source': 'gpu-a', 'destination': 'roce-fabric', 'relation': 'network'}]
    assert not any(row['source'] == 'gpu-a' and row['destination'] == 'storage-a' for row in compute['edges'])
    assert compiled['native_sources']['sources'][0]['labels']['node'] == 'gpu-a'
    assert 'run_id' not in json.dumps(compiled['native_sources'])


def test_generated_bundle_is_accepted_by_actual_config_source_and_topology_consumers(tmp_path, monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    source = write_inventory(tmp_path)
    output = tmp_path / 'bundle'
    manifest = render_inventory(source, output, prometheus_url='http://monitoring.internal:19090')
    server, command = load_config(output / 'server.toml')
    assert command == []
    assert server['CLUSTER_NAME'] == 'training-poc'
    assert server['NODE_NAME'] == 'monitoring-0' and server['NODE_ADDR'] == '127.0.0.1'
    assert server['TOPOLOGY_DIR'] == str(output / 'topology')
    groups = load_file_discovery(Path(server['TELEMETRY_SOURCES_FILE']))
    assert groups[0]['labels']['node'] == 'gpu-a' and groups[0]['targets'] == ['10.0.0.2:8000']
    gauges = build_gauges(output / 'topology')
    assert any(row.labels.get('gpu') == '0' and row.labels.get('resource_node') == 'gpu-a' for row in gauges)
    assert not any('run_id' in row.labels for row in gauges)
    gpu, _ = load_config(output / 'nodes/gpu-a.toml')
    cpu, _ = load_config(output / 'nodes/storage-a.toml')
    assert gpu['ENABLE_GPU_METRICS'] == '1' and cpu['ENABLE_GPU_METRICS'] == '0'
    assert 'TOPOLOGY_DIR' not in (output / 'nodes/gpu-a.toml').read_text()
    assert str(output) not in (output / 'nodes/gpu-a.toml').read_text()
    assert manifest['scope'] == 'configured_inventory'
    assert manifest['diagnosis_generated'] is False
    assert 'publisher' in manifest['deployment_notes'][0].lower()
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.rglob('*') if path.is_file())


def test_empty_services_omit_invalid_empty_native_discovery_and_gpu_is_explicit(tmp_path):
    data = {'schema_version': 1, 'cluster': {'name': 'cpu'}, 'nodes': [
        {'name': 'cpu-a', 'address': 'cpu-a', 'roles': ['sandbox']}]}
    output = tmp_path / 'empty-native'
    manifest = render_inventory(write_inventory(tmp_path, data), output)
    assert not (output / 'native-sources.json').exists()
    assert 'TELEMETRY_SOURCES_FILE' not in (output / 'server.toml').read_text()
    assert 'NODE_NAME' not in (output / 'server.toml').read_text()
    assert 'ENABLE_GPU_METRICS = false' in (output / 'nodes/cpu-a.toml').read_text()
    assert manifest['counts']['native_sources'] == 0


def test_collector_disabled_nodes_remain_configured_inventory_without_duplicate_scrapes():
    data = inventory()
    data['nodes'][2]['collector'] = False
    compiled = compile_inventory(data)
    assert 'storage-a=' not in compiled['telemetry_targets']
    assert any(row['id'] == 'storage-a' for row in compiled['topology']['storage']['components'])
    assert all(row.get('resource_node') != 'storage-a' for row in compiled['topology']['storage']['components'])


def test_generated_roles_use_existing_infrastructure_groups_and_disabled_owner_is_not_forced(tmp_path):
    from xlayer_telemetry.topology_validation import inspect_topology
    data = inventory()
    data['nodes'][2]['collector'] = False
    output = tmp_path / 'bundle'
    manifest = render_inventory(write_inventory(tmp_path, data), output)
    compiled = compile_inventory(data)
    gpu = next(row for row in compiled['topology']['compute']['components'] if row['id'] == 'gpu-a')
    assert gpu['role'] == 'gpu-node'
    assert manifest['nodes'][0]['roles'], 'Original configured roles remain in static metadata'
    report = inspect_topology(output / 'topology', registered_nodes={'gpu-a', 'monitoring-0'})
    assert report['status'] == 'warning'
    assert {row['code'] for row in report['issues']} == {'resource_mapping_unverified'}


def test_json_and_toml_inputs_share_normalization_and_stable_bytes(tmp_path):
    path = tmp_path / 'inventory.toml'
    path.write_text('''schema_version = 1
[cluster]
name = "cpu"
[[nodes]]
name = "b"
address = "10.0.0.2"
roles = ["compute"]
[[nodes]]
name = "a"
address = "10.0.0.1"
roles = ["compute"]
''')
    data = {'schema_version': 1, 'cluster': {'name': 'cpu'}, 'nodes': [
        {'name': 'a', 'address': '10.0.0.1', 'roles': ['compute']},
        {'name': 'b', 'address': '10.0.0.2', 'roles': ['compute']}]}
    assert compile_inventory(load_inventory(path)) == compile_inventory(data)
    first, second = tmp_path / 'one', tmp_path / 'two'
    render_inventory(path, first)
    render_inventory(write_inventory(tmp_path, data), second)
    for relative in ('topology/compute-topology.json', 'topology/storage-topology.json', 'nodes/a.toml'):
        assert (first / relative).read_bytes() == (second / relative).read_bytes()
    assert json.loads((first / 'manifest.json').read_text())['inventory_sha256'] == json.loads((second / 'manifest.json').read_text())['inventory_sha256']


def test_nonregular_inventory_is_rejected_before_a_blocking_open(tmp_path, monkeypatch):
    import os
    path = tmp_path / 'inventory.toml'
    os.mkfifo(path)
    original = Path.open
    def guarded_open(self, *args, **kwargs):
        if self == path:
            raise AssertionError('Opening the FIFO would block configuration validation')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded_open)
    with pytest.raises(ValueError, match='regular'):
        load_inventory(path)


@pytest.mark.parametrize('change', [
    lambda d: d.update(schema_version=True),
    lambda d: d.update(run_id='dynamic'),
    lambda d: d['cluster'].update(monitoring_node='missing'),
    lambda d: d['nodes'].append(deepcopy(d['nodes'][0])),
    lambda d: d['nodes'][1].update(address='127.0.0.1'),
    lambda d: d['nodes'][1].update(address='http://node:19100'),
    lambda d: d['nodes'][1].update(address='999.999.999.999'),
    lambda d: d['nodes'][1].update(gpus=[True]),
    lambda d: d['nodes'][1].update(gpus=[0, '0']),
    lambda d: d['nodes'][1].update(telemetry_home='relative/path'),
    lambda d: d['services'][0].update(node='missing'),
    lambda d: d['services'][0]['labels'].update(node='other'),
    lambda d: d['services'][0]['labels'].update(nodename='other'),
    lambda d: d['services'][0]['labels'].update(run_id='run-a'),
    lambda d: d['services'][0]['labels'].update(model_name='model-a'),
    lambda d: d['services'][0]['labels'].update(replica='rollout-1'),
    lambda d: d['components'].append({'kind': 'compute', 'id': 'gpu-a', 'role': 'trainer', 'resource_node': 'storage-a'}),
    lambda d: d['edges'][0].update(destination='undeclared-switch'),
    lambda d: d['storage'].update(replica_selection='memory'),
])
def test_invalid_or_dynamic_inventory_is_rejected_before_creating_output(tmp_path, change):
    data = inventory()
    change(data)
    path = write_inventory(tmp_path, data)
    output = tmp_path / 'never-created'
    with pytest.raises(ValueError):
        render_inventory(path, output)
    assert not output.exists()


def test_secrets_in_unsafe_endpoint_errors_are_not_echoed(tmp_path):
    data = inventory()
    data['services'][0]['target'] = 'user:secret-password@node:8000'
    with pytest.raises(ValueError) as failure:
        compile_inventory(data)
    assert 'secret-password' not in str(failure.value)


def test_existing_outputs_and_symlink_parents_are_preserved(tmp_path):
    source = write_inventory(tmp_path)
    existing = tmp_path / 'existing'
    existing.mkdir()
    (existing / 'user-file').write_text('preserve')
    with pytest.raises(ValueError, match='exists'):
        render_inventory(source, existing)
    assert (existing / 'user-file').read_text() == 'preserve'
    link = tmp_path / 'link'
    link.symlink_to(existing, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        render_inventory(source, link / 'child')
    assert not (existing / 'child').exists()


def test_bounded_input_duplicate_json_keys_and_unsupported_format_are_rejected(tmp_path):
    path = tmp_path / 'inventory.json'
    path.write_bytes(b' ' * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match='1 MiB'):
        load_inventory(path)
    path.write_text('{"schema_version": 1, "schema_version": 1}')
    with pytest.raises(ValueError, match='duplicate'):
        load_inventory(path)
    yaml = tmp_path / 'inventory.yaml'
    yaml.write_text('schema_version: 1')
    with pytest.raises(ValueError, match='TOML or JSON'):
        load_inventory(yaml)


def test_generated_hashes_describe_files_and_partial_write_cleans_only_owned_output(tmp_path, monkeypatch):
    import hashlib
    import xlayer_telemetry.cluster_inventory as compiler
    source = write_inventory(tmp_path)
    output = tmp_path / 'bundle'
    manifest = render_inventory(source, output)
    for relative, digest in manifest['sha256'].items():
        assert hashlib.sha256((output / relative).read_bytes()).hexdigest() == digest
    sibling = tmp_path / 'keep'
    sibling.write_text('user data')
    monkeypatch.setattr(compiler, '_write_private', lambda *args: (_ for _ in ()).throw(OSError('Injected write failure')))
    failed = tmp_path / 'failed-bundle'
    with pytest.raises(OSError, match='Injected'):
        render_inventory(source, failed)
    assert not failed.exists() and sibling.read_text() == 'user data'


def test_generated_component_and_native_registration_budgets_are_not_silently_truncated(monkeypatch):
    import xlayer_telemetry.cluster_inventory as compiler
    monkeypatch.setattr(compiler, 'MAX_COMPONENTS', 2)
    data = {'schema_version': 1, 'cluster': {'name': 'bounded'}, 'nodes': [
        {'name': 'gpu-a', 'address': '10.0.0.2', 'roles': ['rollout'], 'gpus': [0, 1]}]}
    with pytest.raises(ValueError, match='component limits'):
        compile_inventory(data)
    data = inventory()
    data['services'].append({'name': 'alias', 'kind': 'vllm', 'node': 'gpu-a', 'target': '10.0.0.2:8000'})
    with pytest.raises(ValueError, match='duplicate scrape endpoint'):
        compile_inventory(data)


def test_explicit_host_local_home_is_not_expanded_on_the_compilation_host(tmp_path):
    data = inventory()
    data['nodes'][1]['telemetry_home'] = '~/xlayer-gpu-a'
    output = tmp_path / 'portable'
    render_inventory(write_inventory(tmp_path, data), output)
    contents = (output / 'nodes/gpu-a.toml').read_text()
    assert 'TELEMETRY_HOME = "~/xlayer-gpu-a"' in contents
    assert str(Path.home()) not in contents
