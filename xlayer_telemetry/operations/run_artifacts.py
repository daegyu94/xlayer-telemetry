"""Read saved run state without treating persisted files as live health."""

from __future__ import annotations

import json
from pathlib import Path

from ..measurements import finite_number


def _object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _timestamp(sample: dict) -> float | None:
    for key in ("observed_at", "timestamp", "timestamp_unix_seconds"):
        if key in sample:
            return finite_number(sample[key])
    nanos = finite_number(sample.get("timestamp_unix_nano"))
    return nanos / 1e9 if nanos is not None else None


def read_run_state(run: Path, *, now: float, max_age_seconds: float = 300) -> dict:
    """Return manifest context, recorded outcome and the saved trainer snapshot.

    A stored ``running`` heartbeat is not proof that a workload is still alive.
    Identity comes from snapshot content, and freshness from producer time.
    Identity-incomplete legacy records are accepted only under the exact old
    ``verl-trainer-driver.json`` name and marked ``legacy_unverified``. Explicit
    identity conflicts are never accepted, even in a legacy file. Multiple
    observer nodes without a manifest observer remain ambiguous.
    """
    now = finite_number(now)
    max_age_seconds = finite_number(max_age_seconds)
    if now is None or max_age_seconds is None or max_age_seconds <= 0:
        raise ValueError("now must be finite and max_age_seconds must be finite and positive")
    manifest = _object(run / "telemetry-manifest.json")
    settings = manifest.get("configuration")
    settings = settings if isinstance(settings, dict) else {}
    run_id = _string(manifest.get("run_id"))
    observer = _string(settings.get("observer_node"))
    health = _object(run / "telemetry-health.json")
    workload = health.get("workload")
    workload = workload if isinstance(workload, dict) else {}
    exit_code = workload.get("exit_code")
    result = {
        "path": str(run), "run_id": run_id,
        "cluster": _string(settings.get("cluster")), "observer_node": observer,
        "execution_mode": _string(settings.get("execution_mode")),
        "step": None, "telemetry": _string(health.get("status")),
        "workload": {"status": _string(workload.get("status")) or "unknown",
                     "exit_code": exit_code if type(exit_code) is int else None,
                     "recorded_at": finite_number(health.get("observed_at"))},
        "snapshot": {"scope": "stored_artifact", "health": "unavailable",
                     "observed_at": None, "age_seconds": None, "path": None,
                     "node": None, "identity": "unknown"},
    }
    candidates = []
    try:
        paths = sorted((run / "telemetry-metrics").glob("*.json"))
    except OSError:
        paths = []
    for path in paths:
        sample = _object(path)
        if not sample or sample.get("schema_version", 2) != 2:
            continue
        expected = {"producer": "verl", "role": "trainer", "worker_id": "driver"}
        if run_id is not None:
            expected["run_id"] = run_id
        if observer is not None:
            expected["node"] = observer
        if any(key in sample and sample[key] is not None and sample[key] != value
               for key, value in expected.items()):
            continue
        identity_keys = ("producer", "role", "worker_id", "run_id", "node")
        complete = all(_string(sample.get(key)) is not None for key in identity_keys)
        if not complete and path.name != "verl-trainer-driver.json":
            continue
        timestamp = _timestamp(sample)
        if timestamp is None:
            continue
        candidates.append((complete, timestamp, path, sample))
    if not candidates:
        return result
    # Prefer explicitly identified records to a copied compatibility snapshot.
    if any(item[0] for item in candidates):
        candidates = [item for item in candidates if item[0]]
    identities = {(item[3].get("run_id"), item[3].get("node")) for item in candidates}
    if len(identities) > 1:
        result["snapshot"]["identity"] = "ambiguous"
        return result
    complete, timestamp, path, sample = max(candidates, key=lambda item: (item[1], str(item[2])))
    age = now - timestamp
    step = sample.get("step")
    result["step"] = step if type(step) is int and step >= 0 else None
    result["snapshot"].update(
        health="clock_skew" if age < -5 else "fresh" if age <= max_age_seconds else "stale",
        observed_at=timestamp, age_seconds=age, path=str(path), node=_string(sample.get("node")),
        identity="verified" if complete else "legacy_unverified",
    )
    return result
