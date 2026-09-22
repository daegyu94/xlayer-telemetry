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


DEFAULT_QUERIES = {
    "gpu_utilization_percent": 'telemetry_gpu_utilization_percent{nodename="{node}"}',
    "host_memory_available_ratio": 'node_memory_MemAvailable_bytes{instance="{node}"} / node_memory_MemTotal_bytes{instance="{node}"}',
    "disk_busy_ratio": 'rate(node_disk_io_time_seconds_total{instance="{node}"}[1m])',
    "vllm_requests_waiting": 'vllm:num_requests_waiting{node="{node}"}',
    "vllm_kv_cache_usage": 'vllm:kv_cache_usage_perc{node="{node}"}',
    "vllm_preemptions_total": 'vllm:num_preemptions_total{node="{node}"}',
    "ray_pending_tasks": 'ray_tasks{State=~"PENDING.*"}',
    "policy_version_lag": 'policy_version_lag{nodename="{node}",run_id="{run_id}"}',
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
        params = urlencode({"query": query, "start": start, "end": end, "step": step})
        request = Request(self.url.rstrip("/") + "/api/v1/query_range?" + params)
        payload = _read_json(request, self.timeout)
        if payload.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {payload.get('error', 'unknown error')}")
        return _series_stats(payload.get("data", {}).get("result", []))


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
            "SELECT metricName, sum(count) AS count, "
            "if(sum(count)=0,0,sum(mean*count)/sum(count)) AS weighted_mean, "
            "max(max) AS max, max(p99) AS max_observed_p99 "
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
            return [json.loads(line) for line in response if line.strip()]


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
        slow = _slow_stages(current or {}, history, self.thresholds)
        evidence: dict[str, Any] = {"slow_stages": slow}
        missing: list[str] = []
        queries = {**DEFAULT_QUERIES, **self.config["prometheus"].get("queries", {})}
        step = max(1.0, float(self.config["prometheus"].get("query_step_seconds", 2)))
        for name, template in queries.items():
            try:
                query = str(template).replace("{node}", _escape_prometheus(node)).replace("{run_id}", _escape_prometheus(run_id))
                stats = self.prometheus.query_range(query, float(start), end, step)
                if stats is None:
                    missing.append("prometheus:" + name)
                else:
                    evidence[name] = stats
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                missing.append(f"prometheus:{name}:{type(exc).__name__}")

        threefs_rows: list[dict[str, Any]] = []
        threefs_baseline: list[dict[str, Any]] = []
        if self.threefs is not None:
            try:
                duration = end - float(start)
                threefs_rows = self.threefs.query_window(float(start), end)
                threefs_baseline = self.threefs.query_window(float(start) - duration, float(start))
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
        external_count = len(evidence) - 1
        verdict = "bottleneck_suspected" if findings else "no_anomaly_observed"
        if external_count == 0:
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
        }

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


def run_once(
    engine: DiagnosticEngine,
    history_path: Path,
    output: Path,
    *,
    periodic_when_idle: bool = True,
) -> int:
    history = load_history(history_path)
    seen = _existing_ids(output / "diagnostics.jsonl")
    pending = [record for record in history if record.get("record_id") not in seen]
    if pending:
        for record in pending[-20:]:
            prior = [item for item in history if item.get("observed_at", 0) < record.get("observed_at", 0)]
            write_report(output, engine.analyze(record, prior))
    elif periodic_when_idle:
        write_report(output, engine.analyze(None, history))
    return len(pending)


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
        )
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
