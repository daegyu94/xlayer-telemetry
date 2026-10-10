import json
from pathlib import Path

import pytest

from xlayer_telemetry.topology_validation import (
    MAX_COMPONENTS,
    MAX_EDGES,
    MAX_FILE_BYTES,
    inspect_topology,
)


def topology(directory: Path, kind="compute", **changes):
    payload = {
        "components": [
            {"id": "host", "role": "gpu-node", "resource_node": "compute-a"},
            {"id": "host/gpu-0", "role": "gpu", "resource_node": "compute-a", "gpu": "0"},
        ],
        "edges": [{"source": "host", "destination": "host/gpu-0", "relation": "allocated"}],
        **changes,
    }
    (directory / f"{kind}-topology.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def codes(report):
    return {issue["code"] for issue in report["issues"]}


def test_legacy_single_file_and_explicit_slash_ids_remain_valid_without_missing_optional_file_error(tmp_path):
    topology(tmp_path)
    report = inspect_topology(tmp_path, registered_nodes={"compute-a"})
    assert report["status"] == "ok"
    assert report["counts"] == {"components": 2, "edges": 1}
    assert report["node_references"] == ["compute-a"]
    assert report["files"] == ["compute-topology.json"]


@pytest.mark.parametrize("schema_version", [True, False, 0, 2, "1", 1.0, None])
def test_schema_version_must_be_integer_one_when_present(tmp_path, schema_version):
    topology(tmp_path, schema_version=schema_version)
    assert "schema_version_invalid" in codes(inspect_topology(tmp_path, registered_nodes={"compute-a"}))


def test_explicit_v1_and_optional_cluster_are_checked_without_inferred_cluster(tmp_path):
    topology(tmp_path, schema_version=1, cluster="c")
    assert inspect_topology(tmp_path, registered_nodes={"compute-a"}, cluster="c")["status"] == "ok"
    assert "cluster_mismatch" in codes(inspect_topology(tmp_path, registered_nodes={"compute-a"}, cluster="other"))
    topology(tmp_path)
    assert inspect_topology(tmp_path, registered_nodes={"compute-a"}, cluster="other")["status"] == "ok"


@pytest.mark.parametrize("mode", ["missing_directory", "not_directory", "empty", "unrecognized", "malformed", "wrong_root"])
def test_missing_or_malformed_explicit_topology_is_an_actionable_error(tmp_path, mode):
    path = tmp_path
    if mode == "missing_directory":
        path = tmp_path / "absent"
    elif mode == "not_directory":
        path = tmp_path / "plain-file"
        path.write_text("file")
    elif mode == "unrecognized":
        (tmp_path / "custom.json").write_text("{}")
    elif mode == "malformed":
        (tmp_path / "compute-topology.json").write_text('{"auth_token":"do-not-echo",')
    elif mode == "wrong_root":
        (tmp_path / "compute-topology.json").write_text("[]")
    report = inspect_topology(path)
    assert report["status"] == "error"
    assert report["issues"]
    assert "do-not-echo" not in json.dumps(report)
    for issue in report["issues"]:
        assert {"path", "code", "severity", "message"} <= issue.keys()


@pytest.mark.parametrize("changed,expected", [
    ({"id": "host", "role": "gpu-node", "resource_node": "compute-a"}, "duplicate_component_id"),
    ({"id": "host", "role": "gpu-node", "resource_node": "other"}, "component_mapping_conflict"),
    ({"id": "host", "role": "rollout", "resource_node": "compute-a"}, "component_role_conflict"),
])
def test_duplicate_component_identity_and_conflicting_role_or_owner_are_rejected(tmp_path, changed, expected):
    topology(tmp_path, components=[{"id": "host", "role": "gpu-node", "resource_node": "compute-a"}, changed], edges=[])
    report = inspect_topology(tmp_path, registered_nodes={"compute-a", "other"})
    assert report["status"] == "error"
    assert expected in codes(report)


@pytest.mark.parametrize("field,value", [("resource_node", "bad node"), ("device", True), ("gpu", 0), ("interface", "x" * 129), ("storage_system", "secret\nvalue")])
def test_invalid_mapping_does_not_echo_payload_and_cannot_supply_owner_identity(tmp_path, field, value):
    topology(tmp_path, components=[{"id": "host", "role": "gpu-node", field: value}], edges=[])
    report = inspect_topology(tmp_path)
    assert "mapping_field_invalid" in codes(report)
    assert "secret" not in json.dumps(report)
    assert report["node_references"] == []


def test_registration_unknown_is_warning_but_declared_unknown_owner_and_dangling_edge_are_errors(tmp_path):
    topology(tmp_path)
    unverified = inspect_topology(tmp_path)
    assert unverified["status"] == "warning"
    assert "node_registration_unverified" in codes(unverified)
    unregistered = inspect_topology(tmp_path, registered_nodes={"different"})
    assert "resource_node_unregistered" in codes(unregistered)
    assert unregistered["status"] == "error"
    topology(tmp_path, edges=[{"source": "host", "destination": "absent"}])
    assert "edge_endpoint_unknown" in codes(inspect_topology(tmp_path, registered_nodes={"compute-a"}))


def test_missing_role_is_invalid_but_unknown_and_custom_roles_are_not_forced_into_an_enum(tmp_path):
    topology(tmp_path, components=[{"id": "host", "role": "", "resource_node": "compute-a"}], edges=[])
    assert "component_role_invalid" in codes(inspect_topology(tmp_path, registered_nodes={"compute-a"}))
    topology(tmp_path, components=[{"id": "host", "role": "custom-worker", "resource_node": "compute-a"}], edges=[])
    report = inspect_topology(tmp_path, registered_nodes={"compute-a"})
    assert report["status"] == "warning"
    assert "component_role_custom" in codes(report)
    topology(tmp_path, components=[{"id": "host", "role": "unknown", "resource_node": "compute-a"}], edges=[])
    assert inspect_topology(tmp_path, registered_nodes={"compute-a"})["status"] == "ok"


def test_read_and_entity_limits_bound_validation_without_raw_payload(tmp_path):
    path = tmp_path / "compute-topology.json"
    path.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    assert "file_too_large" in codes(inspect_topology(tmp_path))
    topology(tmp_path, components=[{"id": f"n-{i}", "role": "network"} for i in range(MAX_COMPONENTS + 1)], edges=[])
    assert "components_limit" in codes(inspect_topology(tmp_path))
    topology(tmp_path, edges=[{"source": "host", "destination": "host/gpu-0"}] * (MAX_EDGES + 1))
    assert "edges_limit" in codes(inspect_topology(tmp_path))
