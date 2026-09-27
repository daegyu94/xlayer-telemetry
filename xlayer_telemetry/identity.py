"""Unambiguous filename encoding for producer-owned telemetry streams."""

from __future__ import annotations


def producer_filename_stem(producer: str, role: str, worker_id: str) -> str:
    # Percent is not accepted in producer identifiers, so escaping hyphens is
    # reversible while retaining existing names for common simple identifiers.
    return "-".join(value.replace("-", "%2D") for value in (producer, role, worker_id))
