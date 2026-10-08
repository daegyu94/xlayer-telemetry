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
