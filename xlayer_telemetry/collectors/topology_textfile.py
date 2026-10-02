"""Publish supplied compute and storage topology JSON as Prometheus gauges."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from xlayer_telemetry.metrics.prometheus import GaugeSample, write_gauges
from xlayer_telemetry.measurements import finite_number


def build_gauges(directory: Path) -> list[GaugeSample]:
    gauges = []
    seen = set()
    def add(name: str, help: str, labels: dict[str, str]) -> None:
        identity = (name, tuple(sorted(labels.items())))
        if identity not in seen:
            gauges.append(GaugeSample(name, help, 1, labels))
            seen.add(identity)

    for kind in ("compute", "storage"):
        path = directory / f"{kind}-topology.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        components = payload.get("components", [])
        for component in components if isinstance(components, list) else []:
            if (not isinstance(component, dict) or not isinstance(component.get("id"), str)
                    or not component["id"] or not isinstance(component.get("role", ""), str)):
                continue
            add("telemetry_topology_component_info", "Supplied topology component.",
                {"kind": kind, "component": component["id"], "role": component.get("role", "")})
        edges = payload.get("edges", [])
        for edge in edges if isinstance(edges, list) else []:
            if (not isinstance(edge, dict) or not all(isinstance(edge.get(key), str) and edge[key]
                                                     for key in ("source", "destination"))
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
