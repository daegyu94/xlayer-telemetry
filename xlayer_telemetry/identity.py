"""Unambiguous filename encoding for producer-owned telemetry streams."""

from __future__ import annotations
import hashlib
import json


def producer_filename_stem(producer: str, role: str, worker_id: str, *, node: str | None = None, run_id: str | None = None) -> str:
    # Percent is not accepted in producer identifiers, so escaping hyphens is
    # reversible while retaining existing names for common simple identifiers.
    stem = "-".join(value.replace("-", "%2D") for value in (producer, role, worker_id))
    # @ is forbidden in context identifiers. Keep the producer prefix for
    # existing glob readers while separating node-local worker IDs and runs.
    filename = stem + (f"@{node}@{run_id}" if node is not None and run_id is not None else "")
    if len(filename) > 240:
        identity = json.dumps([producer, role, worker_id, node, run_id], separators=(",", ":"))
        return stem[:120] + "@" + hashlib.sha256(identity.encode()).hexdigest()
    return filename
