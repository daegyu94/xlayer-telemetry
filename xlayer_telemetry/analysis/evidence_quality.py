"""Describe query resolution without mistaking evaluations for raw scrapes."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..measurements import finite_number

_DURATION = re.compile(r"\[([^]:]+)(?::[^\]]*)?\]")
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d|w|y)")
_SCALE = {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
_SELECTOR = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]*\{[^{}]*\}")


def timestamp_query(query: str) -> str | None:
    """Only inspect a single explicit source; never timestamp a computed rate."""
    selectors = set(_SELECTOR.findall(query))
    if len(selectors) == 1:
        return f"timestamp({next(iter(selectors))})"
    if not selectors and re.fullmatch(r"[A-Za-z_:][A-Za-z0-9_:]*", query):
        return f"timestamp({query})"
    return None


def quality(query: str, start: float, end: float, query_step: float,
            stats: Mapping[str, Any] | None, *, source: Mapping[str, Any] | None = None) -> dict:
    duration = end-start
    windows = []
    range_unknown = False
    for raw in _DURATION.findall(_SELECTOR.sub("", query)):
        parts = _DURATION_PART.findall(raw)
        if not parts or "".join(value+unit for value, unit in parts) != raw:
            range_unknown = True
        else:
            windows.append(sum(float(value)*_SCALE[unit] for value, unit in parts))
    lookback = None if range_unknown else max(windows) if windows else 0
    evaluations = finite_number((stats or {}).get("sample_count"))
    source = source or {}
    last = finite_number(source.get("last_source_timestamp"))
    age = max(0, end-last) if last is not None else None
    warnings = []
    if lookback is None:
        warnings.append("range_window_unknown")
    elif lookback > duration:
        warnings.append("range_window_exceeds_interval")
    if query_step > duration:
        warnings.append("query_step_exceeds_interval")
    if evaluations is not None and evaluations < 2:
        warnings.append("fewer_than_two_query_evaluations")
    if last is None:
        warnings.append("source_freshness_unknown")
    return {"interval_seconds": duration, "query_step_seconds": query_step,
            "range_window_seconds": lookback, "evaluation_count": evaluations,
            "evaluation_count_kind": "query_evaluations_not_scrapes",
            "observed_source_samples": source.get("observed_source_samples"),
            "source_sample_count_kind": "distinct_timestamps_seen_at_query_evaluations_not_complete_scrape_count",
            "last_source_timestamp": last, "source_age_seconds": age,
            "freshness": "observed" if last is not None else "unknown",
            "source_coverage": "returned_series_only" if last is not None else "unknown",
            "warnings": warnings}


def check_source(client, query: str, start: float, end: float, step: float) -> dict:
    expression = timestamp_query(query)
    if expression is None or not hasattr(client, "query_range_detail"):
        return {}
    try:
        detail = client.query_range_detail(expression, start, end, step)
        series = detail.get("series", [])
        last = [finite_number(item.get("stats", {}).get("max")) for item in series]
        last = [value for value in last if value is not None]
        timestamps = [stamp for item in series for stamp in item.get("source_timestamps", [])]
        return {"last_source_timestamp": min(last) if last else None,
                "observed_source_samples": sum(len(set(stamp for stamp in item.get("source_timestamps", [])
                                                            if start <= stamp <= end)) for item in series) if timestamps else None}
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return {}


def validate_sampling(config: Mapping[str, Any]) -> None:
    if not isinstance(config, Mapping) or type(config.get("check_source_freshness", False)) is not bool:
        raise ValueError("sampling.check_source_freshness must be boolean")


def validate_quality(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping) or set(value)-{"current", "baseline"}:
        raise ValueError("sampling_quality needs current/baseline quality objects")
    fields = set(quality("", 0, 1, 1, None))
    for item in value.values():
        if not isinstance(item, Mapping) or set(item)-fields:
            raise ValueError("unsupported sampling quality fields")
        for key in ("interval_seconds", "query_step_seconds", "range_window_seconds", "evaluation_count",
                    "observed_source_samples", "last_source_timestamp", "source_age_seconds"):
            number = item.get(key)
            if number is not None and (finite_number(number) is None or number < 0):
                raise ValueError("sampling quality measurements must be finite nonnegative numbers")
        allowed_warnings = {"range_window_exceeds_interval", "query_step_exceeds_interval",
                            "fewer_than_two_query_evaluations", "source_freshness_unknown", "range_window_unknown"}
        warnings = item.get("warnings", [])
        if not isinstance(warnings, list) or any(not isinstance(warning, str) or warning not in allowed_warnings for warning in warnings):
            raise ValueError("invalid sampling quality warnings")
        if item.get("freshness", "unknown") not in {"unknown", "observed"}:
            raise ValueError("invalid sampling freshness")
