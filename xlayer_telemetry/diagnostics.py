"""Correlate VERL update observations with external telemetry sources."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import heapq
import json
import math
import os
from pathlib import Path
import re
import statistics
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .diagnosis_analysis import compare_signals, evaluate_rules, finite, select_baseline, validate_baseline_policy, workload_matches
from .clock_quality import assess_clocks
from .evidence_quality import quality, check_source, validate_sampling
from .sandbox import device_window
from .fileio import atomic_write_text, json_objects
# Keep the established import path for SDK callers.
from .prometheus import PrometheusClient, escape_label


DEFAULT_QUERIES = {
    "gpu_utilization_percent": 'telemetry_gpu_utilization_percent{nodename="{compute_node}"}',
    "host_memory_available_ratio": 'node_memory_MemAvailable_bytes{instance="{node}"} / node_memory_MemTotal_bytes{instance="{node}"}',
    "disk_busy_ratio": 'rate(node_disk_io_time_seconds_total{instance="{node}"}[1m])',
    "vllm_requests_waiting": 'vllm:num_requests_waiting{node="{rollout_node}"}',
    "vllm_kv_cache_usage": 'vllm:kv_cache_usage_perc{node="{rollout_node}"}',
    "vllm_preemptions_total": 'vllm:num_preemptions_total{node="{rollout_node}"}',
    "ray_pending_tasks": 'ray_tasks{node="{rollout_node}",State=~"PENDING.*"}',
    "policy_version_lag": 'policy_version_lag{nodename="{node}",run_id="{run_id}"}',
    "host_swap_activity": 'rate(node_vmstat_pswpin{instance="{node}"}[1m]) + rate(node_vmstat_pswpout{instance="{node}"}[1m])',
    "disk_read_bytes_per_second": 'rate(node_disk_read_bytes_total{instance="{node}"}[1m])',
    "rdma_bytes_per_second": 'rate(node_infiniband_port_data_received_bytes_total{instance="{node}"}[1m]) + rate(node_infiniband_port_data_transmitted_bytes_total{instance="{node}"}[1m])',
}
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
_LATENCY_NAME = re.compile(r"latency|duration|elapsed|cost|time", re.IGNORECASE)


@dataclass
class ThreeFSClient:
    url: str
    database: str = "3fs"
    filters: Mapping[str, str] | None = None
    timeout: float = 5.0
    user_env: str = "THREEFS_CLICKHOUSE_USER"
    password_env: str = "THREEFS_CLICKHOUSE_PASSWORD"

    def __post_init__(self) -> None:
        if not _DATABASE.fullmatch(self.database):
            raise ValueError("3FS ClickHouse database must be an identifier")
        invalid = set(self.filters or {}) - _ALLOWED_3FS_FILTERS
        if invalid:
            raise ValueError(f"unsupported 3FS filters: {sorted(invalid)}")
        if any(not isinstance(value, str) for value in (self.filters or {}).values()):
            raise ValueError("3FS filter values must be strings")

    @staticmethod
    def _literal(value: str) -> str:
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

    def query_window(self, start: float, end: float) -> list[dict[str, Any]]:
        clauses = [f"TIMESTAMP >= toDateTime({int(start)})", f"TIMESTAMP < toDateTime({int(end)})"]
        clauses.extend(
            f"{name} = {self._literal(value)}"
            for name, value in sorted((self.filters or {}).items())
        )
        where = " AND ".join(clauses)
        query = (
            "SELECT metricName, sum(`count`) AS sample_count, "
            "if(sum(`count`)=0,0,sum(mean*`count`)/sum(`count`)) AS weighted_mean, "
            "max(`max`) AS max_value, max(p99) AS max_observed_p99 "
            f"FROM {self.database}.distributions WHERE {where} GROUP BY metricName "
            "FORMAT JSONEachRow"
        )
        params = urlencode({"database": self.database})
        request = Request(
            self.url.rstrip("/") + "/?" + params,
            data=query.encode(),
            method="POST",
        )
        user = os.environ.get(self.user_env)
        password = os.environ.get(self.password_env, "")
        if user:
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            request.add_header("Authorization", "Basic " + token)
        with urlopen(request, timeout=self.timeout) as response:
            rows = [json.loads(line) for line in response if line.strip()]
        for row in rows:
            if "sample_count" in row:
                row["count"] = row.pop("sample_count")
            if "max_value" in row:
                row["max"] = row.pop("max_value")
        return rows


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
    validate_sampling(config.get("sampling", {}))
    validate_baseline_policy(config.get("baseline", {}))
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
        settle = threefs.get("settle_seconds", 30)
        if type(settle) not in (int, float) or not math.isfinite(settle) or settle < 0:
            raise ValueError("threefs.settle_seconds must be finite and nonnegative")
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
    for key in ("max_skew_seconds", "max_sample_age_seconds"):
        if key in clocks and (finite(clocks[key]) is None or clocks[key] <= 0):
            raise ValueError(f"clock.{key} must be finite and positive")
    if clocks.get("enabled", False) and not config.get("cluster"):
        raise ValueError("clock checks require cluster")
    if threefs and "clock_nodes" in threefs and (
        not isinstance(threefs["clock_nodes"], list) or not threefs["clock_nodes"]
        or any(not isinstance(node, str) or not node for node in threefs["clock_nodes"])
    ):
        raise ValueError("threefs.clock_nodes must be a nonempty list of node names")
    return config


def load_history(path: Path) -> list[dict[str, Any]]:
    records = []
    try:
        for record in json_objects(path):
            if (record.get("schema_version") == 1 and record.get("record_type") == "verl_step_observation"
                    and isinstance(record.get("record_id"), str)):
                records.append(record)
    except FileNotFoundError:
        pass
    return records


def tool_span_window(directory: Path, run_id: str, start: float, end: float,
                     *, tool_name: str | None = None) -> dict[str, Any] | None:
    """Use completed tool spans fully inside an analysis interval.

    A VERL step interval can be approximate. Time overlap supplies correlation,
    not proof that the trainer step owns the tool or its sandbox I/O.
    """
    longest: dict[str, Any] | None = None
    max_duration: float | None = None
    count = 0
    try:
        paths = list(directory.glob("agent*.jsonl"))
    except OSError:
        return None
    for path in paths:
        try:
            for item in json_objects(path):
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
                began = finite(item.get("start_time_unix_nano"))
                finished = finite(item.get("end_time_unix_nano"))
                duration = finite(item.get("duration_seconds"))
                if (began is not None and finished is not None and duration is not None
                        and duration >= 0 and start <= began / 1e9
                        and finished / 1e9 <= end):
                    count += 1
                    if max_duration is None or duration > max_duration:
                        longest = item
                        max_duration = duration
        except OSError:
            continue
    if longest is None:
        return None
    return {"max": max_duration,
            "sample_count": count,
            "tool": longest["attributes"]["tool"],
            "related_span": f"{longest['trace_id']}:{longest['span_id']}"}


def _slow_stages(current: Mapping[str, Any], history: list[dict[str, Any]], thresholds: Mapping[str, float]) -> list[dict[str, float]]:
    previous: dict[str, list[float]] = {}
    recent = heapq.nlargest(5, history, key=lambda record: finite(record.get("observed_at")) or 0)
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


class DiagnosticEngine:
    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        prometheus: PrometheusClient | None = None,
        threefs: ThreeFSClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
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
        self.clock = clock
        self.thresholds = {**DEFAULT_THRESHOLDS, **config.get("thresholds", {})}

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
        queries.update(custom)
        if self.config.get("storage_node") and self.config.get("storage_device"):
            queries.setdefault("storage_device_busy_ratio", (
                'rate(node_disk_io_time_seconds_total{cluster="{cluster}",job="telemetry",'
                'instance="{storage_node}",device="{storage_device}"}[1m])'
            ))
        if with_sandbox:
            sandbox = self.config.get("sandbox", {})
            defaults = {
                "tool_duration_seconds": 'agent_tool_call_duration_seconds{run_id="{run_id}"}',
                "sandbox_io_pressure_ratio": 'sandbox_io_pressure_ratio{nodename="{sandbox_node}",role="sandbox"}',
            }
            if sandbox.get("device"):
                defaults["sandbox_device_busy_ratio"] = (
                    'rate(node_disk_io_time_seconds_total{nodename="{sandbox_node}",device="{sandbox_device}"}[1m])'
                )
            for name, template in defaults.items():
                if cluster:
                    template = template.replace('{', '{cluster="{cluster}",job="telemetry",', 1)
                queries.setdefault(name, template)
        return queries

    def analyze(self, current: Mapping[str, Any] | None, history: list[dict[str, Any]]) -> dict[str, Any]:
        now = self.clock()
        lookback = float(self.config.get("lookback_seconds", 60))
        raw_window = (current or {}).get("analysis_window", {})
        window = dict(raw_window) if isinstance(raw_window, Mapping) else {}
        if current and (finite(window.get("start")) is None or finite(window.get("end")) is None
                        or float(window["start"]) >= float(window["end"])):
            return {
                "schema_version": 1, "record_type": "bottleneck_diagnosis",
                "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                "run_id": current.get("run_id"), "node": current.get("node"),
                "execution_mode": current.get("execution_mode", "sync"),
                "data_origin": "observed", "trigger": "step_observed",
                "trigger_record_id": current.get("record_id"), "step": current.get("step"),
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
        end = float(window.get("end") or now)
        start = window.get("start")
        if type(start) not in (int, float) or start >= end:
            start = max(0.0, end - lookback)
            window = {"start": start, "end": end, "accuracy": "periodic", "source": "diagnostic_lookback"}
        node = str((current or {}).get("node") or self.config.get("node", ""))
        compute_node = str(self.config.get("compute_node") or node)
        rollout_node = str(self.config.get("rollout_node") or node)
        storage_node = str(self.config.get("storage_node") or node)
        cluster = str(self.config.get("cluster", ""))
        run_id = str((current or {}).get("run_id") or self.config.get("run_id", ""))
        execution_mode = str((current or {}).get("execution_mode") or self.config.get("execution_mode", "sync"))
        baseline_policy = self.config.get("baseline", {})
        validate_baseline_policy(baseline_policy)
        comparable_history = [
            item for item in history
            if current and item.get("run_id") == current.get("run_id")
            and item.get("node") == current.get("node")
            and item.get("worker_id") == current.get("worker_id")
            and item.get("boundary_scope") == current.get("boundary_scope")
            and workload_matches(current, item, baseline_policy)
        ]
        slow = _slow_stages(current or {}, comparable_history, self.thresholds)
        evidence: dict[str, Any] = {"slow_stages": slow}
        missing: list[str] = []
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
        baseline_record = select_baseline(current, history, policy=baseline_policy) if current else None
        baseline_window = (baseline_record or {}).get("analysis_window", {})
        baseline_window_valid = (finite(baseline_window.get("start")) is not None
                                 and finite(baseline_window.get("end")) is not None
                                 and baseline_window["start"] < baseline_window["end"])
        baseline_metrics: dict[str, Any] = {}
        sampling_quality: dict[str, dict] = {}
        current_series: dict[str, list[dict[str, Any]]] = {}
        baseline_series: dict[str, list[dict[str, Any]]] = {}
        clock_config = self.config.get("clock", {})
        clock_quality = {"status": "unchecked", "nodes": {}}
        if clock_config.get("enabled", bool(cluster)):
            clock_nodes = {node, compute_node, rollout_node, storage_node}
            if sandbox_config.get("enabled"):
                clock_nodes.add(sandbox_node)
            clock_nodes.update(self.config.get("threefs", {}).get("clock_nodes", []))
            clock_quality = assess_clocks(
                self.prometheus.query_range, cluster=cluster, nodes=clock_nodes,
                start=float(start), end=end,
                max_skew_seconds=float(clock_config.get("max_skew_seconds", 1)),
                max_sample_age_seconds=float(clock_config.get("max_sample_age_seconds", 30)),
                require_sync=clock_config.get("require_sync", True),
            )
            if clock_quality["status"] != "aligned":
                missing.extend(f"clock:{name}:{item['status']}" for name, item in clock_quality["nodes"].items() if item["status"] != "aligned")
            if baseline_window_valid:
                clock_quality["baseline"] = assess_clocks(
                    self.prometheus.query_range, cluster=cluster, nodes=clock_nodes,
                    start=float(baseline_window["start"]), end=float(baseline_window["end"]),
                    max_skew_seconds=float(clock_config.get("max_skew_seconds", 1)),
                    max_sample_age_seconds=float(clock_config.get("max_sample_age_seconds", 30)),
                    require_sync=clock_config.get("require_sync", True),
                )
                if clock_quality["baseline"]["status"] != "aligned":
                    missing.append("clock:baseline:unaligned")

        def query_with_detail(query: str, window_start: float, window_end: float) -> tuple[dict[str, float] | None, list[dict[str, Any]]]:
            if hasattr(self.prometheus, "query_range_detail"):
                detail = self.prometheus.query_range_detail(query, window_start, window_end, step)
                return detail["aggregate"], detail["series"]
            return self.prometheus.query_range(query, window_start, window_end, step), []

        for name, template in queries.items():
            try:
                query = str(template)
                for key, value in query_context.items():
                    query = query.replace("{" + key + "}", escape_label(value))
                stats, series = query_with_detail(query, float(start), end)
                source_sample = check_source(self.prometheus, query, float(start), end, step) if self.config.get("sampling", {}).get("check_source_freshness") else {}
                sampling_quality[name] = {"current": quality(query, float(start), end, step, stats, source=source_sample)}
                if stats is None:
                    missing.append("prometheus:" + name)
                else:
                    evidence[name] = stats
                    current_series[name] = series
                if baseline_window_valid:
                    prior, prior_series = query_with_detail(
                        query, float(baseline_window["start"]),
                        float(baseline_window["end"]),
                    )
                    baseline_start, baseline_end = float(baseline_window["start"]), float(baseline_window["end"])
                    prior_source = check_source(self.prometheus, query, baseline_start, baseline_end, step) if self.config.get("sampling", {}).get("check_source_freshness") else {}
                    sampling_quality[name]["baseline"] = quality(query, baseline_start, baseline_end, step, prior, source=prior_source)
                    if prior is not None:
                        baseline_metrics[name] = prior
                        baseline_series[name] = prior_series
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                missing.append(f"prometheus:{name}:{type(exc).__name__}")

        sandbox_device_mapping = None
        if sandbox_config.get("enabled") and sandbox_config.get("events_dir"):
            sandbox_device_mapping = device_window(Path(sandbox_config["events_dir"]), run_id, sandbox_node,
                                                  float(start), end, sandbox_config.get("device_major_minor"))
        tool_event_span = None
        if sandbox_config.get("enabled") and sandbox_config.get("events_dir") and current:
            directory = Path(sandbox_config["events_dir"])
            tool_event = tool_span_window(directory, run_id, float(start), end)
            if tool_event is not None:
                evidence["tool_duration_seconds"] = tool_event
                tool_event_span = tool_event["related_span"]
                queries.pop("tool_duration_seconds", None)
                missing = [item for item in missing
                           if not item.startswith("prometheus:tool_duration_seconds")]
                baseline_metrics.pop("tool_duration_seconds", None)
            if baseline_window_valid:
                if tool_event is not None:
                    previous_tool = tool_span_window(
                        directory, run_id, float(baseline_window["start"]),
                        float(baseline_window["end"]), tool_name=tool_event["tool"],
                    )
                    if previous_tool is not None:
                        baseline_metrics["tool_duration_seconds"] = previous_tool

        threefs_rows: list[dict[str, Any]] = []
        threefs_baseline: list[dict[str, Any]] = []
        if self.threefs is not None:
            try:
                duration = end - float(start)
                threefs_rows = self.threefs.query_window(float(start), end)
                baseline_start = float(baseline_window["start"]) if baseline_window_valid else float(start) - duration
                baseline_end = float(baseline_window["end"]) if baseline_window_valid else float(start)
                threefs_baseline = self.threefs.query_window(baseline_start, baseline_end)
                if threefs_rows:
                    evidence["threefs_distributions"] = threefs_rows
                    evidence["threefs_baseline_distributions"] = threefs_baseline
                else:
                    missing.append("threefs:no_data")
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                missing.append(f"threefs:{type(exc).__name__}")

        current_signals = self._signals(current, evidence, threefs_rows)
        baseline_signals = self._signals(baseline_record, baseline_metrics, threefs_baseline) if baseline_record else {}
        signal_labels: dict[str, dict[str, str]] = {
            ("vllm_preemptions_delta" if name == "vllm_preemptions_total" else name): dict(items[0].get("labels", {}))
            for name, items in current_series.items() if len(items) == 1
        }
        signal_scopes: dict[str, str] = {"gpu_utilization_percent": "node"}
        vllm_names = ("vllm_requests_waiting", "vllm_kv_cache_usage", "vllm_preemptions_total")
        identity_keys = ("cluster", "node", "instance", "component", "engine", "engine_id", "model_name", "model")
        identity_fields = ("instance", "component", "engine", "engine_id", "model_name", "model")
        detailed = [current_series.get(name, []) for name in vllm_names]
        selected_vllm_stats: dict[str, Any] = {}
        available = {name: items for name, items in zip(vllm_names, detailed) if items}
        if available:
            def entity(item: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
                labels = item.get("labels", {})
                return tuple((key, str(labels[key])) for key in identity_keys if key in labels)

            identities = [{entity(item) for item in items} for items in available.values()]
            ambiguous = any(len(items) != len(keys) for items, keys in zip(available.values(), identities))
            shared = set.intersection(*identities)
            if not shared or ambiguous or (len(set.union(*identities)) > 1 and not any(
                    any(key in identity_fields for key, _ in identity) for identity in shared)):
                for name in vllm_names:
                    current_signals.pop("vllm_preemptions_delta" if name == "vllm_preemptions_total" else name, None)
                missing.append("vllm:shared_engine_identity")
            else:
                def score(identity: tuple[tuple[str, str], ...]) -> tuple[int, float]:
                    stats = {name: next(item["stats"] for item in items if entity(item) == identity)
                             for name, items in available.items()}
                    kv = stats.get("vllm_kv_cache_usage", {}).get("max") or 0
                    kv = kv / 100 if kv > 1 else kv
                    waiting = stats.get("vllm_requests_waiting", {}).get("max") or 0
                    preemptions = stats.get("vllm_preemptions_total", {}).get("max_series_delta") or 0
                    return (int(kv >= self.thresholds["vllm_kv_usage"])
                            + int(waiting >= self.thresholds["vllm_waiting"])
                            + int(preemptions >= 1), kv)

                selected = max(sorted(shared), key=score)
                for name, items in available.items():
                    stats = next(item["stats"] for item in items if entity(item) == selected)
                    selected_vllm_stats[name] = stats
                    signal = "vllm_preemptions_delta" if name == "vllm_preemptions_total" else name
                    value = stats.get("max_series_delta" if signal == "vllm_preemptions_delta" else "max")
                    if value is None:
                        current_signals.pop(signal, None)
                    else:
                        current_signals[signal] = value / 100 if name == "vllm_kv_cache_usage" and value > 1 else value
                    signal_labels[signal] = dict(selected)
                    matching_prior = [item for item in baseline_series.get(name, []) if entity(item) == selected]
                    baseline_signals.pop(signal, None)
                    if len(matching_prior) == 1:
                        prior_value = matching_prior[0]["stats"].get("max_series_delta" if signal == "vllm_preemptions_delta" else "max")
                        if prior_value is not None:
                            baseline_signals[signal] = prior_value / 100 if name == "vllm_kv_cache_usage" and prior_value > 1 else prior_value
        finding_evidence = dict(evidence)
        if "vllm:shared_engine_identity" in missing:
            for name in vllm_names:
                finding_evidence.pop(name, None)
        else:
            finding_evidence.update(selected_vllm_stats)
        findings = self._findings(
            finding_evidence, threefs_rows, threefs_baseline, float(start), end, execution_mode
        )
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
        selected_3fs_metric = None
        if baseline_record:
            def latencies(rows: list[dict[str, Any]]) -> dict[str, float]:
                result = {}
                for row in rows:
                    name = str(row.get("metricName", ""))
                    count = finite(row.get("count"))
                    value = finite(row.get("max_observed_p99"))
                    if _LATENCY_NAME.search(name) and count is not None and count > 0 and value is not None:
                        result[name] = value
                return result

            current_latency = latencies(threefs_rows)
            baseline_latency = latencies(threefs_baseline)
            comparable = [
                (name, value, baseline_latency[name])
                for name, value in current_latency.items()
                if baseline_latency.get(name, 0) > 0
            ]
            if comparable:
                name, value, before = max(comparable, key=lambda item: item[1] / item[2])
                selected_3fs_metric = name
                current_signals["threefs_p99_latency"] = value
                baseline_signals["threefs_p99_latency"] = before
            else:
                current_signals.pop("threefs_p99_latency", None)
                baseline_signals.pop("threefs_p99_latency", None)
            request_metric = self.config.get("threefs", {}).get("request_size_metric")
            if isinstance(request_metric, str) and request_metric:
                for signals, rows in ((current_signals, threefs_rows), (baseline_signals, threefs_baseline)):
                    request = next((
                        row for row in rows
                        if row.get("metricName") == request_metric
                        and finite(row.get("count")) is not None
                        and float(row["count"]) > 0
                    ), None)
                    if request is not None and finite(request.get("weighted_mean")) is not None:
                        signals["storage_request_bytes"] = float(request["weighted_mean"])
        # An adjacent shared-service window remains useful in the legacy
        # findings, but is not presented as a same-run step baseline.
        sources = {name: "prometheus" for name in queries}
        if tool_event_span is not None:
            sources["tool_duration_seconds"] = "event_span_time_window"
        sources.update({
            "threefs_p99_latency": "3fs_clickhouse",
            "step_duration_seconds": "workload",
            "rollout_duration_seconds": "workload",
            "communication_duration_seconds": "workload",
        })
        if selected_3fs_metric:
            sources["threefs_p99_latency"] = f"3fs_clickhouse:{selected_3fs_metric}"
        if self.config.get("threefs", {}).get("request_size_metric"):
            sources["storage_request_bytes"] = "3fs_clickhouse:" + str(self.config["threefs"]["request_size_metric"])
        candidates = evaluate_rules(
            current_signals, baseline_signals, thresholds=self.thresholds,
            context={"sources": sources, "window": window,
                     "boundary_accuracy": window.get("accuracy", "unknown"),
                     "node": node,
                     "compute_node": compute_node,
                     "rollout_node": rollout_node,
                     "sandbox_node": sandbox_node if sandbox_config.get("enabled") else None,
                     "sandbox_device": sandbox_device if sandbox_config.get("enabled") else None,
                     "related_spans": (current or {}).get("related_spans", []),
                     "tool_related_spans": [tool_event_span] if tool_event_span else [],
                     "queries": queries,
                     "signal_labels": signal_labels,
                     "signal_scopes": signal_scopes,
                     "participant_durations_seconds": (current or {}).get("participant_durations_seconds", {})},
        ) if current else []
        unsafe_timing = (clock_quality["status"] not in {"aligned", "unchecked"}
                         or clock_quality.get("baseline", {}).get("status", "aligned") != "aligned")
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
            "signals": compare_signals(current_signals, baseline_signals, scopes=signal_scopes, labels=signal_labels),
            "workload_comparability": "matched_configured_fields" if baseline_record and baseline_policy.get("match_fields") else "unverified" if baseline_record else "unavailable",
            "match_fields": baseline_policy.get("match_fields", []),
            "normalization": normalization,
            "current_workload": (current or {}).get("workload", {}),
            "baseline_workload": (baseline_record or {}).get("workload", {}),
        }
        if normalization is not None:
            normalized_current, normalized_baseline = normalization["current"], normalization["baseline"]
            comparison["signals"].append({"signal": "step_seconds_per_token", "scope": "application", "labels": {},
                                          "unit": "seconds/token", "current": normalized_current, "baseline": normalized_baseline,
                                          "delta": normalized_current-normalized_baseline,
                                          "delta_percent": 100*(normalized_current-normalized_baseline)/normalized_baseline if normalized_baseline else None})
        for candidate in candidates:
            for item in candidate.get("evidence", []):
                name = "vllm_preemptions_total" if item["signal"] == "vllm_preemptions_delta" else item["signal"]
                item["sampling_quality"] = sampling_quality.get(name)
        for row in comparison["signals"]:
            name = "vllm_preemptions_total" if row["signal"] == "vllm_preemptions_delta" else row["signal"]
            row["sampling_quality"] = sampling_quality.get(name)
        external_count = len(evidence) - 1
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
        if self.threefs is not None and not self.config.get("threefs", {}).get("clock_nodes"):
            limitations.append("3FS producer clocks were not checked; shared-service timestamp alignment requires threefs.clock_nodes covering its producers.")
            for candidate in candidates:
                if any(item.get("signal", "").startswith("threefs_") for item in candidate.get("evidence", [])):
                    candidate["missing_evidence"].append("threefs_producer_clock_alignment")
                    if candidate["state"] == "strong_signal":
                        candidate["state"] = "supporting_signal"
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
            "boundary_scope": (current or {}).get("boundary_scope", "continuous_window"),
            "analysis_window": window,
            "clock_quality": clock_quality,
            "verdict": verdict,
            "findings": findings,
            "evidence": evidence,
            "sampling_quality": sampling_quality,
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

    @staticmethod
    def _signals(
        record: Mapping[str, Any] | None,
        metrics: Mapping[str, Any],
        threefs_rows: list[dict[str, Any]],
    ) -> dict[str, float]:
        signals: dict[str, float] = {}
        record = record or {}
        duration = finite(record.get("step_duration_seconds"))
        if duration is not None:
            signals["step_duration_seconds"] = duration
        stages = record.get("stage_durations_seconds", {})
        if isinstance(stages, Mapping):
            for signal, names in {
                "rollout_duration_seconds": ("rollout",),
                "communication_duration_seconds": ("weight_sync", "all_reduce", "collective"),
            }.items():
                values = [finite(stages.get(name)) for name in names]
                if signal == "rollout_duration_seconds" and values[0] is None:
                    values.append(finite(stages.get("gen")))
                if signal == "communication_duration_seconds" and finite(stages.get("weight_sync")) is None:
                    values.append(finite(stages.get("update_weights")))
                if any(value is not None for value in values):
                    signals[signal] = sum(value or 0 for value in values)
        fields = {
            "gpu_utilization_percent": "mean",
            "host_memory_available_ratio": "min",
            "host_swap_activity": "max",
            "disk_busy_ratio": "max",
            "tool_duration_seconds": "max",
            "sandbox_io_pressure_ratio": "max",
            "sandbox_device_busy_ratio": "max",
            "storage_device_busy_ratio": "max",
            "disk_read_bytes_per_second": "mean",
            "rdma_bytes_per_second": "mean",
            "vllm_requests_waiting": "max",
            "vllm_kv_cache_usage": "max",
            "vllm_preemptions_total": "max_series_delta",
            "gpu_memory_usage_ratio": "max",
            "gpu_evictions_delta": "max",
            "network_utilization_ratio": "max",
            "threefs_throughput_bytes_per_second": "mean",
            "storage_request_bytes": "mean",
        }
        for name, field in fields.items():
            stats = metrics.get(name)
            if isinstance(stats, Mapping):
                value = finite(stats.get(field))
                if value is not None:
                    target = "vllm_preemptions_delta" if name == "vllm_preemptions_total" else name
                    signals[target] = value / 100 if name in {"vllm_kv_cache_usage", "gpu_memory_usage_ratio"} and value > 1 else value
        latencies = [finite(row.get("max_observed_p99")) for row in threefs_rows if _LATENCY_NAME.search(str(row.get("metricName", ""))) and finite(row.get("count")) and float(row["count"]) > 0]
        if latencies:
            signals["threefs_p99_latency"] = max(value for value in latencies if value is not None)
        return signals

    def _findings(
        self,
        evidence: Mapping[str, Any],
        threefs_rows: list[dict[str, Any]],
        threefs_baseline: list[dict[str, Any]],
        start: float,
        end: float,
        execution_mode: str,
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        slow_names = {item["stage"] for item in evidence.get("slow_stages", [])}
        waiting = evidence.get("vllm_requests_waiting", {}).get("max", 0)
        kv = evidence.get("vllm_kv_cache_usage", {}).get("max", 0)
        kv_ratio = kv / 100 if kv > 1 else kv
        preemptions = evidence.get("vllm_preemptions_total", {}).get("max_series_delta") or 0
        if waiting >= self.thresholds["vllm_waiting"] or kv_ratio >= self.thresholds["vllm_kv_usage"] or preemptions > 0:
            finding = {"component": "vllm", "candidate": "rollout_capacity_or_kv_pressure", "signals": {"waiting_max": waiting, "kv_usage_max_ratio": kv_ratio, "preemptions_delta": preemptions}}
            if execution_mode == "async":
                finding["attribution"] = "continuous_window"
            else:
                finding["correlates_with_slow_stage"] = "gen" in slow_names
            findings.append(finding)
        pending = evidence.get("ray_pending_tasks", {}).get("max", 0)
        if pending >= self.thresholds["ray_pending_tasks"]:
            findings.append({"component": "ray", "candidate": "scheduling_backlog", "signals": {"pending_tasks_max": pending}})
        lag = evidence.get("policy_version_lag", {}).get("max", 0)
        if lag >= self.thresholds["policy_version_lag"]:
            findings.append({"component": "verl_async", "candidate": "policy_version_lag", "signals": {"version_lag_max": lag}})
        memory = evidence.get("host_memory_available_ratio", {}).get("min", 1)
        disk = evidence.get("disk_busy_ratio", {}).get("max", 0)
        gpu = evidence.get("gpu_utilization_percent", {}).get("mean", 100)
        if memory <= self.thresholds["memory_available_ratio"] or disk >= self.thresholds["disk_busy_ratio"]:
            findings.append({"component": "host", "candidate": "memory_or_storage_pressure", "signals": {"memory_available_min_ratio": memory, "disk_busy_max_ratio": disk, "gpu_utilization_mean_percent": gpu}, "correlates_with_slow_stage": bool(slow_names)})
        latency_rows = [row for row in threefs_rows if _LATENCY_NAME.search(str(row.get("metricName", "")))]
        baseline_by_name = {str(row.get("metricName")): row for row in threefs_baseline}
        elevated = []
        for row in latency_rows:
            baseline = baseline_by_name.get(str(row.get("metricName")), {})
            current_p99 = float(row.get("max_observed_p99", 0) or 0)
            baseline_p99 = float(baseline.get("max_observed_p99", 0) or 0)
            ratio = current_p99 / baseline_p99 if baseline_p99 > 0 else 0.0
            if ratio >= self.thresholds["threefs_latency_slowdown_ratio"]:
                elevated.append({**row, "baseline_max_observed_p99": baseline_p99, "ratio": ratio})
        if elevated:
            findings.append({"component": "3fs", "candidate": "latency_regression", "signals": {"metrics": elevated, "window_seconds": end - start}, "attribution": "shared_storage_window"})
        return findings


def _existing_reports(path: Path) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    try:
        for value in json_objects(path):
            record_id = value.get("trigger_record_id")
            if isinstance(record_id, str):
                reports[record_id] = value
    except FileNotFoundError:
        pass
    return reports


def write_report(directory: Path, report: Mapping[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, separators=(",", ":"), sort_keys=True) + "\n"
    with (directory / "diagnostics.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(encoded)
    atomic_write_text(directory / "latest.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    if report.get("trigger") != "step_observed" or report.get("analysis_status") == "provisional":
        return
    # One immutable file per analysis lets Alloy/Loki tail without rereading
    # rewritten content. Flatten only presentation fields; latest.json stays
    # the complete, backend-independent diagnosis artifact.
    investigation = directory / "investigation"
    investigation.mkdir(exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9_.-]", "_", str(report.get("trigger_record_id") or report.get("generated_at")))
    rows = _investigation_rows(report)
    atomic_write_text(investigation / f"{key}.jsonl", "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows))


def _investigation_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    def evidence_text(item: Mapping[str, Any]) -> str:
        labels = ",".join(f"{key}={value}" for key, value in item.get("labels", {}).items())
        suffix = f":{labels}" if labels else ""
        return f"{item['signal']}={item['value']} ({item['observation_scope']}{suffix})"

    def quality_fields(value):
        value = (value or {}).get("current", {})
        return {"query_step_seconds": value.get("query_step_seconds"),
                "range_window_seconds": value.get("range_window_seconds"),
                "evaluation_count": value.get("evaluation_count"),
                "source_age_seconds": value.get("source_age_seconds"),
                "quality_warnings": ", ".join(value.get("warnings", []))}

    window = report.get("analysis_window", {})
    comparison = report.get("comparison", {})
    baseline = comparison.get("baseline_interval") or {}
    common = {
        "schema_version": 1, "run_id": report.get("run_id"), "node": report.get("node"),
        "diagnosis_method": report.get("diagnosis_method", "rule"), "generated_at": report.get("generated_at"),
        "workload_comparability": comparison.get("workload_comparability", "unverified"),
        "sandbox_device_mapping": (report.get("sandbox_device_mapping") or {}).get("status", "not_configured"),
        "data_origin": report.get("data_origin", "observed"),
        "step": report.get("step"), "record_id": report.get("trigger_record_id"),
        "observed_at": window.get("end"), "boundary_accuracy": window.get("accuracy"),
        "window_start_ms": math.floor(window["start"] * 1000) if finite(window.get("start")) is not None else None,
        "window_end_ms": math.ceil(window["end"] * 1000) if finite(window.get("end")) is not None else None,
        "baseline_record_id": comparison.get("baseline_record_id"),
        "baseline_start_ms": math.floor(baseline["start"] * 1000) if finite(baseline.get("start")) is not None else None,
        "baseline_end_ms": math.ceil(baseline["end"] * 1000) if finite(baseline.get("end")) is not None else None,
    }
    symptom = report.get("symptom", {})
    strong = [item for item in report.get("candidates", []) if item.get("state") == "strong_signal"]
    rows = [{**common, "row_kind": "summary", "verdict": report.get("verdict"),
             "step_duration_seconds": symptom.get("step_duration_seconds"),
             "candidate_count": len(report.get("candidates", [])),
             "strong_candidate_count": len(strong),
             "primary_candidate": strong[0].get("id") if strong else None,
             "missing_sources": ", ".join(report.get("missing_sources", []))}]
    for candidate in report.get("candidates", []):
        rows.append({**common, "row_kind": "candidate", "candidate_id": candidate.get("id"),
                     "component": candidate.get("component"), "state": candidate.get("state"),
                     "summary": candidate.get("summary"),
                     "evidence_summary": "; ".join(evidence_text(item) for item in candidate.get("evidence", [])),
                     "counter_evidence_summary": ", ".join(item["signal"] for item in candidate.get("counter_evidence", [])),
                     "missing_evidence_summary": ", ".join(candidate.get("missing_evidence", [])),
                     "observation_scope": candidate.get("observation_scope")})
        for kind, items in (("supporting", candidate.get("evidence", [])),
                            ("counter", candidate.get("counter_evidence", []))):
            for item in items:
                rows.append({**common, "row_kind": "evidence", "candidate_id": candidate.get("id"),
                             "evidence_type": kind, "signal": item.get("signal"),
                             "current": item.get("value"), "baseline": item.get("baseline"),
                             "observation_scope": item.get("observation_scope"),
                             "source": item.get("source"),
                             "entity": ",".join(f"{key}={value}" for key, value in item.get("labels", {}).items()),
                             "sampling_quality": json.dumps(item.get("sampling_quality"), separators=(",", ":")),
                             **quality_fields(item.get("sampling_quality"))})
        for name in candidate.get("missing_evidence", []):
            rows.append({**common, "row_kind": "evidence", "candidate_id": candidate.get("id"),
                         "evidence_type": "missing", "signal": name,
                         "current": None, "baseline": None,
                         "observation_scope": candidate.get("observation_scope"),
                         "source": None, "entity": ""})
    for signal in comparison.get("signals", []):
        if signal.get("baseline") is None:
            continue
        rows.append({**common, "row_kind": "comparison", "signal": signal["signal"],
                     "observation_scope": signal["scope"],
                     "entity": ",".join(f"{key}={value}" for key, value in signal.get("labels", {}).items()),
                     "current": signal["current"],
                     "baseline": signal["baseline"], "delta_percent": signal["delta_percent"],
                     "sampling_quality": json.dumps(signal.get("sampling_quality"), separators=(",", ":")),
                     **quality_fields(signal.get("sampling_quality"))})
    return rows


def run_once(
    engine: DiagnosticEngine,
    history_path: Path,
    output: Path,
    *,
    periodic_when_idle: bool = True,
    max_records: int | None = None,
    finalize_pending: bool = False,
) -> int:
    history = load_history(history_path)
    reports = _existing_reports(output / "diagnostics.jsonl")
    settle = float(engine.config.get("threefs", {}).get("settle_seconds", 30)) if engine.threefs else 0.0
    now = engine.clock()
    retry_seconds = float(engine.config.get("retry_seconds", 60))
    retry_interval = float(engine.config.get("retry_interval_seconds", 10))

    def ready(record: Mapping[str, Any]) -> bool:
        window = record.get("analysis_window")
        end = finite(window.get("end")) if isinstance(window, Mapping) else None
        return end is None or now - end >= settle

    unfinished = [record for record in history
                  if reports.get(record.get("record_id"), {}).get("analysis_status") == "provisional"
                  or record.get("record_id") not in reports]
    pending = [record for record in unfinished if ready(record) and (
        finalize_pending or now >= reports.get(record.get("record_id"), {}).get("retry_at", 0))]
    if pending:
        batch = pending[:max_records] if max_records is not None else pending
        for record in batch:
            observed = finite(record.get("observed_at"))
            prior = [item for item in history if observed is not None
                     and finite(item.get("observed_at")) is not None and item["observed_at"] < observed]
            previous = reports.get(record.get("record_id"), {})
            report = engine.analyze(record, prior)
            first_attempt = previous.get("first_attempt_at", now)
            query_failed = any(source.startswith("prometheus:") and source.count(":") >= 2
                               or source.startswith("threefs:") and source != "threefs:no_data"
                               for source in report["missing_sources"])
            retryable = (report["verdict"] == "insufficient_data" or query_failed) and (
                "step_event_time" not in report["missing_sources"])
            provisional = (retryable and not finalize_pending and retry_seconds > 0
                           and now < first_attempt + retry_seconds)
            report.update({"analysis_status": "provisional" if provisional else "final",
                           "revision": int(previous.get("revision", 0)) + 1,
                           "first_attempt_at": first_attempt,
                           "retry_at": min(now + retry_interval, first_attempt + retry_seconds) if provisional else None})
            write_report(output, report)
    elif periodic_when_idle and not unfinished:
        write_report(output, engine.analyze(None, history))
    return len(batch) if pending else 0


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
    if args.interval <= 0:
        parser.error("--interval must be positive")
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
    while True:
        run_once(
            engine,
            args.history,
            args.output,
            periodic_when_idle=not args.pending_only,
            max_records=None if args.once else 20,
            finalize_pending=args.finalize_pending,
        )
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
