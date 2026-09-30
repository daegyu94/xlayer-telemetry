"""Republish application metric snapshots through Node Exporter textfiles."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from xlayer_telemetry.metrics.prometheus import GaugeSample, validate_sample, write_gauges
from xlayer_telemetry.measurements import finite_number


_IDENTITY_FIELDS = ("run_id", "producer", "role", "worker_id", "node")


def _select_latest(selected: dict, snapshot: dict) -> None:
    identity = tuple(snapshot[key] for key in _IDENTITY_FIELDS)
    previous = selected.get(identity)
    observed = finite_number(snapshot.get("observed_at"))
    prior = finite_number(previous.get("observed_at")) if previous is not None else None
    if previous is None or (observed is not None and (prior is None or observed >= prior)):
        selected[identity] = snapshot


def _iter_snapshots(metrics_dir: Path) -> list[dict]:
    snapshots: dict[tuple[str, ...], dict] = {}
    for path in sorted(metrics_dir.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if (isinstance(value, dict) and value.get("schema_version") == 2
                    and all(isinstance(value.get(key), str) for key in _IDENTITY_FIELDS)):
                _select_latest(snapshots, value)
        except (OSError, ValueError):
            continue
    return list(snapshots.values())


def collect_snapshots(metrics_dirs: list[Path], run_roots: list[Path], *,
                      node: str | None = None, max_age_seconds: float | None = None,
                      now: float | None = None) -> list[dict]:
    """Discover immediate run children on every poll; never recurse unboundedly.

    Explicit directories retain legacy behavior unless an age limit is supplied.
    Run-root discovery excludes runs with a terminal telemetry health record.
    """
    now = time.time() if now is None else now
    discovered = {path.resolve() for root in run_roots
                  for path in root.glob("*/telemetry-metrics") if path.is_dir()}
    directories = discovered | {path.resolve() for path in metrics_dirs}
    selected: dict[tuple[str, ...], dict] = {}
    for directory in sorted(directories):
        if directory in discovered:
            try:
                health = json.loads((directory.parent / "telemetry-health.json").read_text())
                if isinstance(health, dict) and health.get("workload", {}).get("status") == "finished":
                    continue
            except (OSError, ValueError, AttributeError):
                pass
        for snapshot in _iter_snapshots(directory):
            observed = finite_number(snapshot.get("observed_at"))
            if node is not None and snapshot["node"] != node:
                continue
            if max_age_seconds is not None and (observed is None or not 0 <= now-observed <= max_age_seconds):
                continue
            _select_latest(selected, snapshot)
    return list(selected.values())


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
    parser.add_argument("--metrics-dir", action="append", type=Path, default=[])
    parser.add_argument("--runs-root", action="append", type=Path, default=[], help="Discover ROOT/*/telemetry-metrics")
    parser.add_argument("--max-age-seconds", type=float, help="Default 300 with runs-root; unlimited for legacy metrics-dir")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--textfile-dir", required=True, type=Path)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--node", help="Only publish snapshots for this logical collector node")
    args = parser.parse_args()
    if not args.metrics_dir and not args.runs_root:
        parser.error("at least one --metrics-dir or --runs-root is required")
    if finite_number(args.interval) is None or args.interval <= 0:
        parser.error("interval must be finite and positive")
    max_age = args.max_age_seconds if args.max_age_seconds is not None else (300 if args.runs_root else None)
    if max_age is not None and (finite_number(max_age) is None or max_age <= 0):
        parser.error("max-age-seconds must be finite and positive")
    while True:
        snapshots = collect_snapshots(args.metrics_dir, args.runs_root, node=args.node, max_age_seconds=max_age)
        write_gauges(args.textfile_dir, "application.prom", build_metrics(snapshots))
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
