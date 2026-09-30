"""Republish application metric snapshots through Node Exporter textfiles."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from xlayer_telemetry.metrics.prometheus import GaugeSample, validate_sample, write_gauges
from xlayer_telemetry.measurements import finite_number


def _iter_snapshots(metrics_dir: Path) -> list[dict]:
    snapshots: dict[tuple[str, ...], dict] = {}
    for path in sorted(metrics_dir.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if (isinstance(value, dict) and value.get("schema_version") == 2
                    and all(isinstance(value.get(key), str) for key in
                            ("run_id", "producer", "role", "worker_id", "node"))):
                identity = tuple(str(value.get(key, "")) for key in
                                 ("run_id", "producer", "role", "worker_id", "node"))
                previous = snapshots.get(identity)
                observed = value.get("observed_at")
                previous_at = previous.get("observed_at") if previous else None
                if previous is None or (finite_number(observed) is not None
                                        and (finite_number(previous_at) is None or observed >= previous_at)):
                    snapshots[identity] = value
        except (AttributeError, OSError, ValueError):
            continue
    return list(snapshots.values())


def build_metrics(snapshots: list[dict]) -> list[GaugeSample]:
    metrics = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("samples"), list):
            continue
        labels = {
            "run_id": str(snapshot.get("run_id", "")),
            "producer": str(snapshot.get("producer", "")),
            "role": str(snapshot.get("role", "")),
            "worker_id": str(snapshot.get("worker_id", "")),
            "node": str(snapshot.get("node", "")),
        }
        rank = snapshot.get("rank")
        if type(rank) is int:
            labels["rank"] = str(rank)
        local_rank = snapshot.get("local_rank")
        if type(local_rank) is int:
            labels["local_rank"] = str(local_rank)
        gpu = snapshot.get("gpu")
        if not gpu:
            visible = [device.strip() for device in str(snapshot.get("cuda_visible_devices") or "").split(",")]
            if (type(local_rank) is int and 0 <= local_rank < len(visible)
                    and visible[local_rank] and visible[local_rank] != "-1"):
                gpu = visible[local_rank]
        if gpu:
            labels["gpu"] = str(gpu)
            metrics.append(GaugeSample(
                "training_gpu_allocation",
                "Application worker assignment from CUDA_VISIBLE_DEVICES.",
                1,
                labels,
            ))
        observed_at = snapshot.get("observed_at")
        if finite_number(observed_at) is not None:
            metrics.append(GaugeSample(
                "training_sample_timestamp_seconds",
                "Application-reported sample timestamp.",
                observed_at,
                labels,
            ))
        step = snapshot.get("step")
        if type(step) is int:
            metrics.append(GaugeSample("training_step", "Latest reported training step.", step, labels))
        for sample in snapshot["samples"]:
            if (not isinstance(sample, dict) or not isinstance(sample.get("labels", {}), dict)
                    or finite_number(sample.get("value")) is None):
                continue
            if set(sample.get("labels", {})) & labels.keys():
                continue
            metrics.append(GaugeSample(
                str(sample.get("name", "")),
                f"Application-reported {sample.get('name', '')}.",
                sample.get("value", 0),
                {**labels, **{str(key): str(value) for key, value in sample.get("labels", {}).items()}},
                str(sample.get("kind", "gauge")),
            ))
    valid: list[GaugeSample] = []
    definitions: dict[str, tuple[str, str]] = {}
    identities: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for sample in metrics:
        try:
            validate_sample(sample)
        except (TypeError, ValueError):
            continue
        definition = (sample.help, sample.kind)
        identity = (sample.name, tuple(sorted(sample.labels.items())))
        if (sample.name in definitions and definitions[sample.name] != definition) or identity in identities:
            continue
        definitions[sample.name] = definition
        identities.add(identity)
        valid.append(sample)
    return valid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics-dir", required=True, type=Path)
    parser.add_argument("--textfile-dir", required=True, type=Path)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--node", help="Only publish snapshots for this logical collector node")
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("interval must be positive")
    while True:
        snapshots = _iter_snapshots(args.metrics_dir)
        if args.node:
            snapshots = [item for item in snapshots if item.get("node") == args.node]
        write_gauges(args.textfile_dir, "application.prom", build_metrics(snapshots))
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
