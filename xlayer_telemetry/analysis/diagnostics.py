"""Correlate VERL update observations with external telemetry sources."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.client import HTTPException
import heapq
import json
import math
import os
from pathlib import Path
import re
import statistics
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlencode
from urllib.request import Request

from .._http_proxy import urlopen

from .diagnosis_analysis import (
    VLLM_OBSERVATIONS, compare_signals, finite,
    recent_baseline_history, select_baseline, select_vllm_observations,
    validate_baseline_policy, validate_run_engine_instances, vllm_identity,
)
from .clock_quality import assess_interval, clock_inventory, producer_hosts_verified
from ..time_alignment import CalibrationCache, alignment_metadata, event_window, observation_time, reference_now
from . import scheduling
from .evidence_quality import quality, source_for_entity, validate_sampling, selected_quality, INVALID_SOURCE_TIME
from .rollout_replicas import validate_rollout_replicas, scope_queries, observations as replica_observations
from .rollout_state import validate_observations, load_serving_context, comparison_issues, apply_serving_limits
from ..sandbox import device_window
from ..fileio import atomic_write_text, json_objects
# Keep the established import path for SDK callers.
from ..prometheus import PrometheusClient, escape_label
from .diagnostic_signals import (_LATENCY_NAME, _DISTRIBUTION_LABELS, _distribution_identity,
                                 _distribution_index, signal_values, legacy_findings)
from .diagnostic_artifacts import (_existing_reports, _investigation_path, _write_investigation,
                                   write_report, _projection_recovery, _investigation_rows, repair_projections)
from .metric_observations import collect_metrics
from .query_budget import QueryBudget
from .observation_contracts import EvidenceBundle, EntityRef, usable_window
from .diagnosis_pipeline import evaluate_stage, describe_signals, assess_comparisons, assess_candidate_context, cap_candidate_quality
from .metric_queries import (DEFAULT_QUERIES, CONTEXT_SIGNALS, ENTITY_COMPARISON_SIGNALS,
                             profile_queries, validate_metric_profiles)
from .jsonl_cache import JSONLCache, from_config as cache_from_config
from .storage_series import apply_collection_limits, collect_storage_series, distribution_comparison_quality, validate_series_settings
from .storage_overview import storage_overview
from .robust_differential import duration_quality, assess as robust_assess, settings as robust_settings, apply_limits as apply_robust_limits

DEFAULT_THRESHOLDS = {
    "step_slowdown_ratio": 1.5,
    "stage_min_seconds": 1.0,
    "memory_available_ratio": 0.1,
    "disk_busy_ratio": 0.9,
    "vllm_waiting": 1.0,
    "vllm_kv_usage": 0.9,
    "ray_pending_tasks": 1.0,
    "policy_version_lag": 2.0,
    "threefs_latency_slowdown_ratio": 2.0,
    "gpu_utilization_drop_points": 20.0,
    "network_utilization_ratio": 0.8,
    "gpu_memory_ratio": 0.9,
    "rdma_growth_ratio": 1.5,
    "straggler_ratio": 1.5,
    "peer_spread_ratio": 0.2,
    "small_io_bytes": 4096,
    "tool_slowdown_ratio": 1.5,
    "sandbox_io_pressure_ratio": 0.2,
    "sandbox_device_busy_ratio": 0.9,
}
_ALLOWED_3FS_FILTERS = {"host", "mount_name", "instance", "io", "uid", "pod", "method"}
_DATABASE = re.compile(r"^[A-Za-z0-9_]+$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
@dataclass
class ThreeFSClient:
    url: str
    database: str = "3fs"
    filters: Mapping[str, str] | None = None
    timeout: float = 5.0
    user_env: str = "THREEFS_CLICKHOUSE_USER"
    password_env: str = "THREEFS_CLICKHOUSE_PASSWORD"
    # Standalone CLI inspection uses resolved config exports without changing
    # process-global state. Existing callers still read the current environment.
    _environment: Mapping[str, str] | None = field(default=None, repr=False, compare=False, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError("3FS ClickHouse url must be a nonempty string")
        if not isinstance(self.database, str) or not _DATABASE.fullmatch(self.database):
            raise ValueError("3FS ClickHouse database must be an identifier")
        if self.filters is not None and not isinstance(self.filters, Mapping):
            raise ValueError("3FS filters must be an object")
        if any(not isinstance(name, str) for name in self.filters or {}):
            raise ValueError("3FS filter names must be strings")
        invalid = set(self.filters or {}) - _ALLOWED_3FS_FILTERS
        if invalid:
            raise ValueError(f"unsupported 3FS filters: {sorted(invalid)}")
        if any(not isinstance(value, str) for value in (self.filters or {}).values()):
            raise ValueError("3FS filter values must be strings")
        if finite(self.timeout) is None or self.timeout <= 0:
            raise ValueError("3FS timeout must be finite and positive")
        for name in (self.user_env, self.password_env):
            if not isinstance(name, str) or not _ENVIRONMENT_NAME.fullmatch(name):
                raise ValueError("3FS user_env and password_env must be environment variable names")

    @staticmethod
    def _literal(value: str) -> str:
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

    def _where(self, start: float, end: float) -> str:
        if finite(start) is None or finite(end) is None or start >= end:
            raise ValueError("3FS query window must be finite and increasing")
        # This source stores integer-second DateTime values. Preserve [start,
        # end) membership instead of shifting fractional bounds one second back.
        clauses = [f"TIMESTAMP >= toDateTime({math.ceil(start)})", f"TIMESTAMP < toDateTime({math.ceil(end)})"]
        clauses.extend(
            f"{name} = {self._literal(value)}"
            for name, value in sorted((self.filters or {}).items())
        )
        return " AND ".join(clauses)

    def _query_rows(self, query: str) -> list[dict[str, Any]]:
        params = urlencode({"database": self.database})
        request = Request(self.url.rstrip("/") + "/?" + params, data=query.encode(), method="POST")
        environment = os.environ if self._environment is None else self._environment
        user = environment.get(self.user_env)
        password = environment.get(self.password_env, "")
        if user:
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            request.add_header("Authorization", "Basic " + token)
        body = self._read_body(request)
        rows = [json.loads(line) for line in body.splitlines() if line.strip()]
        if any(not isinstance(row, dict) or not isinstance(row.get("metricName"), str) for row in rows):
            raise ValueError("invalid ClickHouse metric row")
        return rows

    def _read_body(self, request: Request) -> bytes:
        # Rule analysis already has an outer process/deadline boundary.
        response_limit = 8 * 1024 * 1024
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read(response_limit + 1)
                remaining = getattr(response, 'length', None)
        except HTTPException:
            raise ConnectionError("ClickHouse response transport failed") from None
        if len(body) > response_limit:
            raise ValueError("ClickHouse response exceeds 8 MiB limit; narrow source filters")
        if isinstance(remaining, int) and remaining > 0:
            raise ConnectionError("ClickHouse response incomplete")
        return body

    @staticmethod
    def _number(value, key, *, integer=False, nonnegative=False):
        if isinstance(value, str):
            try:
                value = int(value) if integer else float(value)
            except ValueError as error:
                raise ValueError(f"invalid ClickHouse {key}") from error
        if (finite(value) is None or (nonnegative and value < 0)
                or (integer and value != int(value))):
            raise ValueError(f"invalid ClickHouse {key}")
        return value

    def query_window(self, start: float, end: float) -> list[dict[str, Any]]:
        identity = ", ".join(("metricName", *_DISTRIBUTION_LABELS))
        query = (
            f"SELECT {identity}, sum(`count`) AS sample_count, "
            "if(sum(`count`)=0,0,sum(mean*`count`)/sum(`count`)) AS weighted_mean, "
            "max(`max`) AS max_value, max(p99) AS max_observed_p99, "
            "count() AS report_count, uniqExact(TIMESTAMP) AS observed_second_count, "
            "toUnixTimestamp(min(TIMESTAMP)) AS first_observed_at, "
            "toUnixTimestamp(max(TIMESTAMP)) AS last_observed_at "
            f"FROM {self.database}.distributions WHERE {self._where(start, end)} AND `count` > 0 "
            f"GROUP BY {identity} ORDER BY {identity} LIMIT 1001 "
            "FORMAT JSONEachRow"
        )
        rows = self._query_rows(query)
        if len(rows) > 1000:
            raise ValueError("3FS distributions exceed 1000 entity limit; narrow source filters")
        for row in rows:
            # Flat upstream identity columns become additive evidence labels.
            # Entirely unlabeled historical/test rows remain legacy; a partial
            # explicit identity must not silently fall back to metricName.
            if any(key in row for key in _DISTRIBUTION_LABELS):
                if any(not isinstance(row.get(key), str) for key in _DISTRIBUTION_LABELS):
                    raise ValueError("invalid ClickHouse distribution identity")
                row["labels"] = {key: row.pop(key) for key in _DISTRIBUTION_LABELS}
            if "sample_count" in row:
                row["count"] = row.pop("sample_count")
            if "max_value" in row:
                row["max"] = row.pop("max_value")
            # JSONEachRow may quote 64-bit integers. Unavailable cells remain
            # unavailable; the established distribution field names stay intact.
            for key in ("count", "weighted_mean", "max", "max_observed_p99",
                        "first_observed_at", "last_observed_at", "report_count", "observed_second_count"):
                if row.get(key) is not None:
                    integer = key in {"count", "first_observed_at", "last_observed_at", "report_count", "observed_second_count"}
                    row[key] = self._number(row[key], key, integer=integer, nonnegative=integer)
        return rows

    def query_counters(self, start: float, end: float) -> list[dict[str, Any]]:
        """Inspect raw producer values; the counters table also contains gauges.

        3FS recorders may reset values after collection. A table name alone is
        insufficient to derive rates, monotonic deltas or operation totals.
        """
        if "method" in (self.filters or {}):
            raise ValueError("3FS counters have no method filter; distribution scope cannot be reused")
        labels = ("host", "tag", "mount_name", "instance", "io", "uid", "pod", "thread", "statusCode")
        identity = ", ".join(("metricName", *labels))
        query = (
            f"SELECT {identity}, count() AS sample_count, "
            "min(val) AS min, max(val) AS max, argMax(val, TIMESTAMP) AS last, "
            "toUnixTimestamp(min(TIMESTAMP)) AS first_observed_at, "
            "toUnixTimestamp(max(TIMESTAMP)) AS last_observed_at "
            f"FROM {self.database}.counters WHERE {self._where(start, end)} "
            f"GROUP BY {identity} ORDER BY {identity} LIMIT 1001 FORMAT JSONEachRow"
        )
        rows = self._query_rows(query)
        if len(rows) > 1000:
            raise ValueError("3FS counters exceed 1000 entity limit; narrow source filters")
        result = []
        for row in rows:
            if any(not isinstance(row.get(key), str) for key in labels):
                raise ValueError("invalid ClickHouse counter identity")
            result.append({
                "metricName": row["metricName"], "labels": {key: row[key] for key in labels},
                **{key: self._number(row.get(key), key, integer=True,
                                     nonnegative=key not in {"min", "max", "last"})
                   for key in ("sample_count", "min", "max", "last", "first_observed_at", "last_observed_at")},
            })
        return result

    def query_distribution_series(self, start: float, end: float, *, metric_names: Sequence[str] = (), max_points: int = 2000) -> list[dict[str, Any]]:
        """Read second-resolution collection reports with bounded full identity."""
        from .storage_queries import distribution_series
        return distribution_series(self, start, end, metric_names=metric_names, max_points=max_points)

    def query_counter_series(self, start: float, end: float, *, metric_names: Sequence[str] = (), max_points: int = 2000) -> list[dict[str, Any]]:
        """Keep raw report values; sum only verified reset-on-collect producers."""
        from .storage_queries import counter_series
        return counter_series(self, start, end, metric_names=metric_names, max_points=max_points)


class _DeadlineThreeFSClient(ThreeFSClient):
    """Bound standalone inspection without nesting workers in rule analysis."""

    def _read_body(self, request: Request) -> bytes:
        from .._http_transport import _ResponseTooLarge, request_bytes

        try:
            return request_bytes(request.full_url, None, self.timeout,
                                 data=request.data, headers=dict(request.header_items()))
        except _ResponseTooLarge:
            raise ValueError("ClickHouse response exceeds 8 MiB limit; narrow source filters") from None


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("diagnostics config must be an object")
    if config.get("schema_version") != 1:
        raise ValueError("diagnostics config schema_version must be 1")
    if not isinstance(config.get("prometheus"), dict):
        raise ValueError("diagnostics config requires a prometheus object")
    if not isinstance(config["prometheus"].get("url"), str):
        raise ValueError("diagnostics config requires prometheus.url")
    validate_metric_profiles(config["prometheus"])
    QueryBudget(config.get("query_budget_seconds", 30))
    deadline = config.get("analysis_deadline_seconds", 60)
    if finite(deadline) is None or deadline < 0:
        raise ValueError("analysis_deadline_seconds must be finite and nonnegative")
    cache_from_config(config)
    for backend in ("prometheus", "threefs"):
        settings = config.get(backend)
        if isinstance(settings, dict):
            value = settings.get("timeout_seconds", 5)
            if finite(value) is None or value <= 0:
                raise ValueError(f"{backend}.timeout_seconds must be finite and positive")
    validate_sampling(config.get("sampling", {}))
    validate_baseline_policy(config.get("baseline", {}))
    validate_run_engine_instances(config)
    validate_rollout_replicas(config)
    validate_observations(config)
    thresholds = config.get("thresholds", {})
    if not isinstance(thresholds, dict) or any(
        type(value) not in (int, float) or not math.isfinite(value) or value < 0
        for value in thresholds.values()
    ):
        raise ValueError("diagnostic thresholds must be finite nonnegative numbers")
    for key, default in (("retry_seconds", 60), ("retry_interval_seconds", 10)):
        value = config.get(key, default)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be a finite positive number")
    threefs = config.get("threefs")
    if threefs is not None:
        if not isinstance(threefs, dict):
            raise ValueError("threefs must be an object")
        validate_series_settings(threefs.get("time_series", {}))
        settle = threefs.get("settle_seconds", 30)
        if type(settle) not in (int, float) or not math.isfinite(settle) or settle < 0:
            raise ValueError("threefs.settle_seconds must be finite and nonnegative")
        if threefs:
            # Validate the same fields used by inspection/analysis before any
            # network request, while preserving an empty optional source.
            ThreeFSClient(threefs.get("url"), database=threefs.get("database", "3fs"),
                          filters=threefs.get("filters"), timeout=threefs.get("timeout_seconds", 5),
                          user_env=threefs.get("user_env", "THREEFS_CLICKHOUSE_USER"),
                          password_env=threefs.get("password_env", "THREEFS_CLICKHOUSE_PASSWORD"))
    sandbox = config.get("sandbox")
    if sandbox is not None:
        if not isinstance(sandbox, dict) or type(sandbox.get("enabled")) is not bool:
            raise ValueError("sandbox must be an object with boolean enabled")
        if any(not isinstance(sandbox.get(key), str) or not sandbox[key]
               for key in ("node", "device", "events_dir") if key in sandbox):
            raise ValueError("sandbox.node, sandbox.device and sandbox.events_dir must be nonempty strings")
    for key in ("cluster", "compute_node", "rollout_node", "storage_node", "storage_device"):
        if key in config and (not isinstance(config[key], str) or not config[key]):
            raise ValueError(f"{key} must be a nonempty string")
    if sandbox and sandbox.get("device_major_minor") is not None and (not isinstance(sandbox["device_major_minor"], str) or not re.fullmatch(r"[0-9]+:[0-9]+", sandbox["device_major_minor"])):
        raise ValueError("sandbox.device_major_minor must be major:minor")
    clocks = config.get("clock", {})
    if not isinstance(clocks, dict):
        raise ValueError("clock must be an object")
    for key in ("enabled", "require_sync"):
        if key in clocks and type(clocks[key]) is not bool:
            raise ValueError(f"clock.{key} must be boolean")
    for key in ("max_skew_seconds", "max_sample_age_seconds", "max_uncertainty_seconds"):
        if key in clocks and (finite(clocks[key]) is None or clocks[key] <= 0):
            raise ValueError(f"clock.{key} must be finite and positive")
    if "monitoring_node" in clocks and (not isinstance(clocks["monitoring_node"], str) or not clocks["monitoring_node"]):
        raise ValueError("clock.monitoring_node must be a registered node identity")
    if "nodes" in clocks and (not isinstance(clocks["nodes"], list) or len(clocks["nodes"]) > 32
            or any(not isinstance(node,str) or not node for node in clocks["nodes"])):
        raise ValueError("clock.nodes needs at most 32 explicit node identities")
    if clocks.get("enabled", False) and not config.get("cluster"):
        raise ValueError("clock checks require cluster")
    if "calibration_reference" in clocks and (not isinstance(clocks["calibration_reference"], str) or not clocks["calibration_reference"].strip()):
        raise ValueError("clock.calibration_reference must be a nonempty reference ID")
    if threefs and "clock_nodes" in threefs and (
        not isinstance(threefs["clock_nodes"], list) or not threefs["clock_nodes"]
        or any(not isinstance(node, str) or not node for node in threefs["clock_nodes"])
    ):
        raise ValueError("threefs.clock_nodes must be a nonempty list of node names")
    return {**config,**{key:({'enabled':False} if key=='sandbox' else {}) for key in ('threefs','sandbox') if config.get(key) is None}}


def load_history(path: Path, *, cache: JSONLCache | None = None) -> list[dict[str, Any]]:
    records = []
    try:
        for record in (cache.read(path) if cache is not None else json_objects(path)):
            if (record.get("schema_version") == 1 and record.get("record_type") == "verl_step_observation"
                    and isinstance(record.get("record_id"), str)):
                records.append(record)
    except FileNotFoundError:
        pass
    return records


def tool_span_window(directory: Path, run_id: str, start: float, end: float,
                     *, tool_name: str | None = None, cache: JSONLCache | None = None,
                     reference_id: str | None = None,
                     reference_session: str | None = None,
                     entity_labels: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Use completed tool spans fully inside an analysis interval.

    A VERL step interval can be approximate. Time overlap supplies correlation,
    not proof that the trainer step owns the tool or its sandbox I/O.
    """
    longest: dict[str, Any] | None = None
    max_duration: float | None = None
    count = 0
    identity_fields = ("run_id", "node", "worker_id", "role", "producer", "rank", "local_rank", "gpu")

    def labels(item):
        return {**{key: str(item[key]) for key in identity_fields if item.get(key) is not None},
                "tool": item["attributes"]["tool"]}

    try:
        # Producer names identify ownership, not event semantics. External
        # adapters may use any SDK producer name for the same tool.call span.
        paths = list(directory.glob("*.jsonl"))
    except OSError:
        return None
    for path in paths:
        try:
            for item in (cache.read(path) if cache is not None else json_objects(path)):
                if (item.get("schema_version") != 1
                        or item.get("record_type") != "span"
                        or item.get("name") != "tool.call" or item.get("run_id") != run_id
                        or item.get("status") != "ok"
                        or item.get("boundary_accuracy") == "clock_discontinuity"
                        or not isinstance(item.get("trace_id"), str)
                        or not isinstance(item.get("span_id"), str)):
                    continue
                attributes = item.get("attributes")
                if not isinstance(attributes, dict) or not isinstance(attributes.get("tool"), str):
                    continue
                if tool_name is not None and attributes["tool"] != tool_name:
                    continue
                if entity_labels is not None and labels(item) != entity_labels:
                    continue
                began, finished = event_window(item, reference_id=reference_id, reference_session=reference_session)
                duration = finite(item.get("duration_seconds"))
                if (began is not None and finished is not None and duration is not None
                        and duration >= 0 and start <= began
                        and finished <= end):
                    count += 1
                    if max_duration is None or duration > max_duration:
                        longest = item
                        max_duration = duration
        except OSError:
            continue
    if longest is None:
        return None
    began, finished = event_window(longest, reference_id=reference_id, reference_session=reference_session)
    return {"max": max_duration,
            "sample_count": count,
            "tool": longest["attributes"]["tool"],
            "labels": labels(longest),
            "window": {"start": began, "end": finished,
                       "accuracy": longest.get("boundary_accuracy", "unknown"), "source": "event_span",
                       **({"time_alignment": longest["time_alignment"]} if "time_alignment" in longest else {})},
            "boundary_accuracy": longest.get("boundary_accuracy", "unknown"),
            "related_span": f"{longest['trace_id']}:{longest['span_id']}"}


def _slow_stages(current: Mapping[str, Any], history: list[dict[str, Any]], thresholds: Mapping[str, float]) -> list[dict[str, float]]:
    previous: dict[str, list[float]] = {}
    recent = heapq.nlargest(5, history, key=lambda record: observation_time(record) or 0)
    for record in recent:
        stages = record.get("stage_durations_seconds", {})
        if not isinstance(stages, Mapping):
            continue
        for name, value in stages.items():
            if finite(value) is not None and value >= 0:
                previous.setdefault(name, []).append(float(value))
    slow = []
    stages = current.get("stage_durations_seconds", {})
    for name, value in (stages.items() if isinstance(stages, Mapping) else []):
        baseline = previous.get(name, [])
        if finite(value) is None or value < 0 or not baseline:
            continue
        median = statistics.median(baseline)
        ratio = float(value) / median if median > 0 else 0.0
        if float(value) >= thresholds["stage_min_seconds"] and ratio >= thresholds["step_slowdown_ratio"]:
            slow.append({"stage": name, "seconds": float(value), "baseline_median": median, "ratio": ratio})
    return slow


def _gpu_memory_observations(items: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    """Keep unambiguous device observations without guessing UUID/index maps.

    Multiple process/worker series for one device need an explicit producer
    aggregation contract; a maximum across them is not device attribution.
    """
    selected, ambiguous = {}, set()
    for item in items:
        labels = item.get("labels", {})
        node = labels.get("nodename") or labels.get("node")
        device = tuple((key, str(labels[key])) for key in ("gpu", "gpu_uuid")
                       if labels.get(key) is not None and str(labels[key]))
        if (not node or not device or finite(item.get("stats", {}).get("max")) is None
                or (labels.get("node") and labels.get("nodename") and labels["node"] != labels["nodename"])):
            continue
        identity = (str(labels.get("cluster", "")), str(node), device)
        if identity in selected:
            ambiguous.add(identity)
        selected[identity] = item
    return {identity: item for identity, item in selected.items() if identity not in ambiguous}


class DiagnosticEngine:
    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        prometheus: PrometheusClient | None = None,
        threefs: ThreeFSClient | None = None,
        clock: Callable[[], float] = time.time,
        time_calibration: CalibrationCache | None = None,
        elapsed_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        config = {**config, **{key: ({'enabled':False} if key=='sandbox' else {}) for key in ('threefs','sandbox') if config.get(key) is None}}
        validate_run_engine_instances(config)
        validate_rollout_replicas(config)
        validate_observations(config)
        self.config = config
        prom = config["prometheus"]
        self.prometheus = prometheus or PrometheusClient(prom["url"], float(prom.get("timeout_seconds", 5)))
        threefs_config = config.get("threefs")
        self.threefs = threefs
        if threefs_config and threefs is None:
            self.threefs = ThreeFSClient(
                threefs_config["url"],
                database=threefs_config.get("database", "3fs"),
                filters=threefs_config.get("filters"),
                timeout=float(threefs_config.get("timeout_seconds", 5)),
                user_env=threefs_config.get("user_env", "THREEFS_CLICKHOUSE_USER"),
                password_env=threefs_config.get("password_env", "THREEFS_CLICKHOUSE_PASSWORD"),
            )
        calibration = time_calibration or (CalibrationCache.from_env(str(config.get("node") or os.environ.get("TELEMETRY_NODE", ""))) if clock is time.time else None)
        self.time_calibration, self.raw_clock = calibration, clock
        self.elapsed_clock, self.elapsed_clock_id = elapsed_clock, scheduling.clock_id(injected=elapsed_clock is not time.monotonic)
        self._settle_deadlines = {}
        self.clock = (lambda: reference_now(calibration, clock())) if calibration is not None else clock
        self.thresholds = {**DEFAULT_THRESHOLDS, **config.get("thresholds", {})}
        self.jsonl_cache = cache_from_config(config)
        self._verified_projections: set[tuple[str, str, str]] = set()

    def _queries(self, cluster: str, *, with_sandbox: bool = False) -> dict[str, str]:
        """Build scoped defaults while preserving explicit user query overrides."""
        queries = dict(DEFAULT_QUERIES)
        custom = self.config["prometheus"].get("queries", {})
        if cluster:
            for name, template in queries.items():
                job = "native" if name.startswith(("vllm_", "ray_")) else "telemetry"
                source = ',telemetry_source="vllm"' if name.startswith("vllm_") else ''
                queries[name] = re.sub(
                    r'\{(?=[A-Za-z_]+=)',
                    lambda match: '{cluster="{cluster}",job="' + job + '"' + source + ',', template,
                )
        queries.update(profile_queries(self.config["prometheus"], cluster))
        queries = scope_queries(queries, self.config)
        queries.update(custom)
        if self.config.get("storage_node") and self.config.get("storage_device"):
            queries.setdefault("storage_device_busy_ratio", CONTEXT_SIGNALS["storage_device_busy_ratio"].expression)
        if with_sandbox:
            sandbox = self.config.get("sandbox", {})
            names = ["tool_duration_seconds", "sandbox_io_pressure_ratio"]
            if sandbox.get("device"):
                names.append("sandbox_device_busy_ratio")
            defaults = {name: CONTEXT_SIGNALS[name].expression for name in names}
            for name, template in defaults.items():
                if cluster:
                    template = template.replace('{', '{cluster="{cluster}",job="telemetry",', 1)
                queries.setdefault(name, template)
        return queries

    def analyze(self, current: Mapping[str, Any] | None, history: list[dict[str, Any]]) -> dict[str, Any]:
        if self.config.get('rollout_replicas') and current and current.get('run_id') != self.config['run_id']:
            raise ValueError('rollout_replicas inventory belongs to a different Run')
        budget = QueryBudget(self.config.get("query_budget_seconds", 30))
        prometheus = budget.wrap(self.prometheus, "prometheus", configurable_timeout=isinstance(self.prometheus, PrometheusClient))
        threefs = budget.wrap(self.threefs, "threefs", configurable_timeout=isinstance(self.threefs, ThreeFSClient)) if self.threefs is not None else None
        now = self.clock()
        lookback = float(self.config.get("lookback_seconds", 60))
        raw_window = (current or {}).get("analysis_window", {})
        window = dict(raw_window) if isinstance(raw_window, Mapping) else {}
        if current and not usable_window(window):
            return {
                "schema_version": 1, "record_type": "bottleneck_diagnosis",
                "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                "run_id": current.get("run_id"), "node": current.get("node"),
                "execution_mode": current.get("execution_mode", "sync"),
                "data_origin": "observed", "trigger": "step_observed",
                "trigger_record_id": current.get("record_id"), "step": current.get("step"),
                "worker_id": current.get("worker_id"),
                "boundary_scope": current.get("boundary_scope"), "analysis_window": window,
                "verdict": "insufficient_data", "findings": [], "evidence": {},
                "missing_sources": ["step_event_time"],
                "limitations": ["No trustworthy step event window is available; external telemetry cannot be correlated to this step."],
                "diagnosis_schema_version": 1,
                "symptom": {"step": current.get("step"),
                            "step_duration_seconds": current.get("step_duration_seconds"),
                            "slow_stages": [], "boundary_scope": current.get("boundary_scope")},
                "comparison": {"current_interval": window, "baseline_interval": None,
                               "baseline_record_id": None, "selection": "unavailable", "signals": []},
                "candidates": [],
            }
        end = float(window["end"]) if window.get("end") is not None else now
        start = window.get("start")
        if type(start) not in (int, float) or start >= end:
            start = max(0.0, end - lookback)
            window = {"start": start, "end": end, "accuracy": "periodic", "source": "diagnostic_lookback"}
            if self.time_calibration is not None and current is None:
                raw_now = self.raw_clock()
                window.update(self.time_calibration.project(max(0, raw_now-lookback), raw_now))
                start, end = window["start"], window["end"]
        node = str((current or {}).get("node") or self.config.get("node", ""))
        compute_node = str(self.config.get("compute_node") or node)
        rollout_node = str(self.config.get("rollout_node") or node)
        storage_node = str(self.config.get("storage_node") or node)
        cluster = str(self.config.get("cluster", ""))
        run_id = str((current or {}).get("run_id") or self.config.get("run_id", ""))
        execution_mode = str((current or {}).get("execution_mode") or self.config.get("execution_mode", "sync"))
        baseline_policy = self.config.get("baseline", {})
        validate_baseline_policy(baseline_policy)
        comparable_history = recent_baseline_history(current, history, policy=baseline_policy) if current else []
        robust_quality = duration_quality(current, comparable_history, baseline_policy, self._signals) if current else None
        slow = _slow_stages(current or {}, comparable_history, self.thresholds)
        if robust_quality is not None:
            config = robust_settings(baseline_policy)
            slow = [row for row in slow if baseline_policy.get('match_fields') and robust_assess(row['seconds'],
                [item['stage_durations_seconds'].get(row['stage']) for item in comparable_history
                 if isinstance(item.get('stage_durations_seconds'), Mapping)],
                minimum_cohort=config['minimum_cohort'], z_threshold=config['z_threshold'],
                relative_floor=config['relative_floor'])['status'] == 'shift_observed']
        evidence: dict[str, Any] = {"slow_stages": slow}
        missing: list[str] = []
        if ("mooncake_storage" in self.config["prometheus"].get("metric_profiles", [])
                and not self.config["prometheus"].get("mooncake_master_node")):
            missing.append("mooncake:master:not_configured")
        sandbox_config = self.config.get("sandbox", {})
        sandbox_node = str((current or {}).get("sandbox_node") or sandbox_config.get("node") or node)
        sandbox_device = str(sandbox_config.get("device", ""))
        queries = self._queries(cluster, with_sandbox=bool(sandbox_config.get("enabled") and current))
        query_context = {
            "node": node, "run_id": run_id, "cluster": cluster,
            "compute_node": compute_node, "rollout_node": rollout_node,
            "storage_node": storage_node, "storage_device": str(self.config.get("storage_device", "")),
            "sandbox_node": sandbox_node, "sandbox_device": sandbox_device,
        }
        step = max(1.0, float(self.config["prometheus"].get("query_step_seconds", 2)))
        baseline_record = select_baseline(current, comparable_history, policy=baseline_policy) if current else None
        baseline_window = (baseline_record or {}).get("analysis_window", {})
        baseline_window_valid = (finite(baseline_window.get("start")) is not None
                                 and finite(baseline_window.get("end")) is not None
                                 and baseline_window["start"] < baseline_window["end"])
        clock_config = self.config.get("clock", {})
        def clock_query(expression, start, end, query_step):
            if hasattr(prometheus,'query_range'):
                return prometheus.query_range(expression,start,end,query_step)
            from ..prometheus import range_summary
            return range_summary(prometheus.query_range_detail(expression,start,end,query_step))
        clock_quality = {"status": "unchecked", "nodes": {}}
        inventory = clock_inventory(self.config, node)
        clock_nodes = set(inventory["nodes"])
        cross_node = inventory["operating_scope"] == "cross-node"
        if cross_node or clock_config.get("enabled", bool(cluster)) or clock_config.get("calibration_reference") or "time_alignment" in window:
            clock_quality = assess_interval(
                clock_query, cluster=cluster, nodes=clock_nodes,
                window=window, producer_node=node, config=clock_config,
                producer_clock_nodes=self.config.get("threefs", {}).get("clock_nodes", []),
            )
            if clock_quality["status"] != "aligned":
                missing.extend(f"clock:{name}:{item['status']}" for name, item in clock_quality["nodes"].items() if item["status"] != "aligned")
            if baseline_window_valid:
                clock_quality["baseline"] = assess_interval(
                    clock_query, cluster=cluster, nodes=clock_nodes,
                    window=baseline_window, producer_node=str(baseline_record.get("node", node)), config=clock_config,
                    producer_clock_nodes=self.config.get("threefs", {}).get("clock_nodes", []),
                )
                if clock_quality["baseline"]["status"] != "aligned":
                    missing.append("clock:baseline:unaligned")
                if alignment_metadata(window).get("reference_session") != alignment_metadata(baseline_window).get("reference_session"):
                    clock_quality["baseline"]["status"] = "unknown"
                    missing.append("clock:baseline:reference_session_changed")
            if inventory["issues"]:
                missing.extend(inventory["issues"])
                if clock_quality["status"] != "unsafe":
                    clock_quality["status"] = "unknown"
                if "baseline" in clock_quality and clock_quality["baseline"]["status"] != "unsafe":
                    clock_quality["baseline"]["status"] = "unknown"
        clock_quality["operating_scope"] = "cross-node" if cross_node else "single-resource-node"
        clock_quality["required_nodes"] = sorted(clock_nodes)
        serving = None
        if self.config.get('rollout_observations'):
            reader = self.jsonl_cache.read if self.jsonl_cache is not None else None
            serving = {'current': load_serving_context(self.config, window, clock_quality, reader=reader),
                       'baseline': load_serving_context(self.config, baseline_window,
                           clock_quality.get('baseline', {}), reader=reader) if baseline_record else {}}

        observations = collect_metrics(prometheus, queries, query_context, float(start), end, step,
            baseline_window=baseline_window if baseline_window_valid else None,
            check_freshness=self.config.get("sampling", {}).get("check_source_freshness", False))
        evidence.update(observations.evidence)
        missing.extend(observations.missing)
        baseline_metrics, executed_queries = observations.baseline, observations.executed_queries
        sampling_quality, source_samples = observations.sampling_quality, observations.source_samples
        current_series, baseline_series = observations.current_series, observations.baseline_series

        sandbox_device_mapping = None
        if sandbox_config.get("enabled") and sandbox_config.get("events_dir"):
            sandbox_device_mapping = device_window(Path(sandbox_config["events_dir"]), run_id, sandbox_node,
                                                  float(start), end, sandbox_config.get("device_major_minor"),
                                                  reference_id=clock_config.get("calibration_reference"),
                                                  reference_session=alignment_metadata(window).get("reference_session"),
                                                  reader=self.jsonl_cache.read if self.jsonl_cache is not None else None)
        tool_event_span = None
        tool_event = None
        if sandbox_config.get("enabled") and sandbox_config.get("events_dir") and current:
            directory = Path(sandbox_config["events_dir"])
            tool_event = tool_span_window(directory, run_id, float(start), end, cache=self.jsonl_cache,
                                         reference_id=clock_config.get("calibration_reference"),
                                         reference_session=alignment_metadata(window).get("reference_session"))
            if tool_event is not None:
                evidence["tool_duration_seconds"] = tool_event
                tool_node=tool_event.get('labels',{}).get('node')
                if tool_node and tool_node != node and tool_node not in clock_nodes:
                    missing.append(f'clock:{tool_node}:unregistered_observation_node')
                    clock_quality['nodes'][tool_node]={'status':'unknown','issues':['unregistered_observation_node']}
                    clock_quality['required_nodes']=sorted(set(clock_quality['required_nodes'])|{tool_node})
                    clock_quality['operating_scope']='cross-node'
                    if clock_quality['status']!='unsafe':
                        clock_quality['status']='unknown'
                tool_event_span = tool_event["related_span"]
                executed_queries.pop("tool_duration_seconds", None)
                # A completed event span is not a Prometheus query evaluation.
                sampling_quality.pop("tool_duration_seconds", None)
                current_series.pop("tool_duration_seconds", None)
                baseline_series.pop("tool_duration_seconds", None)
                missing = [item for item in missing
                           if not item.startswith("prometheus:tool_duration_seconds")]
                baseline_metrics.pop("tool_duration_seconds", None)
            if baseline_window_valid:
                if tool_event is not None:
                    previous_tool = tool_span_window(
                        directory, run_id, float(baseline_window["start"]),
                        float(baseline_window["end"]), tool_name=tool_event["tool"], cache=self.jsonl_cache,
                        reference_id=clock_config.get("calibration_reference"),
                        reference_session=alignment_metadata(baseline_window).get("reference_session"),
                        entity_labels=tool_event["labels"],
                    )
                    if previous_tool is not None:
                        baseline_metrics["tool_duration_seconds"] = previous_tool

        threefs_rows: list[dict[str, Any]] = []
        threefs_baseline: list[dict[str, Any]] = []
        current_3fs_samples = False
        if threefs is not None:
            try:
                duration = end - float(start)
                threefs_rows = threefs.query_window(float(start), end)
                if threefs_rows:
                    evidence["threefs_distributions"] = threefs_rows
                current_3fs_samples = any((finite(row.get("count")) or 0) > 0 for row in threefs_rows)
                if not current_3fs_samples:
                    missing.append("threefs:no_data")
                baseline_start = float(baseline_window["start"]) if baseline_window_valid else float(start) - duration
                baseline_end = float(baseline_window["end"]) if baseline_window_valid else float(start)
                threefs_baseline = threefs.query_window(baseline_start, baseline_end)
                if threefs_baseline:
                    evidence["threefs_baseline_distributions"] = threefs_baseline
                if baseline_window_valid and not any((finite(row.get("count")) or 0) > 0 for row in threefs_baseline):
                    missing.append("threefs:baseline_no_data")
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                missing.append(f"threefs:{type(exc).__name__}")

        current_signals = self._signals(current, evidence, threefs_rows)
        baseline_signals = self._signals(baseline_record, baseline_metrics, threefs_baseline) if baseline_record else {}
        signal_labels: dict[str, dict[str, str]] = {
            ("vllm_preemptions_delta" if name == "vllm_preemptions_total" else name): dict(items[0].get("labels", {}))
            for name, items in current_series.items() if len(items) == 1
        }
        if tool_event is not None:
            signal_labels["tool_duration_seconds"] = tool_event["labels"]
        signal_scopes: dict[str, str] = {"gpu_utilization_percent": "node"}
        selected_vllm_stats: dict[str, Any] = {}
        selected_vllm = select_vllm_observations(current_series, self.thresholds)
        if any(current_series.get(name) for name in VLLM_OBSERVATIONS):
            for name in VLLM_OBSERVATIONS:
                signal = 'vllm_preemptions_delta' if name == 'vllm_preemptions_total' else name
                current_signals.pop(signal, None)
                baseline_signals.pop(signal, None)
                signal_labels.pop(signal, None)
        if selected_vllm is None:
            for name in VLLM_OBSERVATIONS:
                current_signals.pop("vllm_preemptions_delta" if name == "vllm_preemptions_total" else name, None)
            missing.append("vllm:shared_engine_identity")
        else:
            for name, item in selected_vllm.items():
                selected = vllm_identity(item)
                stats = item["stats"]
                selected_vllm_stats[name] = stats
                signal = "vllm_preemptions_delta" if name == "vllm_preemptions_total" else name
                value = stats.get("max_series_delta" if signal == "vllm_preemptions_delta" else "max")
                if value is None:
                    current_signals.pop(signal, None)
                else:
                    current_signals[signal] = value / 100 if name == "vllm_kv_cache_usage" and value > 1 else value
                signal_labels[signal] = dict(selected)
                matching_prior = [item for item in baseline_series.get(name, []) if vllm_identity(item) == selected]
                baseline_signals.pop(signal, None)
                if len(matching_prior) == 1:
                    prior_value = matching_prior[0]["stats"].get("max_series_delta" if signal == "vllm_preemptions_delta" else "max")
                    if prior_value is not None:
                        baseline_signals[signal] = prior_value / 100 if name == "vllm_kv_cache_usage" and prior_value > 1 else prior_value
        finding_evidence = dict(evidence)
        if "vllm:shared_engine_identity" in missing:
            for name in VLLM_OBSERVATIONS:
                finding_evidence.pop(name, None)
        else:
            if any(current_series.get(name) for name in VLLM_OBSERVATIONS):
                for name in VLLM_OBSERVATIONS:
                    finding_evidence.pop(name, None)
            finding_evidence.update(selected_vllm_stats)
        for name in VLLM_OBSERVATIONS:
            for item in current_series.get(name, []):
                observed_node = item.get('labels', {}).get('node')
                if observed_node and observed_node != node and observed_node not in clock_nodes:
                    missing.append(f'clock:{observed_node}:unregistered_observation_node')
                    clock_quality['nodes'][observed_node] = {'status': 'unknown', 'issues': ['unregistered_observation_node']}
                    clock_quality['required_nodes'] = sorted(set(clock_quality['required_nodes']) | {observed_node})
                    clock_quality['operating_scope'] = 'cross-node'
                    if clock_quality['status'] != 'unsafe':
                        clock_quality['status'] = 'unknown'
        def gpu_identity(item):
            labels = item.get("labels", {})
            return tuple((key, str(labels[key])) for key in ("cluster", "instance", "nodename", "node", "gpu", "gpu_uuid") if key in labels)
        before_by_gpu = {
            gpu_identity(item): item["stats"]
            for item in baseline_series.get("gpu_utilization_percent", [])
            if item.get("labels", {}).get("gpu") is not None
        }
        gpu_pairs = [
            (item["labels"], item["stats"], before_by_gpu[gpu_identity(item)])
            for item in current_series.get("gpu_utilization_percent", [])
            if item.get("labels", {}).get("gpu") is not None
            and gpu_identity(item) in before_by_gpu
        ]
        if gpu_pairs:
            gpu_labels, observed, prior = max(gpu_pairs, key=lambda item: item[2]["mean"] - item[1]["mean"])
            current_signals["gpu_utilization_percent"] = observed["mean"]
            baseline_signals["gpu_utilization_percent"] = prior["mean"]
            signal_labels["gpu_utilization_percent"] = dict(gpu_labels)
            signal_scopes["gpu_utilization_percent"] = "device"
        elif current_series.get("gpu_utilization_percent") and baseline_series.get("gpu_utilization_percent"):
            baseline_signals.pop("gpu_utilization_percent", None)
            missing.append("gpu:baseline_entity_match")
        if "gpu_memory_usage_ratio" in current_signals:
            memory = _gpu_memory_observations(current_series.get("gpu_memory_usage_ratio", []))
            evictions = _gpu_memory_observations(current_series.get("gpu_evictions_delta", []))

            def memory_ratio(item):
                value = item["stats"]["max"]
                return value / 100 if value > 1 else value

            def memory_score(identity):
                ratio = memory_ratio(memory[identity])
                eviction = evictions.get(identity, {}).get("stats", {}).get("max", 0)
                return (int(ratio >= self.thresholds["gpu_memory_ratio"]) + int(eviction >= 1), ratio)

            selected_memory = max(memory, key=memory_score) if memory else None
            had_evictions = "gpu_evictions_delta" in current_signals
            for name in ("gpu_memory_usage_ratio", "gpu_evictions_delta"):
                baseline_signals.pop(name, None)
            if selected_memory is not None:
                item = memory[selected_memory]
                current_signals["gpu_memory_usage_ratio"] = memory_ratio(item)
                signal_labels["gpu_memory_usage_ratio"] = dict(item["labels"])
                signal_scopes["gpu_memory_usage_ratio"] = "device"
            else:
                # Retain the observed high usage, but not an invented device.
                signal_labels["gpu_memory_usage_ratio"] = {}
                signal_scopes["gpu_memory_usage_ratio"] = "unknown"
            if selected_memory in evictions:
                item = evictions[selected_memory]
                current_signals["gpu_evictions_delta"] = item["stats"]["max"]
                signal_labels["gpu_evictions_delta"] = dict(item["labels"])
            else:
                current_signals.pop("gpu_evictions_delta", None)
                signal_labels.pop("gpu_evictions_delta", None)
                if had_evictions:
                    missing.append("gpu:memory_entity_match")
            for name in ("gpu_memory_usage_ratio", "gpu_evictions_delta"):
                if name not in current_signals:
                    continue
                previous = _gpu_memory_observations(baseline_series.get(name, [])).get(selected_memory)
                if previous is not None:
                    baseline_signals[name] = memory_ratio(previous) if name == "gpu_memory_usage_ratio" else previous["stats"]["max"]
                elif name in baseline_metrics:
                    missing.append(f"gpu:memory_baseline_entity_match:{name}")
        # Additional exporter profiles are compared per exact entity. Never
        # subtract different disks/interfaces/engines just because both supplied
        # a window maximum. Aggregate-only clients retain unknown-scope values
        # but cannot supply an entity-matched baseline for these signals.
        comparable_profiles = ENTITY_COMPARISON_SIGNALS
        for name, spec in comparable_profiles.items():
            items = [item for item in current_series.get(name, [])
                     if finite(item.get("stats", {}).get(spec.statistic)) is not None]
            if not items:
                if name in current_signals:
                    signal_scopes[name] = "unknown"
                    baseline_signals.pop(name, None)
                    if name in baseline_metrics:
                        missing.append(f"prometheus:{name}:baseline_entity_match")
                continue
            choose = min if spec.statistic == "min" else max
            selected_item = choose(items, key=lambda item: item["stats"][spec.statistic])
            def profile_identity(item):
                return {key: value for key, value in item.get("labels", {}).items() if key != "__name__"}
            selected_identity = profile_identity(selected_item)
            current_signals[name] = selected_item["stats"][spec.statistic]
            signal_labels[name] = selected_identity
            signal_scopes[name] = spec.scope if selected_identity else "unknown"
            baseline_signals.pop(name, None)
            matching = [item for item in baseline_series.get(name, [])
                        if selected_identity and EntityRef.decode(spec.scope, profile_identity(item)).matches(
                            EntityRef.decode(spec.scope, selected_identity))]
            if name in {"disk_busy_ratio","rdma_bytes_per_second","storage_device_busy_ratio","network_utilization_ratio"} and not (
                    any(selected_identity.get(k) for k in ("device","interface","port")) and any(
                        selected_identity.get(key) for key in ("instance", "node", "nodename"))):
                matching = []
                signal_scopes[name] = "unknown"
            if len(matching) == 1:
                previous_value = finite(matching[0].get("stats", {}).get(spec.statistic))
                if previous_value is not None:
                    baseline_signals[name] = previous_value
            elif name in baseline_metrics:
                missing.append(f"prometheus:{name}:baseline_entity_match")
        selected_3fs_metric = None
        selected_3fs_identity = None
        threefs_comparison = None
        if baseline_record:
            def latencies(rows: list[dict[str, Any]]) -> dict[tuple, float]:
                result = {}
                for identity, row in _distribution_index(rows).items():
                    value = finite(row.get("max_observed_p99"))
                    if _LATENCY_NAME.search(row["metricName"]) and value is not None:
                        result[identity] = value
                return result

            current_latency = latencies(threefs_rows)
            baseline_latency = latencies(threefs_baseline)
            comparable = [
                (identity, value, baseline_latency[identity])
                for identity, value in current_latency.items()
                if baseline_latency.get(identity, 0) > 0
            ]
            if comparable:
                identity, value, before = max(comparable, key=lambda item: item[1] / item[2])
                selected_3fs_identity = identity
                selected_3fs_metric = dict(identity)["metricName"]
                current_signals["threefs_p99_latency"] = value
                baseline_signals["threefs_p99_latency"] = before
                signal_labels["threefs_p99_latency"] = dict(identity)
                threefs_comparison = distribution_comparison_quality(
                    [_distribution_index(threefs_rows)[identity]], [_distribution_index(threefs_baseline)[identity]],
                    window, baseline_window)
            else:
                current_signals.pop("threefs_p99_latency", None)
                baseline_signals.pop("threefs_p99_latency", None)
                if current_latency:
                    missing.append("threefs:baseline_entity_match")
            request_metric = self.config.get("threefs", {}).get("request_size_metric")
            if isinstance(request_metric, str) and request_metric:
                current_signals.pop("storage_request_bytes", None)
                baseline_signals.pop("storage_request_bytes", None)
                requests = _distribution_index(threefs_rows)
                prior_requests = _distribution_index(threefs_baseline)
                pairs = [identity for identity, row in requests.items()
                         if row["metricName"] == request_metric
                         and identity in prior_requests
                         and finite(row.get("weighted_mean")) is not None
                         and finite(prior_requests[identity].get("weighted_mean")) is not None
                         and (selected_3fs_identity is None
                              or identity[1:] == selected_3fs_identity[1:])]
                # No implicit first/mean/max selection across request entities.
                # When paired with latency evidence the producer labels must
                # match as well; metricName necessarily differs across signals.
                if len(pairs) == 1:
                    identity = pairs[0]
                    current_signals["storage_request_bytes"] = float(requests[identity]["weighted_mean"])
                    baseline_signals["storage_request_bytes"] = float(prior_requests[identity]["weighted_mean"])
                    signal_labels["storage_request_bytes"] = dict(identity)
                elif any(row["metricName"] == request_metric for row in requests.values()):
                    missing.append("threefs:request_size_entity_match")
        elif "threefs_p99_latency" in current_signals:
            rows = [item for item in _distribution_index(threefs_rows).items()
                    if _LATENCY_NAME.search(item[1]["metricName"])
                    and finite(item[1].get("max_observed_p99")) is not None]
            if len(rows) == 1:
                signal_labels["threefs_p99_latency"] = dict(rows[0][0])
        # An adjacent shared-service window remains useful in the legacy
        # findings, but is not presented as a same-run step baseline.
        # Preserve the expression actually sent to Prometheus, keyed by the
        # canonical signal used by the rule catalog. Do not include backend
        # URLs or configuration, which can contain authentication material.
        signal_queries = {
            ("vllm_preemptions_delta" if name == "vllm_preemptions_total" else name): query
            for name, query in executed_queries.items()
        }
        sources = {name: "prometheus" for name in signal_queries}
        if tool_event_span is not None:
            sources["tool_duration_seconds"] = "event_span_time_window"
        sources.update({
            "threefs_p99_latency": "3fs_clickhouse",
            "step_duration_seconds": "workload",
            "rollout_duration_seconds": "workload",
            "communication_duration_seconds": "workload",
            "actor_update_duration_seconds": "workload",
            "critic_update_duration_seconds": "workload",
            "checkpoint_duration_seconds": "workload",
        })
        if selected_3fs_metric:
            sources["threefs_p99_latency"] = f"3fs_clickhouse:{selected_3fs_metric}"
        if self.config.get("threefs", {}).get("request_size_metric"):
            sources["storage_request_bytes"] = "3fs_clickhouse:" + str(self.config["threefs"]["request_size_metric"])

        # Selection changes the evidence population. Retain query annotations,
        # but use only that entity's evaluations for its quality assessment.
        for name,qualities in sampling_quality.items():
            signal='vllm_preemptions_delta' if name=='vllm_preemptions_total' else name
            labels=signal_labels.get(signal)
            if not labels:
                continue
            for role,series in (('current',current_series),('baseline',baseline_series)):
                matches=[item for item in series.get(name,[]) if all(item.get('labels',{}).get(k)==v for k,v in labels.items() if k!='__name__')]
                if role in qualities:
                    stats = matches[0].get('stats', {}) if len(matches) == 1 else {}
                    source = source_samples.get(name, {}).get(role, {})
                    # A singleton legacy client with no source labels has no
                    # alternate entity to borrow. Multi-entity clients must match.
                    if (any(row.get('labels') for row in source.get('series', []))
                            or len(series.get(name, [])) > 1 or len(source.get('series', [])) > 1):
                        interval = window if role == 'current' else baseline_window
                        qualities[role] = quality(executed_queries[name], interval['start'], interval['end'], step,
                            stats, source=source_for_entity(source, labels), result=qualities[role].get('query_result'))
                    qualities[role] = selected_quality(qualities[role], stats)

        signal_rows = describe_signals(
            compare_signals(current_signals, baseline_signals, scopes=signal_scopes, labels=signal_labels),
            sampling_quality, signal_queries)
        bundle = EvidenceBundle.from_rows(signal_rows, window=window,
            baseline_window=baseline_window if baseline_record else None,
            clock_quality=clock_quality, missing=missing)
        rule_current, rule_baseline = bundle.available_signals(), bundle.available_signals('baseline')
        changed_replica_signals = set()
        if serving:
            for replica in self.config['rollout_replicas']:
                issues = comparison_issues(serving['current'].get(replica['id'], {}), serving['baseline'].get(replica['id'], {})) if baseline_record else []
                for signal, labels in signal_labels.items():
                    if labels.get('instance') != replica['instance'] or labels.get('node') != replica['endpoint_node']:
                        continue
                    if issues:
                        rule_baseline.pop(signal, None); changed_replica_signals.add(signal)
                        missing.extend(replica['id'] + ':' + issue for issue in issues)
                    if serving['current'].get(replica['id'], {}).get('serving_state') in {'sleeping','weight_update','waking'}:
                        finding_evidence.pop('vllm_preemptions_total' if signal=='vllm_preemptions_delta' else signal, None)
        for name,qualities in sampling_quality.items():
            signal='vllm_preemptions_delta' if name=='vllm_preemptions_total' else name
            for role,values in (('current',rule_current),('baseline',rule_baseline)):
                invalid=INVALID_SOURCE_TIME.intersection(qualities.get(role,{}).get('warnings',[]))
                if invalid:
                    missing.extend(f'prometheus:{name}:{role}:{issue}' for issue in sorted(invalid))
                    if role=='current':
                        finding_evidence.pop(name,None)

        aliases=self.config.get('threefs',{}).get('time_series',{}).get('host_clock_nodes',{})
        def storage_clock_ok(rows,clock):
            return producer_hosts_verified(clock,[row.get('labels',{}).get('host') for row in _distribution_index(rows).values()],aliases)
        storage_clock={'current':storage_clock_ok(threefs_rows,clock_quality),
                       'baseline':storage_clock_ok(threefs_baseline,clock_quality.get('baseline',{}))}
        storage_comparable=all(storage_clock.values())
        if threefs_comparison is not None and threefs_comparison['status'] != 'shared_report_window':
            rule_baseline.pop('threefs_p99_latency', None)
            missing.append('threefs:comparison:' + threefs_comparison['status'])
        if threefs is not None:
            for role,values in (('current',rule_current),('baseline',rule_baseline)):
                if not storage_clock[role]:
                    values.pop('threefs_p99_latency',None)
                    if self.config.get('threefs',{}).get('request_size_metric'):
                        values.pop('storage_request_bytes',None)
                    missing.append(f'threefs:producer_clock_alignment:{role}')
        findings=self._findings(finding_evidence,threefs_rows if storage_comparable else [],
            threefs_baseline if storage_comparable else [],float(start),end,execution_mode,
            baseline_window={'start': baseline_start, 'end': baseline_end} if threefs_rows and threefs_baseline else None)
        candidates = evaluate_stage(
            bundle, rule_current, rule_baseline, thresholds=self.thresholds, enabled=bool(current),
            context={"sources": sources, "window": window,
                     "boundary_accuracy": window.get("accuracy", "unknown"),
                     "node": node,
                     "compute_node": compute_node,
                     "rollout_node": rollout_node,
                     "sandbox_node": sandbox_node if sandbox_config.get("enabled") else None,
                     "sandbox_device": sandbox_device if sandbox_config.get("enabled") else None,
                     "related_spans": (current or {}).get("related_spans", []),
                     "tool_related_spans": [tool_event_span] if tool_event_span else [],
                     "queries": signal_queries,
                     "signal_labels": signal_labels,
                     "signal_scopes": signal_scopes,
                     "signal_windows": {"tool_duration_seconds": tool_event["window"]} if tool_event is not None else {},
                     "signal_boundary_accuracy": {"tool_duration_seconds": tool_event["boundary_accuracy"]} if tool_event is not None else {},
                     "participant_durations_seconds": (current or {}).get("participant_durations_seconds", {})},
        )
        unsafe_timing = not bundle.timing_usable
        storage_series = None
        series_settings = self.config.get("threefs", {}).get("time_series", {})
        if series_settings.get("enabled", False):
            storage_series = collect_storage_series(threefs, window, baseline_window if baseline_record else None,
                settings=series_settings, clock_quality=clock_quality, queried_at=now)
            for role in ("current", "baseline"):
                for issue in storage_series.get(role, {}).get("errors", []):
                    missing.append(f"threefs:series:{role}:{issue}")
            apply_collection_limits(candidates)
        if unsafe_timing:
            # Keep raw evidence inspectable; do not use an unaligned resource
            # window as a bottleneck hypothesis for this workload interval.
            candidates = []
            findings = []
        normalization = None
        if baseline_policy.get("normalize_by") and current and baseline_record:
            token_field = baseline_policy["normalize_by"]
            tokens = finite(current.get("workload", {}).get(token_field))
            previous_tokens = finite(baseline_record.get("workload", {}).get(token_field))
            if tokens and previous_tokens and tokens > 0 and previous_tokens > 0:
                duration = current_signals.get("step_duration_seconds")
                previous_duration = baseline_signals.get("step_duration_seconds")
                if duration is not None and previous_duration is not None:
                    normalization = {"field": token_field, "unit": "seconds/token",
                                     "current": duration/tokens, "baseline": previous_duration/previous_tokens}
        comparison = {
            "current_interval": window,
            "baseline_interval": baseline_window if baseline_record else None,
            "baseline_record_id": (baseline_record or {}).get("record_id"),
            "selection": "same_run_same_worker_nearest_prior_median" if baseline_record else "unavailable",
            "signals": signal_rows,
            "workload_comparability": "matched_configured_fields" if baseline_record and baseline_policy.get("match_fields") else "unverified" if baseline_record else "unavailable",
            "match_fields": baseline_policy.get("match_fields", []),
            "normalization": normalization,
            "current_workload": (current or {}).get("workload", {}),
            "baseline_workload": (baseline_record or {}).get("workload", {}),
            **({'robust_baseline': robust_quality} if robust_quality is not None else {}),
        }
        if normalization is not None:
            normalized_current, normalized_baseline = normalization["current"], normalization["baseline"]
            comparison["signals"].append({"signal": "step_seconds_per_token", "scope": "application", "labels": {},
                                          "unit": "seconds/token", "current": normalized_current, "baseline": normalized_baseline,
                                          "delta": normalized_current-normalized_baseline,
                                          "delta_percent": 100*(normalized_current-normalized_baseline)/normalized_baseline if normalized_baseline else None})
        candidates = assess_candidate_context(candidates, config=self.config, run_id=run_id,
            workload_comparability=comparison['workload_comparability'], missing=missing, tool_event=tool_event)
        candidates = cap_candidate_quality(candidates, bundle, sampling_quality, signal_queries)
        apply_robust_limits(candidates, robust_quality)
        if serving:
            apply_serving_limits(candidates, self.config['rollout_replicas'], serving['current'])
        comparison['signals'] = assess_comparisons(comparison['signals'], bundle)
        for row in comparison["signals"]:
            if robust_quality is not None and row['signal'] in robust_quality['signals']:
                row['robust_baseline'] = robust_quality['signals'][row['signal']]
            if row['signal'] in changed_replica_signals:
                row.update(delta=None, delta_percent=None, comparison_status='replica_context_changed')
            if row['signal'] == 'threefs_p99_latency' and threefs_comparison is not None:
                row['comparison_quality'] = threefs_comparison
                if threefs_comparison['status'] != 'shared_report_window':
                    row.update(delta=None, delta_percent=None, comparison_status=threefs_comparison['status'])
            if row['signal'] in {'threefs_p99_latency','storage_request_bytes'} and threefs is not None and not storage_comparable:
                row.update(delta=None,delta_percent=None,comparison_status='producer_clock_unverified')
        # Raw empty distributions and an observed baseline cannot establish
        # that the current resource interval was measured.
        invalid_current={name for name,qualities in sampling_quality.items() if INVALID_SOURCE_TIME.intersection(qualities.get('current',{}).get('warnings',[]))}
        external_count = len(set(evidence) - {"slow_stages", "threefs_distributions", "threefs_baseline_distributions"} - invalid_current) + int(current_3fs_samples and storage_clock['current'])
        verdict = "bottleneck_suspected" if findings or candidates else "no_anomaly_observed"
        if unsafe_timing:
            verdict = "insufficient_data"
        if external_count == 0 and not candidates:
            verdict = "insufficient_data"
        limitations = []
        if comparison["workload_comparability"] == "unverified":
            limitations.append("Baseline workload comparability is unverified; configure baseline.match_fields before interpreting duration growth as a resource symptom.")
        elif baseline_policy.get("match_fields") and not baseline_record:
            missing.append("baseline:comparable_workload")
        if unsafe_timing:
            limitations.append("Clock alignment is unsafe or unknown; cross-layer diagnosis and baseline deltas are withheld. Raw resource windows remain available for inspection.")
        if clock_quality["status"] == "unchecked":
            limitations.append("Clock alignment was not checked. Configure cluster to enable Node Exporter clock checks.")
        if threefs is not None and not storage_comparable:
            limitations.append("3FS producer clocks were not checked; shared-service timestamp alignment requires threefs.clock_nodes covering its producers.")
            for candidate in candidates:
                if any(item.get("signal", "").startswith("threefs_") for item in candidate.get("evidence", [])):
                    candidate["missing_evidence"].append("threefs_producer_clock_alignment")
                    if candidate["state"] == "strong_signal":
                        candidate["state"] = "supporting_signal"
        candidates = cap_candidate_quality(candidates, bundle, sampling_quality, signal_queries)
        profiles = validate_metric_profiles(self.config["prometheus"])
        if profiles:
            limitations.append("Extended exporter evidence is node/device/shared-service scoped, not owned by the run. Window extrema can refer to different entities; comparisons match exact labels. Rate windows can extend before the step, p95 values are per-entity histogram estimates, and missing metrics do not mean zero.")
        if execution_mode == "async":
            limitations.append(
                "The VERL record is a trainer-update boundary; continuous vLLM, Ray, and 3FS activity is not owned by this step."
            )
        if window.get("accuracy") == "periodic":
            limitations.append("The analysis window is a periodic lookback and has no step ownership.")
        elif window.get("accuracy") != "exact":
            limitations.append("The analysis window is inferred from file-logger observation time and reported duration.")
        return {
            "schema_version": 1,
            "record_type": "bottleneck_diagnosis",
            "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "run_id": run_id,
            "data_origin": "observed",
            "node": node,
            "execution_mode": execution_mode,
            "trigger": "step_observed" if current else "periodic",
            "trigger_record_id": (current or {}).get("record_id"),
            "step": (current or {}).get("step"),
            "worker_id": (current or {}).get("worker_id"),
            "boundary_scope": (current or {}).get("boundary_scope", "continuous_window"),
            "analysis_window": window,
            "clock_quality": clock_quality,
            **({'threefs_clock_quality':storage_clock} if threefs is not None else {}),
            "verdict": verdict,
            "findings": findings,
            "evidence": evidence,
            "sampling_quality": sampling_quality,
            **({'rollout_replicas': replica_observations({**self.config, 'node': node}, current_series, baseline_series,
                source_samples, executed_queries, sampling_quality, window, baseline_window if baseline_record else {},
                clock_quality, self.thresholds, serving=serving)} if self.config.get('rollout_replicas') else {}),
            **({'async_decision': current['async_decision']} if current and current.get('async_decision') else {}),
            "metric_profiles": profiles,
            "query_execution": budget.summary(),
            "storage_overview": storage_overview(comparison, signal_queries, missing,
                unsafe_clock=unsafe_timing, threefs_configured=threefs is not None,
                threefs_rows=threefs_rows if current_3fs_samples else [], profiles=profiles,
                sampling_quality=sampling_quality),
            **({"storage_series": storage_series} if storage_series is not None else {}),
            "sandbox_device_mapping": sandbox_device_mapping,
            "diagnosis_method": "rule",
            "missing_sources": missing,
            "limitations": limitations,
            "diagnosis_schema_version": 1,
            "symptom": {
                "step": (current or {}).get("step"),
                "step_duration_seconds": current_signals.get("step_duration_seconds"),
                "slow_stages": slow,
                "boundary_scope": (current or {}).get("boundary_scope", "continuous_window"),
            },
            "comparison": {**comparison, "signals": []} if unsafe_timing else comparison,
            "candidates": candidates,
        }

    _signals = staticmethod(signal_values)

    def _findings(self, evidence, threefs_rows, threefs_baseline, start, end,
                  execution_mode, *, baseline_window=None):
        return legacy_findings(self.thresholds, evidence, threefs_rows, threefs_baseline,
                               start, end, execution_mode, baseline_window=baseline_window)


def _prepare_batch(engine, history_path, output, *, periodic_when_idle=True,
                   max_records=None, finalize_pending=False):
    history = load_history(history_path, cache=engine.jsonl_cache)
    reports, latest = _existing_reports(output / "diagnostics.jsonl", cache=engine.jsonl_cache)
    recovery = _projection_recovery(engine, output, reports, latest)
    settle = float(engine.config.get("threefs", {}).get("settle_seconds", 30)) if engine.threefs else 0.0
    now = engine.clock()
    retry_seconds = float(engine.config.get("retry_seconds", 60))
    retry_interval = float(engine.config.get("retry_interval_seconds", 10))

    active_keys=set()
    def ready(record):
        return scheduling.ready(engine,record,settle,now,active_keys, reports.get(record.get("record_id"), {}))

    unfinished = [record for record in history
                  if reports.get(record.get("record_id"), {}).get("analysis_status") == "provisional"
                  or record.get("record_id") not in reports]
    pending = [record for record in unfinished if ready(record) and (
        finalize_pending or scheduling.elapsed(engine) >= (scheduling.retry_state(engine,reports.get(record.get('record_id'),{})).get('retry_at') or 0))]
    if hasattr(engine,'_settle_deadlines'):
        engine._settle_deadlines={key:value for key,value in engine._settle_deadlines.items() if key in active_keys}
    batch = pending[:max_records] if max_records is not None else pending
    return {"pending": [(record, {key: reports.get(record.get("record_id"), {}).get(key)
                                 for key in ("first_attempt_at", "revision", "retry_timing")}) for record in batch],
            "settle_wait_seconds": max([0.0, *[
                value - scheduling.elapsed(engine) for value in getattr(engine, '_settle_deadlines', {}).values()]]),
            "periodic": periodic_when_idle and not unfinished, "projection_recovery": recovery,
            "now": now, "retry_seconds": retry_seconds, "retry_interval": retry_interval}, history


def run_once(
    engine: DiagnosticEngine,
    history_path: Path,
    output: Path,
    *,
    periodic_when_idle: bool = True,
    max_records: int | None = None,
    finalize_pending: bool = False,
    analyzer=None,
) -> int:
    options = dict(periodic_when_idle=periodic_when_idle, max_records=max_records,
                   finalize_pending=finalize_pending)
    def prepare():
        if analyzer is None:
            return _prepare_batch(engine, history_path, output, **options)
        return analyzer.prepare(history_path, output, **options), []

    plan, history = prepare()
    if finalize_pending and plan.get("settle_wait_seconds", 0) > 0:
        # A fresh process with no same-boot proof must actually wait. Bound the
        # finalizer by its analysis budget and reevaluate in the same worker;
        # never manufacture clock alignment to make a step queryable.
        budget = float(engine.config.get("analysis_deadline_seconds", 60)) or 60
        time.sleep(min(plan["settle_wait_seconds"], budget))
        plan, history = prepare()
    if plan.get("record_type") == "bottleneck_diagnosis":
        write_report(output, plan)  # Failed scheduling cannot finalize any step.
        return 0
    repair_projections(output, plan.get("projection_recovery", {}))
    for record, previous in plan["pending"]:
        attempted_at = engine.clock()
        attempted_elapsed = scheduling.elapsed(engine)
        if analyzer is None:
            observed = observation_time(record)
            prior = [item for item in history if observed is not None
                     and observation_time(item) is not None and observation_time(item) < observed]
            report = engine.analyze(record, prior)
        else:
            report = analyzer.analyze(record, history_path)
        first_attempt = previous.get("first_attempt_at")
        if first_attempt is None:
            first_attempt = attempted_at
        completed_at = engine.clock()
        completed_elapsed = scheduling.elapsed(engine)
        first_elapsed = scheduling.retry_state(engine,previous).get('first_attempt',attempted_elapsed)
        query_incomplete = any(source.startswith(("prometheus:", "threefs:"))
                               for source in report["missing_sources"])
        retryable = (report["verdict"] == "insufficient_data" or query_incomplete) and (
            "step_event_time" not in report["missing_sources"])
        provisional = (retryable and not finalize_pending and plan["retry_seconds"] > 0
                       and completed_elapsed < first_elapsed + plan["retry_seconds"])
        report.update({"analysis_status": "provisional" if provisional else "final",
                       "revision": int(previous.get("revision") or 0) + 1,
                       "first_attempt_at": first_attempt,
                       "retry_at": completed_at+max(0,min(plan['retry_interval'],first_elapsed+plan['retry_seconds']-completed_elapsed)) if provisional else None,
                       "retry_timing":{'clock_id':getattr(engine,'elapsed_clock_id',None),'first_attempt':first_elapsed,
                           'retry_at':min(completed_elapsed+plan['retry_interval'],first_elapsed+plan['retry_seconds']) if provisional else None}})
        write_report(output, report)
    if plan["periodic"]:
        write_report(output, analyzer.analyze(None, history_path) if analyzer is not None else engine.analyze(None, history))
    return len(plan["pending"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--pending-only", action="store_true")
    parser.add_argument("--finalize-pending", action="store_true")
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--node")
    parser.add_argument("--execution-mode", choices=("sync", "async"))
    parser.add_argument("--interval", type=float, default=10)
    args = parser.parse_args()
    if finite(args.interval) is None or args.interval <= 0:
        parser.error("--interval must be finite and positive")
    config = load_config(args.config)
    if args.run_id:
        config["run_id"] = args.run_id
    if args.node:
        config["node"] = args.node
    if args.execution_mode:
        config["execution_mode"] = args.execution_mode
    engine = DiagnosticEngine(config)
    if args.check_config:
        return
    if args.history is None or args.output is None:
        parser.error("--history and --output are required unless --check-config is used")
    from .deadline import IsolatedAnalyzer
    import signal
    deadline = float(config.get("analysis_deadline_seconds", 60))
    analyzer = IsolatedAnalyzer(config, seconds=deadline) if deadline else None
    def interrupted(signum, frame):
        raise SystemExit(128 + signum)
    previous_term = signal.signal(signal.SIGTERM, interrupted)
    try:
        while True:
            run_once(
                engine,
                args.history,
                args.output,
                periodic_when_idle=not args.pending_only,
                max_records=None if args.once else 20,
                finalize_pending=args.finalize_pending,
                analyzer=analyzer,
            )
            if args.once:
                return
            time.sleep(args.interval)
    finally:
        if analyzer is not None:
            analyzer.close()
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    main()
