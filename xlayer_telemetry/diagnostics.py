"""Correlate VERL update observations with external telemetry sources."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import statistics
import time
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .diagnosis_analysis import compare_signals, evaluate_rules, finite, select_baseline


DEFAULT_QUERIES = {
    "gpu_utilization_percent": 'telemetry_gpu_utilization_percent{nodename="{node}"}',
    "host_memory_available_ratio": 'node_memory_MemAvailable_bytes{instance="{node}"} / node_memory_MemTotal_bytes{instance="{node}"}',
    "disk_busy_ratio": 'rate(node_disk_io_time_seconds_total{instance="{node}"}[1m])',
    "vllm_requests_waiting": 'vllm:num_requests_waiting{node="{node}"}',
    "vllm_kv_cache_usage": 'vllm:kv_cache_usage_perc{node="{node}"}',
    "vllm_preemptions_total": 'vllm:num_preemptions_total{node="{node}"}',
    "ray_pending_tasks": 'ray_tasks{State=~"PENDING.*"}',
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
}
_ALLOWED_3FS_FILTERS = {"host", "mount_name", "instance", "io", "uid", "pod", "method"}
_DATABASE = re.compile(r"^[A-Za-z0-9_]+$")
_LATENCY_NAME = re.compile(r"latency|duration|elapsed|cost|time", re.IGNORECASE)


def _read_json(request: Request, timeout: float) -> Any:
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _escape_prometheus(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _series_stats(series: Iterable[Mapping[str, Any]]) -> dict[str, float] | None:
    values: list[float] = []
    deltas: list[float] = []
    for item in series:
        current: list[float] = []
        for point in item.get("values", []):
            try:
                value = float(point[1])
            except (IndexError, TypeError, ValueError):
                continue
            if math.isfinite(value):
                current.append(value)
                values.append(value)
        if len(current) >= 2:
            deltas.append(max(0.0, current[-1] - current[0]))
    if not values:
        return None
    return {
        "min": min(values),
        "mean": statistics.fmean(values),
        "max": max(values),
        "last": values[-1],
        "max_series_delta": max(deltas, default=0.0),
        "sample_count": float(len(values)),
    }


@dataclass
class PrometheusClient:
    url: str
    timeout: float = 5.0

    def query_range(self, query: str, start: float, end: float, step: float) -> dict[str, float] | None:
        return self.query_range_detail(query, start, end, step)["aggregate"]

    def query_range_detail(self, query: str, start: float, end: float, step: float) -> dict[str, Any]:
        params = urlencode({"query": query, "start": start, "end": end, "step": step})
        request = Request(self.url.rstrip("/") + "/api/v1/query_range?" + params)
        payload = _read_json(request, self.timeout)
        if payload.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {payload.get('error', 'unknown error')}")
        matrix = payload.get("data", {}).get("result", [])
        return {
            "aggregate": _series_stats(matrix),
            "series": [
                {"labels": item.get("metric", {}), "stats": stats}
                for item in matrix
                if (stats := _series_stats([item])) is not None
            ],
        }


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
    if config.get("schema_version") != 1:
        raise ValueError("diagnostics config schema_version must be 1")
    if not isinstance(config.get("prometheus"), dict):
        raise ValueError("diagnostics config requires a prometheus object")
    if not isinstance(config["prometheus"].get("url"), str):
        raise ValueError("diagnostics config requires prometheus.url")
    thresholds = config.get("thresholds", {})
    if not isinstance(thresholds, dict) or any(
        type(value) not in (int, float) or not math.isfinite(value) or value < 0
        for value in thresholds.values()
    ):
        raise ValueError("diagnostic thresholds must be finite nonnegative numbers")
    threefs = config.get("threefs")
    if threefs is not None:
        if not isinstance(threefs, dict):
            raise ValueError("threefs must be an object")
        settle = threefs.get("settle_seconds", 30)
        if type(settle) not in (int, float) or not math.isfinite(settle) or settle < 0:
            raise ValueError("threefs.settle_seconds must be finite and nonnegative")
    return config


def load_history(path: Path) -> list[dict[str, Any]]:
    records = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("schema_version") == 1 and record.get("record_type") == "verl_step_observation":
                    records.append(record)
    except FileNotFoundError:
        pass
    return records


def _slow_stages(current: Mapping[str, Any], history: list[dict[str, Any]], thresholds: Mapping[str, float]) -> list[dict[str, float]]:
    previous: dict[str, list[float]] = {}
    for record in history[-5:]:
        for name, value in record.get("stage_durations_seconds", {}).items():
            if type(value) in (int, float):
                previous.setdefault(name, []).append(float(value))
    slow = []
    for name, value in current.get("stage_durations_seconds", {}).items():
        baseline = previous.get(name, [])
        if type(value) not in (int, float) or not baseline:
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

    def analyze(self, current: Mapping[str, Any] | None, history: list[dict[str, Any]]) -> dict[str, Any]:
        now = self.clock()
        lookback = float(self.config.get("lookback_seconds", 60))
        window = dict((current or {}).get("analysis_window", {}))
        end = float(window.get("end") or now)
        start = window.get("start")
        if type(start) not in (int, float) or start >= end:
            start = max(0.0, end - lookback)
            window = {"start": start, "end": end, "accuracy": "periodic", "source": "diagnostic_lookback"}
        node = str((current or {}).get("node") or self.config.get("node", ""))
        run_id = str((current or {}).get("run_id") or self.config.get("run_id", ""))
        execution_mode = str((current or {}).get("execution_mode") or self.config.get("execution_mode", "sync"))
        comparable_history = [
            item for item in history
            if current and item.get("run_id") == current.get("run_id")
            and item.get("worker_id") == current.get("worker_id")
            and item.get("boundary_scope") == current.get("boundary_scope")
        ]
        slow = _slow_stages(current or {}, comparable_history, self.thresholds)
        evidence: dict[str, Any] = {"slow_stages": slow}
        missing: list[str] = []
        queries = {**DEFAULT_QUERIES, **self.config["prometheus"].get("queries", {})}
        step = max(1.0, float(self.config["prometheus"].get("query_step_seconds", 2)))
        baseline_record = select_baseline(current, history) if current else None
        baseline_window = (baseline_record or {}).get("analysis_window", {})
        baseline_metrics: dict[str, Any] = {}
        current_series: dict[str, list[dict[str, Any]]] = {}
        baseline_series: dict[str, list[dict[str, Any]]] = {}

        def query_with_detail(query: str, window_start: float, window_end: float) -> tuple[dict[str, float] | None, list[dict[str, Any]]]:
            if hasattr(self.prometheus, "query_range_detail"):
                detail = self.prometheus.query_range_detail(query, window_start, window_end, step)
                return detail["aggregate"], detail["series"]
            return self.prometheus.query_range(query, window_start, window_end, step), []

        for name, template in queries.items():
            try:
                query = str(template).replace("{node}", _escape_prometheus(node)).replace("{run_id}", _escape_prometheus(run_id))
                stats, series = query_with_detail(query, float(start), end)
                if stats is None:
                    missing.append("prometheus:" + name)
                else:
                    evidence[name] = stats
                    current_series[name] = series
                if baseline_record:
                    prior, prior_series = query_with_detail(
                        query, float(baseline_window["start"]),
                        float(baseline_window["end"]),
                    )
                    if prior is not None:
                        baseline_metrics[name] = prior
                        baseline_series[name] = prior_series
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                missing.append(f"prometheus:{name}:{type(exc).__name__}")

        threefs_rows: list[dict[str, Any]] = []
        threefs_baseline: list[dict[str, Any]] = []
        if self.threefs is not None:
            try:
                duration = end - float(start)
                threefs_rows = self.threefs.query_window(float(start), end)
                baseline_start = float(baseline_window["start"]) if baseline_record else float(start) - duration
                baseline_end = float(baseline_window["end"]) if baseline_record else float(start)
                threefs_baseline = self.threefs.query_window(baseline_start, baseline_end)
                if threefs_rows:
                    evidence["threefs_distributions"] = threefs_rows
                    evidence["threefs_baseline_distributions"] = threefs_baseline
                else:
                    missing.append("threefs:no_data")
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                missing.append(f"threefs:{type(exc).__name__}")

        findings = self._findings(
            evidence, threefs_rows, threefs_baseline, float(start), end, execution_mode
        )
        current_signals = self._signals(current, evidence, threefs_rows)
        baseline_signals = self._signals(baseline_record, baseline_metrics, threefs_baseline) if baseline_record else {}
        signal_labels: dict[str, dict[str, str]] = {}
        signal_scopes: dict[str, str] = {"gpu_utilization_percent": "node"}
        before_by_gpu = {
            str(item.get("labels", {}).get("gpu")): item["stats"]
            for item in baseline_series.get("gpu_utilization_percent", [])
            if item.get("labels", {}).get("gpu") is not None
        }
        gpu_pairs = [
            (str(item["labels"]["gpu"]), item["stats"], before_by_gpu[str(item["labels"]["gpu"])])
            for item in current_series.get("gpu_utilization_percent", [])
            if item.get("labels", {}).get("gpu") is not None
            and str(item["labels"]["gpu"]) in before_by_gpu
        ]
        if gpu_pairs:
            gpu, observed, prior = max(gpu_pairs, key=lambda item: item[2]["mean"] - item[1]["mean"])
            current_signals["gpu_utilization_percent"] = observed["mean"]
            baseline_signals["gpu_utilization_percent"] = prior["mean"]
            signal_labels["gpu_utilization_percent"] = {"gpu": gpu}
            signal_scopes["gpu_utilization_percent"] = "device"
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
                     "related_spans": (current or {}).get("related_spans", []),
                     "queries": queries,
                     "signal_labels": signal_labels,
                     "signal_scopes": signal_scopes,
                     "participant_durations_seconds": (current or {}).get("participant_durations_seconds", {})},
        ) if current else []
        comparison = {
            "current_interval": window,
            "baseline_interval": baseline_window if baseline_record else None,
            "baseline_record_id": (baseline_record or {}).get("record_id"),
            "selection": "same_run_same_worker_nearest_prior_median" if baseline_record else "unavailable",
            "signals": compare_signals(current_signals, baseline_signals, scopes=signal_scopes, labels=signal_labels),
        }
        external_count = len(evidence) - 1
        verdict = "bottleneck_suspected" if findings or candidates else "no_anomaly_observed"
        if external_count == 0 and not candidates:
            verdict = "insufficient_data"
        limitations = []
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
            "verdict": verdict,
            "findings": findings,
            "evidence": evidence,
            "missing_sources": missing,
            "limitations": limitations,
            "diagnosis_schema_version": 1,
            "symptom": {
                "step": (current or {}).get("step"),
                "step_duration_seconds": current_signals.get("step_duration_seconds"),
                "slow_stages": slow,
                "boundary_scope": (current or {}).get("boundary_scope", "continuous_window"),
            },
            "comparison": comparison,
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
                "rollout_duration_seconds": ("gen", "rollout"),
                "communication_duration_seconds": ("weight_sync", "all_reduce", "collective"),
            }.items():
                values = [finite(stages.get(name)) for name in names]
                if any(value is not None for value in values):
                    signals[signal] = sum(value or 0 for value in values)
        fields = {
            "gpu_utilization_percent": "mean",
            "host_memory_available_ratio": "min",
            "host_swap_activity": "max",
            "disk_busy_ratio": "max",
            "storage_device_busy_ratio": "max",
            "disk_read_bytes_per_second": "mean",
            "rdma_bytes_per_second": "mean",
            "vllm_requests_waiting": "max",
            "vllm_kv_cache_usage": "max",
            "vllm_preemptions_total": "max_series_delta",
            "gpu_memory_usage_ratio": "max",
            "gpu_evictions_delta": "max_series_delta",
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
        preemptions = evidence.get("vllm_preemptions_total", {}).get("max_series_delta", 0)
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


def _existing_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                record_id = value.get("trigger_record_id")
                if isinstance(record_id, str):
                    ids.add(record_id)
    except FileNotFoundError:
        pass
    return ids


def write_report(directory: Path, report: Mapping[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, separators=(",", ":"), sort_keys=True) + "\n"
    with (directory / "diagnostics.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(encoded)
    temporary = directory / f".latest.json.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, directory / "latest.json")
    if report.get("trigger") != "step_observed":
        return
    # One immutable file per analysis lets Alloy/Loki tail without rereading
    # rewritten content. Flatten only presentation fields; latest.json stays
    # the complete, backend-independent diagnosis artifact.
    investigation = directory / "investigation"
    investigation.mkdir(exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9_.-]", "_", str(report.get("trigger_record_id") or report.get("generated_at")))
    rows = _investigation_rows(report)
    temporary_rows = investigation / f".{key}.{os.getpid()}.tmp"
    temporary_rows.write_text("".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    os.replace(temporary_rows, investigation / f"{key}.jsonl")


def _investigation_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    def evidence_text(item: Mapping[str, Any]) -> str:
        labels = ",".join(f"{key}={value}" for key, value in item.get("labels", {}).items())
        suffix = f":{labels}" if labels else ""
        return f"{item['signal']}={item['value']} ({item['observation_scope']}{suffix})"

    window = report.get("analysis_window", {})
    comparison = report.get("comparison", {})
    baseline = comparison.get("baseline_interval") or {}
    common = {
        "schema_version": 1, "run_id": report.get("run_id"), "node": report.get("node"),
        "data_origin": report.get("data_origin", "observed"),
        "step": report.get("step"), "record_id": report.get("trigger_record_id"),
        "observed_at": window.get("end"), "boundary_accuracy": window.get("accuracy"),
        "window_start_ms": int(window["start"] * 1000) if finite(window.get("start")) is not None else None,
        "window_end_ms": int(window["end"] * 1000) if finite(window.get("end")) is not None else None,
        "baseline_record_id": comparison.get("baseline_record_id"),
        "baseline_start_ms": int(baseline["start"] * 1000) if finite(baseline.get("start")) is not None else None,
        "baseline_end_ms": int(baseline["end"] * 1000) if finite(baseline.get("end")) is not None else None,
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
                             "entity": ",".join(f"{key}={value}" for key, value in item.get("labels", {}).items())})
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
                     "baseline": signal["baseline"], "delta_percent": signal["delta_percent"]})
    return rows


def run_once(
    engine: DiagnosticEngine,
    history_path: Path,
    output: Path,
    *,
    periodic_when_idle: bool = True,
    max_records: int | None = None,
) -> int:
    history = load_history(history_path)
    seen = _existing_ids(output / "diagnostics.jsonl")
    unseen = [record for record in history if record.get("record_id") not in seen]
    settle = float(engine.config.get("threefs", {}).get("settle_seconds", 30)) if engine.threefs else 0.0
    now = engine.clock()

    def ready(record: Mapping[str, Any]) -> bool:
        window = record.get("analysis_window")
        end = finite(window.get("end")) if isinstance(window, Mapping) else None
        return end is not None and now - end >= settle

    pending = [record for record in unseen if ready(record)]
    if pending:
        batch = pending[:max_records] if max_records is not None else pending
        for record in batch:
            prior = [item for item in history if item.get("observed_at", 0) < record.get("observed_at", 0)]
            write_report(output, engine.analyze(record, prior))
    elif periodic_when_idle and not unseen:
        write_report(output, engine.analyze(None, history))
    return len(batch) if pending else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--pending-only", action="store_true")
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
        )
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
