"""Shared Prometheus range-query client and evidence parsing.

This module preserves series labels and sampled points. It does not join entities
or attribute shared resource metrics to application runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPException
import json
import math
import statistics
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ERROR_RESPONSE_BYTES = 8 * 1024
MAX_RESULT_SERIES = 1000
MAX_RESULT_POINTS = 200000
MAX_ANNOTATION_COUNT = 1000
GPU_PRODUCER_TIMESTAMPS = {
    name: 'telemetry_gpu_sample_timestamp_seconds' for name in (
        'telemetry_gpu_utilization_percent', 'telemetry_gpu_memory_used_bytes',
        'telemetry_gpu_memory_total_bytes', 'telemetry_gpu_power_watts',
        'telemetry_gpu_temperature_celsius', 'telemetry_gpu_sm_clock_mhz',
    )
} | {'telemetry_gpu_process_memory_bytes': 'telemetry_gpu_process_sample_timestamp_seconds',
     'telemetry_gpu_field_status': 'telemetry_gpu_field_sample_timestamp_seconds'}
PRODUCER_TIMESTAMP_JOIN = ' + on(cluster, instance, job) group_right() (0 * '


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
    return _range_result(payload)[0]


def _range_result(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep bounded query-wide quality counts, never backend annotation text."""
    if not isinstance(payload, dict):
        raise RuntimeError("Prometheus response must be an object")
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload.get('error', 'unknown error')}")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("result"), list):
        raise RuntimeError("Prometheus response is missing a range-query matrix")
    if data.get("resultType", "matrix") != "matrix":
        raise RuntimeError("Prometheus range-query response must be a matrix")
    if len(data["result"]) > MAX_RESULT_SERIES:
        raise RuntimeError("Prometheus result exceeds 1000 series; narrow source selectors")
    result_quality = {"scope": "query", "discarded_sample_count": 0, "discarded_series_count": 0}
    for key, field in (("warnings", "warning_count"), ("infos", "info_count")):
        annotations = payload.get(key, [])
        if not isinstance(annotations, list) or any(not isinstance(item, str) for item in annotations):
            raise RuntimeError("Prometheus annotations must be lists of strings")
        result_quality[field] = min(len(annotations), MAX_ANNOTATION_COUNT)
    result = []
    point_count = 0
    identities = set()
    for item in data['result']:
        if (not isinstance(item, dict) or not isinstance(item.get('metric', {}), dict)
                or not isinstance(item.get('values', []), list)):
            raise RuntimeError("Prometheus response contains an invalid series")
        point_count += len(item.get('values', []))
        if point_count > MAX_RESULT_POINTS:
            raise RuntimeError("Prometheus result exceeds 200000 points; narrow time window or increase query step")
        labels = item.get('metric', {})
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in labels.items()):
            raise RuntimeError("Prometheus series labels must be strings")
        identity = tuple(sorted(labels.items()))
        if identity in identities:
            raise RuntimeError("Prometheus response contains duplicate series identities")
        identities.add(identity)
        points = []
        for point in item.get('values', []):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                result_quality["discarded_sample_count"] += 1
                continue
            timestamp, value = map(_number, point)
            if timestamp is not None and value is not None:
                # Counter reset detection assumes chronological samples. Never
                # invent growth from an unordered or duplicate evaluation.
                if points and timestamp <= points[-1][0]:
                    raise RuntimeError("Prometheus sample timestamps must be strictly increasing")
                points.append([timestamp, value])
            else:
                result_quality["discarded_sample_count"] += 1
        if points:
            result.append({'labels': labels, 'points': points})
        else:
            result_quality["discarded_series_count"] += 1
    return result, result_quality


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


def range_summary(detail: Mapping[str, Any]) -> dict[str, Any] | None:
    """Keep source multiplicity and safe response-quality counts with aggregates."""
    aggregate = detail.get('aggregate')
    if aggregate is None:
        return None
    series = detail.get('series', [])
    return {**aggregate, 'series_count': len(series),
            'min_series_sample_count': min((_number((item.get('stats') or {}).get('sample_count')) or 0 for item in series), default=0),
            'series_identities': [{key: value for key, value in item['labels'].items() if key != '__name__'}
                                  for item in series],
            'query_result': detail.get('result_quality', {})}


class PrometheusQueryTimeout(RuntimeError):
    """A server-side query timeout, not an unavailable backend transport."""


def _query_timeout_response(error: HTTPError) -> bool:
    # Only the structured Prometheus timeout envelope narrows a 503's scope.
    # Unreadable, oversized or proxy-generated responses retain outage behavior.
    if error.code != 503:
        return False
    try:
        with error:
            body = error.read(MAX_ERROR_RESPONSE_BYTES + 1)
        if len(body) > MAX_ERROR_RESPONSE_BYTES:
            return False
        payload = json.loads(body)
        return (isinstance(payload, dict) and payload.get('status') == 'error'
                and payload.get('errorType') == 'timeout')
    except (OSError, HTTPException, ValueError, RecursionError):
        return False


def _read_json(request: Request, timeout: float) -> Any:
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        if _query_timeout_response(error):
            raise PrometheusQueryTimeout("Prometheus query timed out") from None
        raise
    except HTTPException:
        # Bad framing is a source-local transport failure, not a failed entire
        # analysis. Keep partial bodies and backend connection details private.
        raise ConnectionError("Prometheus response transport failed") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Prometheus response exceeds the 8 MiB limit; narrow query scope")
    return json.loads(body)


@dataclass
class PrometheusClient:
    url: str
    timeout: float = 5.0

    def _load_payload(self, request: Request) -> Any:
        # Rule analysis already has an outer process/deadline boundary.
        return _read_json(request, self.timeout)

    def query_range(self, query: str, start: float, end: float, step: float) -> dict[str, Any] | None:
        return range_summary(self.query_range_detail(query, start, end, step))

    def query_range_detail(self, query: str, start: float, end: float, step: float) -> dict[str, Any]:
        params = urlencode({'query': query, 'start': start, 'end': end, 'step': step})
        request = Request(self.url.rstrip('/') + '/api/v1/query_range?' + params)
        series, result_quality = _range_result(self._load_payload(request))
        return {'aggregate': series_stats(series), 'result_quality': result_quality, 'series': [
            {'labels': item['labels'], 'stats': series_stats([item]),
             **({'source_timestamps': [value for _, value in item['points']]}
                if query.startswith('timestamp(') or any(
                    query.startswith(gauge+PRODUCER_TIMESTAMP_JOIN)
                    for gauge in set(GPU_PRODUCER_TIMESTAMPS.values())) else {})}
            for item in series
        ]}


class _DeadlinePrometheusClient(PrometheusClient):
    """Use the cancellable transport only for unisolated direct collection."""

    def _load_payload(self, request: Request) -> Any:
        from ._http_transport import _ResponseTooLarge, request_json

        try:
            return request_json(request.full_url, None, self.timeout)
        except _ResponseTooLarge:
            raise RuntimeError("Prometheus response exceeds the 8 MiB limit; narrow query scope") from None
