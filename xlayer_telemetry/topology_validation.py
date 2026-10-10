"""Read-only validation of operator-declared topology; no discovery or clock changes."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 1024 * 1024
MAX_COMPONENTS = 4096
MAX_EDGES = 8192
MAX_ISSUES = 128
TOPOLOGY_KINDS = ("compute", "storage")
MAPPING_FIELDS = ("resource_node", "device", "storage_system", "gpu", "interface")
_LABEL = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_COMPONENT = re.compile(r"[A-Za-z0-9_.:/-]{1,128}\Z")
_KNOWN_ROLES = {"unknown", "gpu-node", "compute-node", "trainer", "trainer-node", "rollout", "rollout-node", "gpu", "nic", "network", "metadata", "data", "mds", "ds", "ssd", "storage-node", "sandbox", "sandbox-node", "cpu", "memory"}


class _Issues:
    def __init__(self) -> None:
        self.values: list[dict[str, str]] = []
        self.counts: Counter[str] = Counter()

    def add(self, path: str, code: str, message: str, severity: str = "error") -> None:
        self.counts[severity] += 1
        item = {"path": path, "code": code, "severity": severity, "message": message}
        if len(self.values) < MAX_ISSUES:
            self.values.append(item)
        elif severity == "error":
            # Warnings must not hide a later actionable error at the report cap.
            index = next((i for i, existing in enumerate(self.values) if existing["severity"] == "warning"), None)
            if index is not None:
                self.values[index] = item


def valid_component_id(value: Any) -> bool:
    return isinstance(value, str) and _COMPONENT.fullmatch(value) is not None


def valid_topology_label(value: Any) -> bool:
    if not isinstance(value,str) or len(value)>128 or any(ord(char)<32 or ord(char)==127 for char in value):
        return False
    try:
        return len(value.encode('utf-8'))<=512
    except UnicodeError:
        return False


def _valid_role(value: Any) -> bool:
    return valid_topology_label(value) and bool(value) and value.strip()==value


def declared_mapping(component: dict[str, Any]) -> dict[str, str] | None:
    """Return validated declaration fields, never a guessed publisher/owner mapping."""
    fields = {key: component[key] for key in MAPPING_FIELDS if key in component}
    if not _valid_role(component.get("role")) or not all(isinstance(value, str) and _LABEL.fullmatch(value) for value in fields.values()):
        return None
    return fields


@dataclass
class TopologyFile:
    payload: dict[str, Any] | None
    issues: list[dict[str, str]]
    signature: tuple[int, int, str]


def read_topology_file(path: Path) -> TopologyFile:
    """Read only one bounded regular file; diagnostics never include JSON contents."""
    issues = _Issues()
    signature = (0, 0, "unreadable")
    try:
        metadata = path.stat()
        signature = (metadata.st_mtime_ns, metadata.st_size, "unreadable")
        if not stat.S_ISREG(metadata.st_mode):
            issues.add(path.name, "file_not_regular", "Topology source must be a regular JSON file.")
            return TopologyFile(None, issues.values, signature)
        if metadata.st_size > MAX_FILE_BYTES:
            issues.add(path.name, "file_too_large", "Topology JSON exceeds the 1 MiB file limit.")
            return TopologyFile(None, issues.values, signature)
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        signature = (metadata.st_mtime_ns, len(raw), hashlib.sha256(raw).hexdigest())
        if len(raw) > MAX_FILE_BYTES:
            issues.add(path.name, "file_too_large", "Topology JSON exceeds the 1 MiB file limit.")
            return TopologyFile(None, issues.values, signature)
        payload = json.loads(raw.decode("utf-8"))
    except OSError:
        issues.add(path.name, "file_unreadable", "Topology JSON could not be read.")
        return TopologyFile(None, issues.values, signature)
    except (ValueError, UnicodeError, RecursionError):
        issues.add(path.name, "invalid_json", "Topology source is not valid UTF-8 JSON.")
        return TopologyFile(None, issues.values, signature)
    if not isinstance(payload, dict):
        issues.add(path.name, "root_type_invalid", "Topology JSON root must be an object.")
        return TopologyFile(None, issues.values, signature)
    return TopologyFile(payload, issues.values, signature)


def validate_topology_payload(payload: dict[str, Any], path: str, *, registered_nodes: set[str] | None = None, cluster: str | None = None) -> dict[str, Any]:
    issues = _Issues()
    if "schema_version" in payload and (type(payload["schema_version"]) is not int or payload["schema_version"] != 1):
        issues.add(f"{path}.schema_version", "schema_version_invalid", "Optional schema_version must be the integer 1.")
    if "cluster" in payload:
        value = payload["cluster"]
        if not isinstance(value, str) or not _LABEL.fullmatch(value):
            issues.add(f"{path}.cluster", "cluster_invalid", "Declared cluster must be a safe identifier of 1–128 characters.")
        elif cluster is not None and value != cluster:
            issues.add(f"{path}.cluster", "cluster_mismatch", "Declared cluster differs from the configured cluster.")
    components = payload.get("components")
    edges = payload.get("edges", [])
    if not isinstance(components, list):
        issues.add(f"{path}.components", "components_type_invalid", "components must be an array.")
        components = []
    if not isinstance(edges, list):
        issues.add(f"{path}.edges", "edges_type_invalid", "edges must be an array when supplied.")
        edges = []
    if len(components) > MAX_COMPONENTS:
        issues.add(f"{path}.components", "components_limit", "Topology exceeds the 4096-component validation limit.")
    if len(edges) > MAX_EDGES:
        issues.add(f"{path}.edges", "edges_limit", "Topology exceeds the 8192-edge validation limit.")
    seen: dict[str, tuple[Any, ...]] = {}
    references: set[str] = set()
    for index, component in enumerate(components[:MAX_COMPONENTS]):
        item_path = f"{path}.components[{index}]"
        if not isinstance(component, dict):
            issues.add(item_path, "component_type_invalid", "Each component must be an object.")
            continue
        identity, role = component.get("id"), component.get("role")
        if not valid_component_id(identity):
            issues.add(f"{item_path}.id", "component_id_invalid", "Component id must be a safe identifier of 1–128 characters; slash is allowed.")
        if not _valid_role(role):
            issues.add(f"{item_path}.role", "component_role_invalid", "Component role must be a nonempty printable string of at most 128 characters.")
        elif role not in _KNOWN_ROLES:
            issues.add(f"{item_path}.role", "component_role_custom", "Custom role is preserved; standard role grouping is not established.", "warning")
        mapping_valid = True
        for key in MAPPING_FIELDS:
            if key in component and (not isinstance(component[key], str) or not _LABEL.fullmatch(component[key])):
                issues.add(f"{item_path}.{key}", "mapping_field_invalid", "Mapping field must be a safe string identifier of 1–128 characters.")
                mapping_valid = False
        if valid_component_id(identity):
            signature = (role if isinstance(role, str) else None, *(component.get(key) if isinstance(component.get(key), str) else None for key in MAPPING_FIELDS))
            previous = seen.get(identity)
            if previous is not None:
                issues.add(f"{item_path}.id", "duplicate_component_id", "Component id is duplicated within the topology namespace.")
                if signature[0] != previous[0]:
                    issues.add(f"{item_path}.role", "component_role_conflict", "Duplicate component identity declares conflicting roles.")
                if signature[1:] != previous[1:]:
                    issues.add(item_path, "component_mapping_conflict", "Duplicate component identity declares conflicting resource mappings.")
            else:
                seen[identity] = signature
        owner = component.get("resource_node")
        if mapping_valid and isinstance(owner, str) and _LABEL.fullmatch(owner):
            references.add(owner)
            if registered_nodes is not None and owner not in registered_nodes:
                issues.add(f"{item_path}.resource_node", "resource_node_unregistered", "resource_node is not present in the registered collector inventory.")
        elif owner is None and role != "network":
            issues.add(item_path, "resource_mapping_unverified", "No explicit resource_node is declared; publisher identity is not a resource owner.", "warning")
    if references and registered_nodes is None:
        issues.add(f"{path}.components", "node_registration_unverified", "Collector inventory is unavailable; declared resource-node registration is unverified.", "warning")
    seen_edges: set[tuple[str, str, str]] = set()
    for index, edge in enumerate(edges[:MAX_EDGES]):
        item_path = f"{path}.edges[{index}]"
        if not isinstance(edge, dict):
            issues.add(item_path, "edge_type_invalid", "Each edge must be an object.")
            continue
        source, destination = edge.get("source"), edge.get("destination")
        if not valid_component_id(source) or not valid_component_id(destination):
            issues.add(item_path, "edge_endpoint_invalid", "Edge source and destination must be valid component identifiers.")
            continue
        if source not in seen or destination not in seen:
            issues.add(item_path, "edge_endpoint_unknown", "Edge references a component absent from this topology namespace.")
        relation = edge.get("relation", "")
        if not isinstance(relation, str) or len(relation) > 128 or any(ord(char) < 32 or ord(char) == 127 for char in relation):
            issues.add(f"{item_path}.relation", "edge_relation_invalid", "Edge relation must be a printable string of at most 128 characters.")
            continue
        signature = (source, destination, relation)
        if signature in seen_edges:
            issues.add(item_path, "duplicate_edge", "Repeated declared edge is preserved and de-duplicated by the textfile collector.", "warning")
        seen_edges.add(signature)
    return {"issues": issues.values, "issue_counts": dict(issues.counts), "node_references": sorted(references), "counts": {"components": len(components), "edges": len(edges)}}


def inspect_topology(directory: str | Path, registered_nodes: set[str] | None = None, cluster: str | None = None) -> dict[str, Any]:
    """Inspect declared topology against optional collector inventory, without modifying it."""
    directory = Path(directory)
    issues = _Issues()
    files: list[str] = []
    references: set[str] = set()
    counts = {"components": 0, "edges": 0}
    try:
        is_directory = directory.is_dir()
    except OSError:
        is_directory = False
    if not is_directory:
        issues.add("topology_dir", "directory_unavailable", "Configured topology directory is missing, unreadable or not a directory.")
    else:
        for kind in TOPOLOGY_KINDS:
            path = directory / f"{kind}-topology.json"
            try:
                present = path.exists() or path.is_symlink()
            except OSError:
                present = True
            if not present:
                continue
            files.append(path.name)
            source = read_topology_file(path)
            for item in source.issues:
                issues.add(item["path"], item["code"], item["message"], item["severity"])
            if source.payload is None:
                continue
            validation = validate_topology_payload(source.payload, path.name, registered_nodes=registered_nodes, cluster=cluster)
            for item in validation["issues"]:
                issues.add(item["path"], item["code"], item["message"], item["severity"])
            # Preserve counts of omitted issues without duplicating stored ones.
            stored_counts = Counter(item["severity"] for item in validation["issues"])
            for severity, number in validation["issue_counts"].items():
                issues.counts[severity] += number - stored_counts[severity]
            references.update(validation["node_references"])
            for key in counts:
                counts[key] += validation["counts"][key]
        if not files:
            issues.add("topology_dir", "topology_files_missing", "No compute-topology.json or storage-topology.json was found.")
    return {"status": "error" if issues.counts["error"] else "warning" if issues.counts["warning"] else "ok", "files": files, "counts": counts,
            "node_references": sorted(references), "issues": issues.values, "issue_counts": dict(issues.counts), "issues_truncated": sum(issues.counts.values()) > len(issues.values)}
