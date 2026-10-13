"""Pure signal extraction and legacy findings with explicit source identity."""
from __future__ import annotations

import re
from typing import Any, Mapping

from .diagnosis_analysis import finite
from .metric_queries import SIGNAL_CATALOG, SIGNAL_REDUCERS
from .storage_series import distribution_comparison_quality

_LATENCY_NAME = re.compile(r"latency|duration|elapsed|cost|time", re.IGNORECASE)
_DISTRIBUTION_LABELS = ("host", "tag", "mount_name", "instance", "io", "uid",
                        "method", "pod", "thread", "statusCode")

def _distribution_identity(row: Mapping[str, Any]) -> tuple | None:
    """Legacy unlabeled rows match only legacy rows, never explicit entities."""
    name = row.get("metricName")
    if not isinstance(name, str):
        return None
    if "labels" not in row:
        if any(key in row for key in _DISTRIBUTION_LABELS):
            # Do not turn a partially annotated custom/legacy row into the
            # metric-only compatibility identity.
            return None
        return (("metricName", name),)
    labels = row["labels"]
    if (not isinstance(labels, Mapping) or set(labels) != set(_DISTRIBUTION_LABELS)
            or any(not isinstance(value, str) for value in labels.values())):
        return None
    return (("metricName", name), *((key, labels[key]) for key in _DISTRIBUTION_LABELS))

def _distribution_index(rows: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    """Reject duplicate identities rather than choosing a last response row."""
    result, ambiguous = {}, set()
    for row in rows:
        key = _distribution_identity(row)
        count = finite(row.get("count"))
        if key is None or count is None or count <= 0:
            continue
        if key in result or key in ambiguous:
            result.pop(key, None)
            ambiguous.add(key)
        else:
            result[key] = row
    return result

def signal_values(
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
            "actor_update_duration_seconds": ("update_actor",),
            "critic_update_duration_seconds": ("update_critic",),
            "checkpoint_duration_seconds": ("save_checkpoint",),
            "communication_duration_seconds": ("weight_sync", "all_reduce", "collective"),
        }.items():
            values = [finite(stages.get(name)) for name in names]
            if signal == "rollout_duration_seconds" and values[0] is None:
                values.append(finite(stages.get("gen")))
            if signal == "communication_duration_seconds" and finite(stages.get("weight_sync")) is None:
                values.append(finite(stages.get("update_weights")))
            if any(value is not None for value in values):
                signals[signal] = sum(value or 0 for value in values)
    fields = SIGNAL_REDUCERS
    for name, field in fields.items():
        stats = metrics.get(name)
        if isinstance(stats, Mapping):
            value = finite(stats.get(field))
            if value is not None:
                target = SIGNAL_CATALOG[name].signal or name
                signals[target] = value / 100 if name in {"vllm_kv_cache_usage", "gpu_memory_usage_ratio"} and value > 1 else value
    latencies = [finite(row.get("max_observed_p99")) for row in _distribution_index(threefs_rows).values()
                 if _LATENCY_NAME.search(row["metricName"])]
    latencies = [value for value in latencies if value is not None]
    if len(latencies) == 1:
        signals["threefs_p99_latency"] = latencies[0]
    return signals

def legacy_findings(
    thresholds: Mapping[str, float],
    evidence: Mapping[str, Any],
    threefs_rows: list[dict[str, Any]],
    threefs_baseline: list[dict[str, Any]],
    start: float,
    end: float,
    execution_mode: str,
    *, baseline_window: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    slow_names = {item["stage"] for item in evidence.get("slow_stages", [])}
    waiting = finite(evidence.get("vllm_requests_waiting", {}).get("max"))
    kv = finite(evidence.get("vllm_kv_cache_usage", {}).get("max"))
    kv_ratio = kv / 100 if kv is not None and kv > 1 else kv
    preemptions = finite(evidence.get("vllm_preemptions_total", {}).get("max_series_delta"))
    if (waiting is not None and waiting >= thresholds["vllm_waiting"] or
            kv_ratio is not None and kv_ratio >= thresholds["vllm_kv_usage"] or
            preemptions is not None and preemptions > 0):
        finding = {"component": "vllm", "candidate": "rollout_capacity_or_kv_pressure", "signals": {"waiting_max": waiting, "kv_usage_max_ratio": kv_ratio, "preemptions_delta": preemptions}}
        if execution_mode == "async":
            finding["attribution"] = "continuous_window"
        else:
            finding["correlates_with_slow_stage"] = "gen" in slow_names
        findings.append(finding)
    pending = finite(evidence.get("ray_pending_tasks", {}).get("max"))
    if pending is not None and pending >= thresholds["ray_pending_tasks"]:
        findings.append({"component": "ray", "candidate": "scheduling_backlog", "observation_scope": "cluster/session", "signals": {"pending_tasks_max": pending}})
    lag = finite(evidence.get("policy_version_lag", {}).get("max"))
    if lag is not None and lag >= thresholds["policy_version_lag"]:
        findings.append({"component": "verl_async", "candidate": "policy_version_lag", "signals": {"version_lag_max": lag}})
    memory = finite(evidence.get("host_memory_available_ratio", {}).get("min"))
    disk = finite(evidence.get("disk_busy_ratio", {}).get("max"))
    gpu = finite(evidence.get("gpu_utilization_percent", {}).get("mean"))
    if (memory is not None and memory <= thresholds["memory_available_ratio"] or
            disk is not None and disk >= thresholds["disk_busy_ratio"]):
        findings.append({"component": "host", "candidate": "memory_or_storage_pressure", "signals": {"memory_available_min_ratio": memory, "disk_busy_max_ratio": disk, "gpu_utilization_mean_percent": gpu}, "correlates_with_slow_stage": bool(slow_names)})
    latency_rows = {identity: row for identity, row in _distribution_index(threefs_rows).items()
                    if _LATENCY_NAME.search(row["metricName"])}
    baseline_by_identity = _distribution_index(threefs_baseline)
    elevated = []
    for identity, row in latency_rows.items():
        baseline = baseline_by_identity.get(identity, {})
        counts = [finite(item.get("count")) for item in (row, baseline)]
        current_p99 = finite(row.get("max_observed_p99"))
        baseline_p99 = finite(baseline.get("max_observed_p99"))
        # Distribution cells without sampled operations are not latency
        # observations, even when the backend returns a retained p99 value.
        if (any(count is None or count <= 0 for count in counts)
                or current_p99 is None or baseline_p99 is None or baseline_p99 <= 0):
            continue
        exposure = distribution_comparison_quality([row], [baseline], {'start': start, 'end': end},
            baseline_window or {'start': start - (end - start), 'end': start})
        if exposure['status'] != 'shared_report_window':
            continue
        ratio = current_p99 / baseline_p99
        if ratio >= thresholds["threefs_latency_slowdown_ratio"]:
            elevated.append({**row, "baseline_max_observed_p99": baseline_p99, "ratio": ratio})
    if elevated:
        findings.append({"component": "3fs", "candidate": "latency_regression", "signals": {"metrics": elevated, "window_seconds": end - start}, "attribution": "shared_storage_window"})
    return findings
