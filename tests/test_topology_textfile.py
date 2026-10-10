import json
from pathlib import Path

from xlayer_telemetry.collectors.topology_textfile import build_gauges


def test_build_gauges_emits_supplied_components_and_edges(tmp_path: Path) -> None:
    (tmp_path / "storage-topology.json").write_text(json.dumps({
        "components": [{"id": "meta-0", "role": "metadata"}, {"id": "data-0", "role": "data"}],
        "edges": [{"source": "client-0", "destination": "meta-0", "relation": "metadata"}],
    }))

    gauges = build_gauges(tmp_path)

    assert {(g.name, g.labels.get("component")) for g in gauges} >= {
        ("telemetry_topology_component_info", "meta-0"),
        ("telemetry_topology_component_info", "data-0"),
    }
    assert any(g.name == "telemetry_topology_edge_info" and g.labels["relation"] == "metadata" for g in gauges)


def test_explicit_storage_mapping_keeps_resource_owner_separate_from_publisher(tmp_path):
    (tmp_path/'storage-topology.json').write_text(json.dumps({'components':[
        {'id':'mds-service','role':'metadata','resource_node':'meta-host','storage_system':'3fs'},
        {'id':'ds-service','role':'data','resource_node':'data-host','storage_system':'3fs'},
        {'id':'ds-device','role':'ssd','resource_node':'data-host','device':'nvme0n1','storage_system':'3fs'},
        {'id':'unknown-service','role':'data'},
    ]}))
    nodes={g.labels['component']:g.labels for g in build_gauges(tmp_path)}
    assert nodes['mds-service']['resource_node']=='meta-host'
    assert nodes['ds-device']['device']=='nvme0n1'
    assert 'resource_node' not in nodes['unknown-service']
    assert all('nodename' not in labels and 'instance' not in labels for labels in nodes.values())


def test_conflicting_or_invalid_mapping_does_not_supply_storage_owner(tmp_path):
    (tmp_path/'storage-topology.json').write_text(json.dumps({'components':[
        {'id':'ds','role':'data','resource_node':'a'},
        {'id':'ds','role':'data','resource_node':'b'},
        {'id':'bad','role':'data','resource_node':'host" bad'},
    ]}))
    rows=[g.labels for g in build_gauges(tmp_path)]
    assert rows and all('resource_node' not in row for row in rows)
def test_compute_resource_mapping_is_explicit_and_conflicts_are_not_attributed(tmp_path):
    import json
    (tmp_path/'compute-topology.json').write_text(json.dumps({'components':[
        {'id':'gpu','role':'gpu','resource_node':'compute-a','gpu':'0'},
        {'id':'nic','role':'nic','resource_node':'compute-a','interface':'ens5'},
        {'id':'conflict','role':'gpu','resource_node':'a','gpu':'0'},
        {'id':'conflict','role':'gpu','resource_node':'b','gpu':'0'},
        {'id':'unknown','role':'gpu'}]}))
    values=build_gauges(tmp_path)
    gpu=next(row for row in values if row.labels['component']=='gpu')
    assert gpu.labels['resource_node']=='compute-a' and gpu.labels['gpu']=='0'
    nic=next(row for row in values if row.labels['component']=='nic')
    assert nic.labels['interface']=='ens5'
    assert all('resource_node' not in row.labels for row in values if row.labels['component'] in {'conflict','unknown'})


def test_malformed_file_is_reported_once_and_valid_other_namespace_survives(tmp_path, caplog):
    malformed = tmp_path / 'compute-topology.json'
    malformed.write_text('{"secret":"never-log-this",')
    (tmp_path / 'storage-topology.json').write_text(json.dumps({'components': [{'id': 'ds', 'role': 'data'}]}))
    gauges = build_gauges(tmp_path)
    assert any(sample.labels.get('component') == 'ds' for sample in gauges)
    assert 'invalid_json' in caplog.text
    assert 'never-log-this' not in caplog.text
    count = len(caplog.records)
    build_gauges(tmp_path)
    assert len(caplog.records) == count
    malformed.write_text('[]')
    build_gauges(tmp_path)
    assert 'root_type_invalid' in caplog.text


def test_role_conflict_keeps_raw_inventory_without_assigning_a_resource_owner(tmp_path):
    (tmp_path / 'compute-topology.json').write_text(json.dumps({'components': [
        {'id': 'shared', 'role': 'trainer', 'resource_node': 'host'},
        {'id': 'shared', 'role': 'rollout', 'resource_node': 'host'},
    ]}))
    gauges = build_gauges(tmp_path)
    assert {sample.labels['role'] for sample in gauges} == {'trainer', 'rollout'}
    assert all('resource_node' not in sample.labels for sample in gauges)
