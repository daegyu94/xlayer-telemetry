"""Numeric validation and summaries for telemetry and profiling."""

import math
import statistics


def finite_number(value: object) -> float | None:
    """Accept finite numeric measurements without overflowing on JSON integers."""
    if type(value) not in (int, float):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def summarize_steps(seconds: list[float], warmup: int) -> dict[str, float | int]:
    """Use nearest-rank percentiles after excluding warmup steps."""
    if not 0 <= warmup < len(seconds):
        raise ValueError("warmup must leave at least one measured step")
    values = sorted(seconds[warmup:])
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("step durations must be finite and positive")
    return {
        "count": len(values),
        "mean_seconds": statistics.mean(values),
        "p50_seconds": values[math.ceil(len(values) * 0.5) - 1],
        "p95_seconds": values[math.ceil(len(values) * 0.95) - 1],
    }
