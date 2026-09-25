"""Workload-aware comparisons and inspectable, conservative diagnosis rules.

Inputs are measurements, never ownership claims. A shared-service observation
remains shared even when it overlaps a run's step interval.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Mapping


SIGNAL_SCOPE = {
    "step_duration_seconds": "application",
    "rollout_duration_seconds": "application",
    "communication_duration_seconds": "application",
    "gpu_utilization_percent": "device",
    "gpu_memory_usage_ratio": "device",
    "gpu_evictions_delta": "process",
    "host_memory_available_ratio": "node",
    "host_swap_activity": "node",
    "disk_busy_ratio": "device",
    "storage_device_busy_ratio": "device",
    "disk_read_bytes_per_second": "node",
    "storage_request_bytes": "shared-service",
    "threefs_p99_latency": "shared-service",
    "threefs_throughput_bytes_per_second": "shared-service",
    "network_utilization_ratio": "network-interface",
    "rdma_bytes_per_second": "network-interface",
    "vllm_requests_waiting": "service",
    "vllm_kv_cache_usage": "service",
    "vllm_preemptions_delta": "service",
}
BASELINE_REQUIRED = {
    "step_duration_seconds", "rollout_duration_seconds",
    "communication_duration_seconds", "gpu_utilization_percent",
    "rdma_bytes_per_second", "threefs_p99_latency",
    "threefs_throughput_bytes_per_second",
}


def finite(value: Any) -> float | None:
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return float(value)


def select_baseline(current: Mapping[str, Any], history: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Use a recent, same-run peer nearest to the prior step-duration median."""
    eligible = [
        item for item in history
        if item.get("run_id") == current.get("run_id")
        and item.get("worker_id") == current.get("worker_id")
        and item.get("boundary_scope") == current.get("boundary_scope")
        and finite(item.get("step_duration_seconds")) is not None
        and finite(item.get("analysis_window", {}).get("start")) is not None
        and finite(item.get("analysis_window", {}).get("end")) is not None
        and finite(item.get("observed_at")) is not None
        and float(item["observed_at"]) < float(current.get("observed_at") or float("inf"))
    ][-5:]
    if not eligible:
        return None
    median = statistics.median(float(item["step_duration_seconds"]) for item in eligible)
    return min(reversed(eligible), key=lambda item: abs(float(item["step_duration_seconds"]) - median))


def compare_signals(
    current: Mapping[str, Any], baseline: Mapping[str, Any], *,
    scopes: Mapping[str, str] | None = None,
    labels: Mapping[str, Mapping[str, str]] | None = None,
) -> list[dict[str, Any]]:
    rows = []
    for name, value in current.items():
        now, before = finite(value), finite(baseline.get(name))
        if now is None:
            continue
        change = now - before if before is not None else None
        rows.append({
            "signal": name,
            "scope": (scopes or {}).get(name, SIGNAL_SCOPE.get(name, "unknown")),
            "labels": dict((labels or {}).get(name, {})),
            "current": now,
            "baseline": before,
            "delta": change,
            "delta_percent": 100 * change / abs(before) if change is not None and before else None,
        })
    return sorted(rows, key=lambda row: abs(row["delta_percent"] or 0), reverse=True)


def evaluate_rules(
    current: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    thresholds: Mapping[str, float],
    context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return only observed candidates; missing requirements cap the state.

    A rule can have an observed symptom with incomplete supporting sources, but
    it can never become strong without every required positive observation.
    """
    candidates: list[dict[str, Any]] = []

    def scope_for(name: str) -> str:
        return context.get("signal_scopes", {}).get(name, SIGNAL_SCOPE.get(name, "unknown"))

    def labels_for(name: str) -> dict[str, str]:
        return dict(context.get("signal_labels", {}).get(name, {}))

    def val(name: str, *, previous: bool = False) -> float | None:
        return finite((baseline if previous else current).get(name))

    def high(name: str, minimum: float) -> bool:
        value = val(name)
        return value is not None and value >= minimum

    def low(name: str, maximum: float) -> bool:
        value = val(name)
        return value is not None and value <= maximum

    def raised(name: str, ratio: float) -> bool:
        value, before = val(name), val(name, previous=True)
        return value is not None and before is not None and before > 0 and value >= before * ratio

    def dropped(name: str, amount: float) -> bool:
        value, before = val(name), val(name, previous=True)
        return value is not None and before is not None and before - value >= amount

    def add(identifier: str, component: str, summary: str, checks: list[tuple[str, bool]], *, scope: str, related: Mapping[str, Any] | None = None) -> None:
        observed = [name for name, ok in checks if ok]
        if not observed:
            return
        required = [name for name, _ in checks]
        missing = [name for name in required if val(name) is None]
        missing.extend(f"baseline:{name}" for name in required if name in BASELINE_REQUIRED and val(name, previous=True) is None)
        contrary = [name for name, ok in checks if not ok and val(name) is not None and (name not in BASELINE_REQUIRED or val(name, previous=True) is not None)]
        # A single symptom is weak; two independent supporting signals are
        # supporting; all requirements must be present and true for strong.
        state = "strong_signal" if len(observed) == len(required) and not missing else (
            "supporting_signal" if len(observed) >= 2 else "weak_signal"
        )
        if component == "storage" and finite(context.get("per_run_storage_bytes")) is None:
            missing.append("per_run_3fs_client_bytes")
        evidence = [{
            "signal": name,
            "value": val(name),
            "baseline": val(name, previous=True),
            "observation_scope": scope_for(name),
            "labels": labels_for(name),
            "source": context.get("sources", {}).get(name, "workload"),
            "query": context.get("queries", {}).get(name),
            "window": context.get("window"),
            "boundary_accuracy": context.get("boundary_accuracy", "unknown"),
        } for name in observed]
        gpu_label = labels_for("gpu_utilization_percent").get("gpu")
        related_devices = list((related or {}).get("devices", []))
        if not related_devices and component == "compute" and gpu_label is not None:
            related_devices = [gpu_label]
        candidates.append({
            "id": identifier, "component": component, "summary": summary,
            "state": state, "evidence": evidence,
            "counter_evidence": [{"signal": name, "value": val(name), "observation_scope": scope_for(name), "labels": labels_for(name)} for name in contrary],
            "missing_evidence": missing,
            "observation_scope": scope,
            "related_nodes": list((related or {}).get("nodes", [])) or ([context["node"]] if component in {"compute", "host"} and context.get("node") else []),
            "related_devices": related_devices,
            "related_spans": list((related or {}).get("spans", [])) or list(context.get("related_spans", [])),
        })

    slow = raised("step_duration_seconds", thresholds.get("step_slowdown_ratio", 1.5))
    storage = raised("threefs_p99_latency", thresholds.get("threefs_latency_slowdown_ratio", 2))
    disk = high("storage_device_busy_ratio", thresholds.get("disk_busy_ratio", 0.9))
    network = high("network_utilization_ratio", thresholds.get("network_utilization_ratio", 0.8))
    throughput = val("threefs_throughput_bytes_per_second")
    old_throughput = val("threefs_throughput_bytes_per_second", previous=True)
    plateau = throughput is not None and old_throughput is not None and old_throughput > 0 and throughput <= old_throughput * 1.2
    if storage and disk:
        add("storage_queue_saturation", "storage", "Storage latency and device queue pressure overlap this interval", [
            ("threefs_p99_latency", storage), ("storage_device_busy_ratio", disk),
            ("threefs_throughput_bytes_per_second", plateau),
        ], scope="mixed")
        add("device_limited_storage", "storage", "Storage latency with device pressure and measured network headroom", [
            ("threefs_p99_latency", storage), ("storage_device_busy_ratio", disk),
            ("network_utilization_ratio", val("network_utilization_ratio") is not None and not network),
        ], scope="mixed")
    if storage and network:
        add("network_limited_storage", "storage", "Storage latency with network pressure and measured device headroom", [
            ("threefs_p99_latency", storage), ("network_utilization_ratio", network),
            ("storage_device_busy_ratio", val("storage_device_busy_ratio") is not None and not disk),
        ], scope="mixed")
    # Request-size evidence is optional. Never infer small I/O from IOPS alone.
    if storage and val("storage_request_bytes") is not None:
        add("small_io_pressure", "storage", "Small requests accompany storage latency", [
            ("storage_request_bytes", low("storage_request_bytes", thresholds.get("small_io_bytes", 4096))),
            ("threefs_p99_latency", storage),
        ], scope="shared-service")
    if slow:
        add("gpu_starvation", "compute", "GPU idle time accompanies a slow step and upstream waiting", [
        ("step_duration_seconds", slow),
        ("gpu_utilization_percent", dropped("gpu_utilization_percent", thresholds.get("gpu_utilization_drop_points", 20))),
        ("vllm_requests_waiting", high("vllm_requests_waiting", thresholds.get("vllm_waiting", 1))),
        ], scope="mixed")
    if high("gpu_memory_usage_ratio", thresholds.get("gpu_memory_ratio", 0.9)):
        add("gpu_memory_pressure", "compute", "GPU memory use and eviction coincide", [
        ("gpu_memory_usage_ratio", high("gpu_memory_usage_ratio", thresholds.get("gpu_memory_ratio", 0.9))),
        ("gpu_evictions_delta", high("gpu_evictions_delta", 1)),
        ], scope="device")
    if raised("rollout_duration_seconds", thresholds.get("step_slowdown_ratio", 1.5)):
        add("rollout_queue_backlog", "rollout", "Rollout duration and vLLM queue increased", [
        ("rollout_duration_seconds", raised("rollout_duration_seconds", thresholds.get("step_slowdown_ratio", 1.5))),
        ("vllm_requests_waiting", high("vllm_requests_waiting", thresholds.get("vllm_waiting", 1))),
        ], scope="mixed")
    if high("vllm_kv_cache_usage", thresholds.get("vllm_kv_usage", 0.9)):
        add("kv_cache_pressure", "rollout", "KV use, preemptions, and waiting requests increased", [
        ("vllm_kv_cache_usage", high("vllm_kv_cache_usage", thresholds.get("vllm_kv_usage", 0.9))),
        ("vllm_preemptions_delta", high("vllm_preemptions_delta", 1)),
        ("vllm_requests_waiting", high("vllm_requests_waiting", thresholds.get("vllm_waiting", 1))),
        ], scope="service")
    if raised("communication_duration_seconds", thresholds.get("step_slowdown_ratio", 1.5)):
        add("communication_bound", "network", "Communication duration and RDMA activity increased as GPU use fell", [
        ("communication_duration_seconds", raised("communication_duration_seconds", thresholds.get("step_slowdown_ratio", 1.5))),
        ("rdma_bytes_per_second", raised("rdma_bytes_per_second", thresholds.get("rdma_growth_ratio", 1.5))),
        ("gpu_utilization_percent", dropped("gpu_utilization_percent", thresholds.get("gpu_utilization_drop_points", 20))),
        ], scope="mixed")
    if low("host_memory_available_ratio", thresholds.get("memory_available_ratio", 0.1)):
        add("host_memory_pressure", "host", "Low available memory and paging activity coincide", [
        ("host_memory_available_ratio", low("host_memory_available_ratio", thresholds.get("memory_available_ratio", 0.1))),
        ("host_swap_activity", high("host_swap_activity", 1)),
        ], scope="node")
    peers = context.get("participant_durations_seconds", {})
    if isinstance(peers, Mapping) and len(peers) >= 3:
        numbers = {str(key): finite(value) for key, value in peers.items()}
        numbers = {key: value for key, value in numbers.items() if value is not None}
        if len(numbers) >= 3:
            slowest = max(numbers, key=numbers.get)
            others = [value for key, value in numbers.items() if key != slowest]
            median = statistics.median(others)
            spread = (max(others) - min(others)) / median if median else float("inf")
            if median > 0 and numbers[slowest] >= median * thresholds.get("straggler_ratio", 1.5) and spread <= thresholds.get("peer_spread_ratio", 0.2):
                candidates.append({
                    "id": "straggler", "component": "distributed_execution",
                    "summary": f"Participant {slowest} is slower than comparable peers",
                    "state": "strong_signal",
                    "evidence": [{
                        "signal": "participant_duration_seconds", "value": numbers[slowest],
                        "baseline": median, "observation_scope": "worker",
                        "source": "workload", "participant": slowest,
                    }],
                    "counter_evidence": [], "missing_evidence": [], "observation_scope": "worker",
                    "related_nodes": [], "related_devices": [], "related_spans": [],
                })
    return candidates
