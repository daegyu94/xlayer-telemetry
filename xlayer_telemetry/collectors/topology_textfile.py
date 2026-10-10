"""Publish supplied compute and storage topology JSON as Prometheus gauges."""

from __future__ import annotations

import argparse
import logging
import time
from collections import OrderedDict
from pathlib import Path

from xlayer_telemetry.metrics.prometheus import GaugeSample, write_gauges
from xlayer_telemetry.measurements import finite_number
from xlayer_telemetry.topology_validation import (
    MAPPING_FIELDS,
    MAX_COMPONENTS,
    MAX_EDGES,
    TOPOLOGY_KINDS,
    declared_mapping,
    read_topology_file,
    valid_component_id,
    valid_topology_label,
    validate_topology_payload,
)


_LOGGER = logging.getLogger(__name__)
_FILE_SIGNATURES: OrderedDict[str, tuple[int, int, str]] = OrderedDict()


def _log_quality(path: Path, signature: tuple[int, int, str], issues: list[dict[str, str]]) -> None:
    key = str(path.absolute())
    unchanged = _FILE_SIGNATURES.get(key) == signature
    _FILE_SIGNATURES[key] = signature
    _FILE_SIGNATURES.move_to_end(key)
    while len(_FILE_SIGNATURES) > len(TOPOLOGY_KINDS):
        _FILE_SIGNATURES.popitem(last=False)
    if issues and not unchanged:
        # Filename and static issue codes only: malformed JSON may contain
        # credentials, so neither its payload nor decoder exception is logged.
        codes = sorted({issue['code'] for issue in issues})[:8]
        _LOGGER.warning("Topology source %s: %s; raw declarations remain configured/unverified.", path.name, ", ".join(codes))


def build_gauges(directory: Path) -> list[GaugeSample]:
    gauges = []
    seen = set()
    def add(name: str, help: str, labels: dict[str, str]) -> None:
        if not all(valid_topology_label(value) for value in labels.values()):
            return
        identity = (name, tuple(sorted(labels.items())))
        if identity not in seen:
            gauges.append(GaugeSample(name, help, 1, labels))
            seen.add(identity)

    for kind in TOPOLOGY_KINDS:
        path = directory / f"{kind}-topology.json"
        try:
            present = path.exists() or path.is_symlink()
        except OSError:
            present = True
        if not present:
            continue
        source = read_topology_file(path)
        if source.payload is None:
            _log_quality(path, source.signature, source.issues)
            continue
        payload = source.payload
        quality = validate_topology_payload(payload, path.name)
        _log_quality(path, source.signature, [*source.issues, *quality['issues']])
        components = payload.get("components", [])
        components = components[:MAX_COMPONENTS] if isinstance(components, list) else []
        mappings = {}
        for component in components:
            if isinstance(component, dict) and isinstance(component.get('id'), str):
                mappings.setdefault(component['id'], set()).add(tuple(component.get(key,'') if isinstance(component.get(key,''),str) else None
                    for key in ('role', *MAPPING_FIELDS)))
        for component in components:
            if (not isinstance(component, dict) or not valid_component_id(component.get("id"))
                    or not isinstance(component.get("role", ""), str)):
                continue
            labels = {"kind": kind, "component": component["id"], "role": component.get("role", "")}
            # This is an operator declaration, not discovery or a dependency.
            # Keep the publisher's scrape identity separate from the resource
            # owner. Conflicting/invalid mappings remain unclassified inventory.
            fields = declared_mapping(component)
            if len(mappings.get(component['id'], [])) == 1 and fields is not None:
                labels.update(fields)
            add("telemetry_topology_component_info", "Supplied topology component; resource mapping is declared, not observed.", labels)
        edges = payload.get("edges", [])
        edges = edges[:MAX_EDGES] if isinstance(edges, list) else []
        for edge in edges:
            if (not isinstance(edge, dict) or not all(valid_component_id(edge.get(key)) for key in ("source", "destination"))
                    or not isinstance(edge.get("relation", ""), str)):
                continue
            add("telemetry_topology_edge_info", "Supplied topology edge.",
                {"kind": kind, "source": edge["source"], "destination": edge["destination"], "relation": edge.get("relation", "")})
    return gauges


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topology-dir", type=Path, required=True)
    parser.add_argument("--textfile-dir", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=10)
    args = parser.parse_args()
    if finite_number(args.interval) is None or args.interval <= 0:
        parser.error("interval must be finite and positive")
    while True:
        write_gauges(args.textfile_dir, "topology.prom", build_gauges(args.topology_dir))
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
