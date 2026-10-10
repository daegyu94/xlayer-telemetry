"""Exporter availability and applied target configuration are separate facts."""
import json
import pytest
from xlayer_telemetry.operations.config import load_config
from xlayer_telemetry.operations import health


def check(tmp_path, monkeypatch, addresses, *, discovered=True, configured='10.0.0.20'):
    path = tmp_path/'config.toml'
    path.write_text('[telemetry]\n'+f'TELEMETRY_HOME={json.dumps(str(tmp_path))}\n'+
        f'NODE_NAME="gpu-a"\nNODE_ADDR="{configured}"\nTELEMETRY_TARGETS="gpu-a={configured}"\nENABLE_GPU_METRICS=false\n')
    config, _ = load_config(path)
    monkeypatch.setattr(health, 'process_identity', lambda path: {'process': 'running'})
    targets = [{'labels': {'job': 'telemetry', 'cluster': config['CLUSTER_NAME'], 'nodename': 'gpu-a'},
                'health': 'up', **({'scrapeUrl': f'http://{value}:19100/metrics'} if value else {})} for value in addresses]
    def probe(url, **kwargs):
        if '/targets' in url and not discovered: return {'health': 'unreachable', 'data': None}
        return {'health': 'healthy', 'data': {'database': 'ok', 'status': 'success', 'data': {'activeTargets': targets}}}
    monkeypatch.setattr(health, 'probe', probe)
    return health.status(config, role='server')


@pytest.mark.parametrize('addresses,state', [(['10.0.0.10'], 'mismatch'), ([None], 'unknown'),
    (['10.0.0.20','10.0.0.10'], 'ambiguous')])
def test_up_old_unknown_or_duplicate_target_does_not_verify_applied_config(tmp_path, monkeypatch, addresses, state):
    result = check(tmp_path, monkeypatch, addresses)
    assert result['status'] == 'degraded'
    assert result['collector_targets'][0]['health'] == 'up'
    assert result['collector_targets'][0]['configuration_status'] == state


def test_updated_address_matches_without_claiming_hardware_ownership(tmp_path, monkeypatch):
    result = check(tmp_path, monkeypatch, ['10.0.0.20'])
    assert result['status'] == 'healthy'
    assert result['collector_targets'][0]['configuration_status'] == 'matched'


def test_unavailable_discovery_does_not_claim_collector_failure(tmp_path, monkeypatch):
    result = check(tmp_path, monkeypatch, [], discovered=False)
    assert result['services']['node']['health'] == 'unknown'
    assert result['collector_targets'][0]['health'] == 'unavailable'
    assert 'PROMETHEUS_URL' in result['target_discovery']['action']


def test_hostname_to_ip_equivalence_is_not_guessed(tmp_path, monkeypatch):
    result = check(tmp_path, monkeypatch, ['10.0.0.20'], configured='node.internal')
    assert result['status'] == 'degraded'
    assert result['collector_targets'][0]['configuration_status'] == 'unknown'
