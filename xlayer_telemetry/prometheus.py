"""Shared Prometheus range-query client and evidence parsing.

This module preserves series labels and sampled points. It does not join entities
or attribute shared resource metrics to application runs.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import statistics
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_RESULT_SERIES = 1000
MAX_RESULT_POINTS = 200000


def escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def range_series(payload: Any) -> list[dict[str, Any]]:
    """Validate the response envelope and skip malformed/nonfinite sample pairs."""
    if not isinstance(payload, dict):
        raise RuntimeError("Prometheus response must be an object")
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload.get('error', 'unknown error')}")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("result"), list):
        raise RuntimeError("Prometheus response is missing a range-query matrix")
    if len(data["result"]) > MAX_RESULT_SERIES:
        raise RuntimeError("Prometheus result exceeds 1000 series; narrow source selectors")
    result = []
    point_count = 0
    for item in data['result']:
        if (not isinstance(item, dict) or not isinstance(item.get('metric', {}), dict)
                or not isinstance(item.get('values', []), list)):
            raise RuntimeError("Prometheus response contains an invalid series")
        point_count += len(item.get('values', []))
        if point_count > MAX_RESULT_POINTS:
            raise RuntimeError("Prometheus result exceeds 200000 points; narrow time window or increase query step")
        points = []
        for point in item.get('values', []):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            timestamp, value = map(_number, point)
            if timestamp is not None and value is not None:
                points.append([timestamp, value])
        if points:
            result.append({'labels': item.get('metric', {}), 'points': points})
    return result


def series_stats(series: Iterable[Mapping[str, Any]]) -> dict[str, float | None] | None:
    """Summarize parsed points; preserve reset-aware per-series counter increase."""
    values = []
    deltas = []
    for item in series:
        current = [value for _, value in item['points']]
        values.extend(current)
        if len(current) >= 2:
            deltas.append(sum(right - left if right >= left else right
                              for left, right in zip(current, current[1:])))
    if not values:
        return None
    return {'min': min(values), 'mean': statistics.fmean(values), 'max': max(values),
            'last': values[-1], 'max_series_delta': max(deltas) if deltas else None,
            'sample_count': float(len(values))}


def _read_json(request: Request, timeout: float) -> Any:
    with urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Prometheus response exceeds the 8 MiB limit; narrow query scope")
    return json.loads(body)


@dataclass
class PrometheusClient:
    url: str
    timeout: float = 5.0

    def query_range(self, query: str, start: float, end: float, step: float) -> dict[str, float | None] | None:
        return self.query_range_detail(query, start, end, step)['aggregate']

    def query_range_detail(self, query: str, start: float, end: float, step: float) -> dict[str, Any]:
        params = urlencode({'query': query, 'start': start, 'end': end, 'step': step})
        request = Request(self.url.rstrip('/') + '/api/v1/query_range?' + params)
        series = range_series(_read_json(request, self.timeout))
        return {'aggregate': series_stats(series), 'series': [
            {'labels': item['labels'], 'stats': series_stats([item]),
             **({'source_timestamps': [value for _, value in item['points']]}
                if query.startswith('timestamp(') else {})}
            for item in series
        ]}
