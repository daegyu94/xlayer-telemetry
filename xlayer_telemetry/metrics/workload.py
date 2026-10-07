"""Bounded projection of explicit wrapper outcomes through the node textfile."""

from __future__ import annotations

import json
from pathlib import Path
import re
import time

from xlayer_telemetry.measurements import finite_number
from xlayer_telemetry.metrics.prometheus import GaugeSample


WORKLOAD_METRIC_NAMES = {
    "telemetry_wrapped_workload_state",
    "telemetry_wrapped_workload_observed_timestamp_seconds",
    "telemetry_wrapped_workload_exit_code",
}
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_STATES = ("running", "succeeded", "failed")


def collect_workload_metrics(
    metrics_dirs: list[Path],
    run_roots: list[Path],
    *,
    node: str | None = None,
    max_age_seconds: float | None = None,
    now: float | None = None,
    max_runs: int = 1024,
    max_record_bytes: int = 64 * 1024,
    counters: dict | None = None,
) -> list[GaugeSample]:
    """Read immediate run children, including fresh terminal wrapper reports.

    Missing/old artifacts without explicit wrapper identity are not inferred
    from directory names, application steps, manifest roles, or scrape health.
    Timestamps are wrapper node-clock observations, not exact workload bounds.
    """
    if any(type(value) is not int or value <= 0 for value in (max_runs, max_record_bytes)):
        raise ValueError("workload read limits must be positive integers")
    now = time.time() if now is None else now

    def count(key, amount=1):
        if counters is not None:
            counters[key] = counters.get(key, 0) + amount

    directories = {path.resolve().parent for path in metrics_dirs}
    directories.update(path.resolve().parent for root in run_roots
                       for path in root.glob("*/telemetry-metrics") if path.is_dir())
    candidates = []
    for directory in directories:
        path = directory / "telemetry-health.json"
        try:
            candidates.append((path.stat().st_mtime_ns, str(path), path))
        except OSError:
            continue
    candidates.sort(reverse=True)
    count("workload_limit_drops", max(0, len(candidates) - max_runs))
    selected = {}
    for _stamp, _name, path in candidates[:max_runs]:
        count("workload_reads")
        try:
            with path.open("rb") as stream:
                encoded = stream.read(max_record_bytes + 1)
            if len(encoded) > max_record_bytes:
                raise ValueError("workload record exceeds byte limit")
            record = json.loads(encoded)
            if not isinstance(record, dict) or record.get("schema_version") != 1 or record.get("record_type") != "telemetry_health":
                raise ValueError("unsupported workload artifact")
            observation, workload = record.get("workload_observation"), record.get("workload")
            if not isinstance(observation, dict) or not isinstance(workload, dict):
                raise ValueError("missing explicit workload context")
            if (observation.get("source") != "wrapper_health"
                    or observation.get("boundary_scope") != "wrapped_command"
                    or observation.get("clock_scope") != "node"):
                raise ValueError("unsupported workload source/scope")
            identity = (observation.get("run_id"), observation.get("node"))
            if not all(isinstance(value, str) and _IDENTIFIER.fullmatch(value) for value in identity):
                raise ValueError("invalid workload identity")
            if node is not None and identity[1] != node:
                continue
            observed_at = finite_number(observation.get("observed_at"))
            if observed_at is None or observed_at < 0:
                raise ValueError("unknown workload observation time")
            if now < observed_at or (max_age_seconds is not None and now - observed_at > max_age_seconds):
                continue
            status, code = workload.get("status"), workload.get("exit_code")
            if status == "starting" and code is None:
                continue  # The initial health artifact precedes child launch.
            if status == "running" and code is None:
                state = "running"
            elif status == "finished" and type(code) is int and 0 <= code <= 255:
                state = "succeeded" if code == 0 else "failed"
            else:
                raise ValueError("unknown workload status/exit code")
            previous = selected.get(identity)
            if previous is None or observed_at > previous[0]:
                selected[identity] = (observed_at, state, code)
        except (OSError, ValueError, TypeError, OverflowError):
            count("workload_rejections")
    samples = []
    for (run_id, owner_node), (observed_at, state, code) in sorted(selected.items()):
        labels = {"run_id": run_id, "node": owner_node, "producer": "xlayer",
                  "role": "launcher", "worker_id": "wrapper", "source": "wrapper_health",
                  "boundary_scope": "wrapped_command"}
        samples.extend(GaugeSample(
            "telemetry_wrapped_workload_state",
            "Latest explicit wrapper report; covers the wrapped command only.",
            int(state == candidate), {**labels, "state": candidate}) for candidate in _STATES)
        samples.append(GaugeSample(
            "telemetry_wrapped_workload_observed_timestamp_seconds",
            "Wrapper node-clock observation time, not a phase boundary.", observed_at, labels))
        if code is not None:
            samples.append(GaugeSample("telemetry_wrapped_workload_exit_code",
                                       "Exit code explicitly reported by the wrapped command launcher.",
                                       code, labels))
    return samples
