"""Describe query resolution without mistaking evaluations for raw scrapes."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..measurements import finite_number
from ..prometheus import MAX_ANNOTATION_COUNT, MAX_RESULT_POINTS, MAX_RESULT_SERIES

_DURATION = re.compile(r"\[([^]:]+)(?::[^\]]*)?\]")
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d|w|y)")
_SCALE = {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
_SELECTOR = re.compile(r'[A-Za-z_:][A-Za-z0-9_:]*\{(?:[^{}"\\]|"(?:\\.|[^"\\])*")*\}')
_SOURCE_WRAPPERS = re.compile(
    r'\b(?:sum|avg|min|max|count|stddev|stdvar|rate|irate|increase|delta|idelta|'
    r'clamp_min|clamp_max|abs|ceil|floor|round|sqrt|ln|log2|log10)\s*(?=\()')
_RESULT_FIELDS = {
    "warning_count": ("backend_warnings", MAX_ANNOTATION_COUNT),
    "info_count": ("backend_infos", MAX_ANNOTATION_COUNT),
    "discarded_sample_count": ("discarded_samples", MAX_RESULT_POINTS),
    "discarded_series_count": ("discarded_series", MAX_RESULT_SERIES),
}


def _validate_result_quality(value: Any) -> None:
    if value is None:
        return
    if (not isinstance(value, Mapping) or set(value) != {"scope", *_RESULT_FIELDS}
            or value.get("scope") != "query"
            or any(type(value.get(key)) is not int or not 0 <= value[key] <= limit
                   for key, (_, limit) in _RESULT_FIELDS.items())):
        raise ValueError("invalid query result quality")


def result_quality_issues(value: Mapping[str, Any]) -> list[str]:
    """Safe count-bearing limitations also survive windows with no observations."""
    result = value.get("query_result")
    _validate_result_quality(result)
    return [f"{code}:{result[key]}" for key, (code, _) in _RESULT_FIELDS.items()
            if result and result[key]]


RESOLUTION_BLOCKERS = frozenset({'query_step_exceeds_interval','fewer_than_two_query_evaluations',
    'source_timestamp_in_future','source_sample_before_interval','fewer_than_two_observed_source_samples'})


def correlation_quality_issues(value: Mapping[str,Any]) -> list[str]:
    """Sampling limits cap strong hypotheses even without backend annotations."""
    return result_quality_issues(value)+[warning for warning in value.get('warnings',[])
        if warning in RESOLUTION_BLOCKERS | {'range_window_exceeds_interval','range_window_unknown','source_freshness_unknown'}]


def timestamp_query(query: str) -> str | None:
    """Only inspect a single explicit source; never timestamp a computed rate."""
    selectors = set(_SELECTOR.findall(query))
    if len(selectors) == 1:
        remainder = _SELECTOR.sub("", query)
        # Do not silently discard temporal modifiers or an additional bare
        # metric. This is a deliberately narrow extractor, not a PromQL parser.
        if re.search(r'\boffset\b|@|\[[^\]]*:', remainder):
            return None
        remainder = _DURATION.sub("", remainder)
        remainder = re.sub(r'\b(?:by|without)\s*\([A-Za-z0-9_,\s]*\)', "", remainder)
        remainder = _SOURCE_WRAPPERS.sub("", remainder)
        if re.search(r'[A-Za-z_:"\'`{}]', remainder):
            return None
        return f"timestamp({next(iter(selectors))})"
    if not selectors and re.fullmatch(r"[A-Za-z_:][A-Za-z0-9_:]*", query):
        return f"timestamp({query})"
    return None


def quality(query: str, start: float, end: float, query_step: float,
            stats: Mapping[str, Any] | None, *, source: Mapping[str, Any] | None = None,
            result: Mapping[str, Any] | None = None) -> dict:
    _validate_result_quality(result)
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
    future = last is not None and last > end
    age = end-last if last is not None and not future else None
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
    if future:
        warnings.append("source_timestamp_in_future")
    if last is not None and last < start:
        warnings.append("source_sample_before_interval")
    observed = finite_number(source.get('observed_source_samples'))
    if observed is not None and observed < 2:
        warnings.append('fewer_than_two_observed_source_samples')
    warnings.extend(code for key, (code, _) in _RESULT_FIELDS.items() if result and result[key])
    return {"interval_seconds": duration, "query_step_seconds": query_step,
            "range_window_seconds": lookback, "evaluation_count": evaluations,
            "evaluation_count_kind": "query_evaluations_not_scrapes",
            "observed_source_samples": source.get("observed_source_samples"),
            "source_sample_count_kind": "distinct_timestamps_seen_at_query_evaluations_not_complete_scrape_count",
            "last_source_timestamp": last, "source_age_seconds": age,
            "freshness": "observed" if last is not None and not future else "unknown",
            "source_coverage": "returned_series_only" if last is not None else "unknown",
            "query_result": dict(result) if result is not None else None,
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
        _validate_result_quality(item.get("query_result"))
        for key in ("interval_seconds", "query_step_seconds", "range_window_seconds", "evaluation_count",
                    "observed_source_samples", "last_source_timestamp", "source_age_seconds"):
            number = item.get(key)
            if number is not None and (finite_number(number) is None or number < 0):
                raise ValueError("sampling quality measurements must be finite nonnegative numbers")
        allowed_warnings = {"range_window_exceeds_interval", "query_step_exceeds_interval",
                            "fewer_than_two_query_evaluations", "source_freshness_unknown", "range_window_unknown",
                            "source_timestamp_in_future", "source_sample_before_interval",
                            "fewer_than_two_observed_source_samples"} | {code for code, _ in _RESULT_FIELDS.values()}
        warnings = item.get("warnings", [])
        if not isinstance(warnings, list) or any(not isinstance(warning, str) or warning not in allowed_warnings for warning in warnings):
            raise ValueError("invalid sampling quality warnings")
        if item.get("freshness", "unknown") not in {"unknown", "observed"}:
            raise ValueError("invalid sampling freshness")
