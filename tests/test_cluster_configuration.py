"""Cross-file configuration admission, separate from observed resource health."""
import json
from pathlib import Path
import pytest

from xlayer_telemetry import cli
from xlayer_telemetry.operations.config import defaults


def configured(tmp_path):
    value=defaults()
    value.update(TELEMETRY_HOME=str(tmp_path),TELEMETRY_TARGETS='gpu-a=10.0.0.1,ds-a=10.0.0.2',
                 CLUSTER_NAME='poc',ENABLE_GPU_METRICS='0',TOPOLOGY_DIR=str(tmp_path/'topology'))
    Path(value['TOPOLOGY_DIR']).mkdir()
    return value


def test_config_validate_rejects_malformed_topology_before_starting_services(tmp_path,capsys):
    top=tmp_path/'topology';top.mkdir();(top/'compute-topology.json').write_text('{broken')
    config=tmp_path/'config.toml';config.write_text('[telemetry]\nTOPOLOGY_DIR='+json.dumps(str(top))+'\nENABLE_GPU_METRICS=false\n')
    assert cli.main(['--config',str(config),'config','validate'])==2
    assert 'topology' in capsys.readouterr().err.lower()


def test_cross_file_owner_and_cluster_mismatch_are_configuration_errors_not_missing_metric(tmp_path):
    from xlayer_telemetry.operations.cluster import configuration_report
    config=configured(tmp_path)
    Path(config['TOPOLOGY_DIR'],'storage-topology.json').write_text(json.dumps({'components':[
        {'id':'ds-a','role':'data','resource_node':'ghost'}],'edges':[]}))
    diagnosis=tmp_path/'diagnosis.json';diagnosis.write_text(json.dumps({'schema_version':1,'cluster':'other','prometheus':{'url':'http://localhost:19090'}}))
    config['DIAGNOSTICS_CONFIG']=str(diagnosis)
    report=configuration_report(config)
    codes={row['code'] for row in report['issues']}
    assert 'resource_node_unregistered' in codes and 'diagnosis_cluster_mismatch' in codes
    assert report['status']=='invalid' and report['mode']=='offline'
    assert report['observations']=='not_checked'


def test_partial_native_and_replica_collection_are_warnings_without_invented_ownership(tmp_path):
    from xlayer_telemetry.operations.cluster import configuration_report
    config=configured(tmp_path)
    Path(config['TOPOLOGY_DIR'],'compute-topology.json').write_text(json.dumps({'components':[{'id':'gpu-a','role':'gpu-node','resource_node':'gpu-a'}]}))
    native=tmp_path/'sources.json';native.write_text(json.dumps({'schema_version':1,'sources':[
        {'name':'engine','kind':'vllm','target':'shared.internal:8000','labels':{'node':'remote-worker'}}]}))
    config['TELEMETRY_SOURCES_FILE']=str(native)
    report=configuration_report(config)
    assert report['status']=='needs_attention'
    assert all(row['severity']!='error' for row in report['issues'])
    assert 'source_node_not_registered' in {row['code'] for row in report['issues']}
    assert report['counts']['native_endpoints']==1


def test_unknown_live_backend_is_not_node_down_and_does_not_hide_offline_error(tmp_path,monkeypatch):
    from xlayer_telemetry.operations import cluster
    config=configured(tmp_path)
    Path(config['TOPOLOGY_DIR'],'compute-topology.json').write_text('{bad')
    monkeypatch.setattr(cluster,'observed_status',lambda config,**kwargs:{'target_discovery':{'status':'unavailable','error':'OSError'},'collector_targets':[
        {'node':'gpu-a','health':'unavailable'}],'native_sources':{'sources':[]}})
    report=cluster.configuration_report(config,live=True)
    assert report['status']=='invalid' and report['observations']['collector_targets'][0]['health']=='unavailable'
    assert report['observations']['metric_coverage']=='not_checked'
    assert report['system_time_changed'] is False


def static_inventory(tmp_path):
    path = tmp_path / 'inventory.json'
    path.write_text(json.dumps({'schema_version': 1, 'cluster': {'name': 'poc'}, 'nodes': [
        {'name': 'gpu-a', 'address': '10.0.0.1', 'roles': ['rollout'], 'gpus': [0]},
        {'name': 'ds-a', 'address': '10.0.0.2', 'roles': ['ds'], 'devices': ['nvme0n1']}
    ]}))
    return path


def test_inventory_validate_needs_no_installed_config_or_user_state(tmp_path, monkeypatch, capsys):
    from xlayer_telemetry.operations.config import KEYS
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    inventory = static_inventory(tmp_path)
    # --config is not needed, and no service or persistent state is created.
    assert cli.main(['cluster', 'validate', '--inventory', str(inventory), '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'valid' and report['observations'] == 'not_checked'
    assert report['inventory']['counts']['nodes'] == 2
    assert list(tmp_path.iterdir()) == [inventory]


def test_cluster_render_keeps_user_files_and_uses_existing_contract(tmp_path, capsys):
    inventory = static_inventory(tmp_path)
    output = tmp_path / 'bundle'
    assert cli.main(['cluster', 'render', '--inventory', str(inventory), '--output', str(output), '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['diagnosis_generated'] is False
    before = (output / 'manifest.json').read_bytes()
    assert cli.main(['cluster', 'render', '--inventory', str(inventory), '--output', str(output), '--json']) == 2
    assert (output / 'manifest.json').read_bytes() == before


def test_doctor_reports_topology_error_in_structured_checks(tmp_path):
    from xlayer_telemetry.operations.config import load_config
    from xlayer_telemetry.operations.health import doctor
    path = tmp_path / 'config.toml'
    top = tmp_path / 'topology'
    top.mkdir()
    (top / 'compute-topology.json').write_text('{broken')
    path.write_text('[telemetry]\nENABLE_GPU_METRICS=false\nTOPOLOGY_DIR=' + json.dumps(str(top)) + '\n')
    config, _ = load_config(path)
    result = doctor(config, role='server')
    assert result['status'] == 'incomplete'
    assert result['cluster_configuration']['status'] == 'invalid'
    assert any(row['component'] == 'cluster configuration' and row['status'] == 'missing' for row in result['checks'])


def test_live_and_clock_failure_keep_configuration_results(tmp_path, monkeypatch):
    from xlayer_telemetry.operations import cluster, health
    config = configured(tmp_path)
    Path(config['TOPOLOGY_DIR'], 'compute-topology.json').write_text(json.dumps({'components': [], 'edges': []}))
    monkeypatch.setattr(health, 'correlation_preflight', lambda config: {'status': 'not_configured'})
    result = cluster.configuration_report(config, correlation=True)
    assert result['status'] == 'needs_attention'
    assert result['clock_preflight']['status'] == 'not_configured'
    assert result['observations'] == 'not_checked'


def test_documented_inventory_is_accepted_by_cli_and_existing_consumers(tmp_path, capsys):
    inventory = Path(__file__).parents[1] / 'examples/cluster/inventory.toml'
    assert cli.main(['cluster', 'validate', '--inventory', str(inventory), '--json']) == 0
    assert json.loads(capsys.readouterr().out)['counts']['collector_nodes'] == 5
    output = tmp_path / 'bundle'
    assert cli.main(['cluster', 'render', '--inventory', str(inventory), '--output', str(output)]) == 0
    capsys.readouterr()
    assert cli.main(['--config', str(output / 'server.toml'), 'config', 'validate']) == 0


def test_native_and_diagnosis_errors_are_separate_and_do_not_echo_credentials(tmp_path):
    from xlayer_telemetry.operations.cluster import configuration_report
    config = configured(tmp_path)
    Path(config['TOPOLOGY_DIR'], 'compute-topology.json').write_text(json.dumps({'components': []}))
    native = tmp_path / 'bad-native.json'
    native.write_text('{"password":"never-echo-native"')
    config['TELEMETRY_SOURCES_FILE'] = str(native)
    diagnosis = tmp_path / 'bad-diagnosis.json'
    diagnosis.write_text('{"token":"never-echo-diagnosis"')
    config['DIAGNOSTICS_CONFIG'] = str(diagnosis)
    report = configuration_report(config, correlation=True)
    assert report['status'] == 'invalid'
    assert {'invalid_native_sources', 'invalid_diagnosis_config'} <= {row['code'] for row in report['issues']}
    assert 'never-echo' not in json.dumps(report)


def test_native_endpoint_down_requires_attention_even_with_all_collectors_up(tmp_path, monkeypatch):
    from xlayer_telemetry.operations import cluster
    config = configured(tmp_path)
    Path(config['TOPOLOGY_DIR'], 'compute-topology.json').write_text(json.dumps({'components': []}))
    monkeypatch.setattr(cluster, 'observed_status', lambda config, **kwargs: {
        'target_discovery': {'status': 'observed'},
        'collector_targets': [{'node': 'gpu-a', 'health': 'up'}],
        'native_sources': {'sources': [{'telemetry_source': 'vllm', 'status': 'down'}]}
    })
    report = cluster.configuration_report(config, live=True)
    assert report['status'] == 'needs_attention'
    assert 'native_observation_incomplete' in {row['code'] for row in report['issues']}


def test_inventory_cross_checks_explicit_runtime_and_keeps_it_unchanged(tmp_path, monkeypatch, capsys):
    inventory = static_inventory(tmp_path)
    diagnosis = tmp_path / 'diagnosis.json'
    diagnosis.write_text(json.dumps({'schema_version': 1, 'cluster': 'different',
                                    'prometheus': {'url': 'http://127.0.0.1:19090'}}))
    config = tmp_path / 'runtime.toml'
    original = '[telemetry]\nENABLE_GPU_METRICS=false\nDIAGNOSTICS_CONFIG=' + json.dumps(str(diagnosis)) + '\n'
    config.write_text(original)
    monkeypatch.setenv('XLAYER_CONFIG', str(config))
    assert cli.main(['cluster', 'validate', '--inventory', str(inventory), '--json']) == 2
    report = json.loads(capsys.readouterr().out)
    assert 'diagnosis_cluster_mismatch' in {row['code'] for row in report['issues']}
    assert config.read_text() == original


def test_doctor_correlation_preserves_invalid_diagnosis_without_traceback(tmp_path, capsys):
    config = tmp_path / 'config.toml'
    diagnosis = tmp_path / 'diagnosis.json'
    diagnosis.write_text('{bad')
    config.write_text('[telemetry]\nENABLE_GPU_METRICS=false\nDIAGNOSTICS_CONFIG=' + json.dumps(str(diagnosis)) + '\n')
    assert cli.main(['--config', str(config), 'doctor', '--correlation', '--json']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['cluster_configuration']['diagnosis_config'] == 'invalid'
    assert result['correlation_preflight']['status'] == 'invalid_config'


def test_inventory_errors_are_actionable_without_echoing_unvalidated_fields(tmp_path, capsys):
    path = static_inventory(tmp_path)
    data = json.loads(path.read_text())
    data['nodes'].append(data['nodes'][0])
    path.write_text(json.dumps(data))
    assert cli.main(['cluster', 'validate', '--inventory', str(path), '--json']) == 2
    assert 'Duplicate node' in json.loads(capsys.readouterr().out)['error']
    data['secret-private-key'] = 'never-echo-value'
    path.write_text(json.dumps(data))
    assert cli.main(['cluster', 'validate', '--inventory', str(path), '--json']) == 2
    result = capsys.readouterr()
    assert 'unsupported fields' in result.out
    assert 'secret-private-key' not in result.out + result.err
    assert 'never-echo-value' not in result.out + result.err


def test_generated_bundle_feeds_existing_server_launcher_without_starting_services(tmp_path):
    import os
    import re
    import subprocess
    import sys
    from xlayer_telemetry.cluster_inventory import render_inventory
    from xlayer_telemetry.operations.config import load_config, snapshot
    root = Path(__file__).parents[1]
    output = tmp_path / 'bundle'
    render_inventory(root / 'examples/cluster/inventory.toml', output)
    config, _ = load_config(output / 'server.toml')
    config['TELEMETRY_HOME'] = str(tmp_path / 'state')
    server = tmp_path / 'server'
    result = subprocess.run(['bash', str(root / 'scripts/run_telemetry.sh'), 'server', '--config', str(snapshot(config))],
                            env=os.environ | config | {'SERVER_CONFIG_ONLY': '1', 'OUTPUT_DIR': str(server), 'PYTHON': sys.executable},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    prometheus = (server / 'prometheus.yml').read_text()
    assert set(re.findall(r'^\s+nodename: (\S+)$', prometheus, re.M)) == {
        'monitoring-0', 'gpu-0', 'gpu-1', 'metadata-0', 'storage-0'}
    groups = json.loads((server / 'native-targets.json').read_text())
    assert {row['labels']['telemetry_source'] for row in groups} == {'vllm', 'mooncake'}
    assert all('run_id' not in row['labels'] for row in groups)
    assert not (server / 'prometheus-data').exists(), 'Config-only validation must not launch a backend'
    if os.environ.get('PROMTOOL'):
        checked = subprocess.run([os.environ['PROMTOOL'], 'check', 'config', str(server / 'prometheus.yml')],
                                 capture_output=True, text=True, timeout=10)
        assert checked.returncode == 0, checked.stderr


@pytest.mark.parametrize('optional', [{'threefs': None}, {'sandbox': None}])
def test_optional_null_sources_remain_unconfigured_during_identity_checks(tmp_path, optional):
    from xlayer_telemetry.operations.cluster import configuration_report
    config = configured(tmp_path)
    Path(config['TOPOLOGY_DIR'], 'compute-topology.json').write_text(json.dumps({'components': []}))
    diagnosis = tmp_path / 'diagnosis.json'
    diagnosis.write_text(json.dumps({'schema_version': 1, 'cluster': 'poc', 'node': 'gpu-a',
        'prometheus': {'url': 'http://127.0.0.1:19090'}, **optional}))
    config['DIAGNOSTICS_CONFIG'] = str(diagnosis)
    assert configuration_report(config)['status'] == 'valid'


def test_explicit_inventory_is_not_replaced_by_another_clusters_environment(tmp_path, monkeypatch, capsys):
    inventory = static_inventory(tmp_path)
    monkeypatch.setenv('CLUSTER_NAME', 'old-cluster')
    monkeypatch.setenv('TELEMETRY_TARGETS', 'old-node=10.0.9.9')
    assert cli.main(['cluster', 'validate', '--inventory', str(inventory), '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['cluster'] == 'poc' and result['counts']['collector_nodes'] == 2
