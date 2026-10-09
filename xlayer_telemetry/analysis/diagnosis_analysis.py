"""Workload-aware comparisons and inspectable, conservative diagnosis rules.

Inputs are measurements, never ownership claims. A shared-service observation
remains shared even when it overlaps a run's step interval.
"""

from __future__ import annotations

from ..time_alignment import observation_time

import heapq
import statistics
from typing import Any, Iterable, Mapping

from ..measurements import finite_number as finite
from .metric_queries import PROFILE_SIGNALS


SIGNAL_SCOPE = {
    "step_duration_seconds": "application",
    "rollout_duration_seconds": "application",
    "communication_duration_seconds": "application",
    "actor_update_duration_seconds": "application",
    "critic_update_duration_seconds": "application",
    "checkpoint_duration_seconds": "application",
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
    "tool_duration_seconds": "application",
    "sandbox_io_pressure_ratio": "cgroup",
    "sandbox_device_busy_ratio": "device",
}
SIGNAL_SCOPE.update({name: spec.scope for name, spec in PROFILE_SIGNALS.items()})
BASELINE_REQUIRED = {
    "vllm_queue_p95_seconds",
    "actor_update_duration_seconds", "critic_update_duration_seconds", "checkpoint_duration_seconds",
    "step_duration_seconds", "rollout_duration_seconds",
    "communication_duration_seconds", "gpu_utilization_percent",
    "rdma_bytes_per_second", "threefs_p99_latency",
    "threefs_throughput_bytes_per_second",
    "tool_duration_seconds",
    "mooncake_dfs_read_p95_seconds", "mooncake_dfs_write_p95_seconds",
}
BASELINE_IDENTITY = (
    "run_id", "node", "worker_id", "boundary_scope", "execution_mode",
    "cluster", "producer", "role", "rank", "local_rank", "gpu",
)
_POLICY_IDENTIFIERS = {"policy_version", "fully_async/count/current_param_version"}
_APPLICATION_DURATION_METADATA = {
    name: {"unit": "s", "window_statistic": "sum of reported stages" if name == "communication_duration_seconds" else "reported completed duration"}
    for name in ("step_duration_seconds", "rollout_duration_seconds", "actor_update_duration_seconds",
                 "critic_update_duration_seconds", "checkpoint_duration_seconds", "communication_duration_seconds")
}

VLLM_OBSERVATIONS = ('vllm_requests_waiting', 'vllm_kv_cache_usage', 'vllm_preemptions_total')
_VLLM_IDENTITY = ('cluster', 'node', 'instance', 'component', 'replica', 'replica_rank', 'node_rank', 'engine', 'engine_id', 'model_name', 'model')
_VLLM_ENGINE_FIELDS = frozenset({'instance', 'component', 'engine', 'engine_id', 'model_name', 'model'})


def vllm_identity(item: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    labels = item.get('labels', {})
    return tuple((key, str(labels[key])) for key in _VLLM_IDENTITY if key in labels)


def validate_run_engine_instances(config):
    instances = config.get('run_engine_instances', [])
    if (not isinstance(instances, list) or len(instances) > 16
            or any(not isinstance(value, str) or not value.strip() or len(value) > 256 for value in instances)
            or len(set(instances)) != len(instances)):
        raise ValueError('run_engine_instances needs at most 16 unique endpoint identities')
    if instances and any(not isinstance(config.get(key), str) or not config[key].strip()
                         for key in ('run_id', 'cluster', 'rollout_node')):
        raise ValueError('run_engine_instances requires explicit run_id, cluster and rollout_node')


def resource_run_relation(evidence, config, run_id):
    """Endpoint configuration is a relation declaration, never I/O ownership."""
    validate_run_engine_instances(config)
    from .rollout_replicas import matches_replica
    replicas = config.get('rollout_replicas', [])
    if replicas and config.get('run_id') == run_id and evidence and all(
            item['signal'].startswith(('vllm_', 'mooncake_connector_')) for item in evidence):
        return 'configured' if all(any(matches_replica(item.get('labels', {}), row, config['cluster'])
                                      for row in replicas) for item in evidence) else 'unlinked'
    instances = config.get('run_engine_instances', [])
    if not instances or config.get('run_id') != run_id:
        return 'shared_unverified'
    if not evidence or any(not item['signal'].startswith(('vllm_', 'mooncake_connector_')) for item in evidence):
        return 'shared_unverified'
    if any(item.get('labels', {}).get('cluster') != config['cluster'] or
           item.get('labels', {}).get('node') != config['rollout_node'] for item in evidence):
        return 'shared_unverified'
    observed = [item.get('labels', {}).get('instance') for item in evidence]
    if any(not instance for instance in observed):
        return 'shared_unverified'
    return 'configured' if all(instance in instances for instance in observed) else 'unlinked'


def select_vllm_observations(series: Mapping[str, list[dict]], thresholds: Mapping[str, float]) -> dict[str, dict] | None:
    """Choose one coherent engine, retaining partial evidence on hot engines.

    None means conflicting/duplicate identity, not absent telemetry. Empty
    input stays empty; legacy singleton identities keep their existing meaning.
    """
    indexed = {}
    for name in VLLM_OBSERVATIONS:
        items = series.get(name, [])
        if not items:
            continue
        index = {}
        for item in items:
            identity = vllm_identity(item)
            if identity in index:
                return None
            index[identity] = item
        indexed[name] = index
    if not indexed:
        return {}
    populations = [set(index) for index in indexed.values()]
    population = set.union(*populations)
    shared = set.intersection(*populations)
    # Multiple unlabeled/disjoint single-metric entities cannot establish a
    # coherent pressure pattern. Never fill a missing value from another engine.
    eligible = {identity for identity in population if identity in shared or
                sum(identity in index for index in indexed.values()) >= 2}
    if not eligible or (len(population) > 1 and any(
            not any(key in _VLLM_ENGINE_FIELDS for key, _ in identity) for identity in eligible)):
        return None

    def score(identity):
        stats = {name: index[identity]['stats'] for name, index in indexed.items() if identity in index}
        kv = stats.get('vllm_kv_cache_usage', {}).get('max') or 0
        kv = kv / 100 if kv > 1 else kv
        waiting = stats.get('vllm_requests_waiting', {}).get('max') or 0
        preemptions = stats.get('vllm_preemptions_total', {}).get('max_series_delta') or 0
        return (int(kv >= thresholds['vllm_kv_usage']) + int(waiting >= thresholds['vllm_waiting'])
                + int(preemptions >= 1), kv)

    selected = max(sorted(eligible), key=score)
    return {name: index[selected] for name, index in indexed.items() if selected in index}


def validate_baseline_policy(policy: Mapping[str, Any]) -> None:
    if not isinstance(policy, Mapping):
        raise ValueError("baseline must be an object")
    fields = policy.get("match_fields", [])
    if (not isinstance(fields, list) or len(fields) > 16
            or any(not isinstance(key, str) or not key for key in fields)
            or len(fields) != len(set(fields))):
        raise ValueError("baseline.match_fields must contain at most 16 unique field names")
    tolerance = finite(policy.get("relative_tolerance", 0))
    if tolerance is None or not 0 <= tolerance <= 1:
        raise ValueError("baseline.relative_tolerance must be between 0 and 1")
    if policy.get("normalize_by") not in {None, "perf/total_num_tokens"}:
        raise ValueError("baseline.normalize_by only supports perf/total_num_tokens")


def workload_matches(current: Mapping[str, Any], prior: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
    for key in policy.get("match_fields", []):
        now, before = current.get("workload", {}).get(key), prior.get("workload", {}).get(key)
        if now is None or before is None:
            return False
        if key in _POLICY_IDENTIFIERS:
            # A version is an identity, not a continuous workload magnitude.
            if type(now) is bool or type(before) is bool or finite(now) is None or finite(before) is None or now != before:
                return False
            continue
        a, b = finite(now), finite(before)
        if a is not None and b is not None:
            if abs(a-b) > abs(b) * policy.get("relative_tolerance", 0):
                return False
        elif type(now) is not type(before) or now != before:
            return False
    return True


def recent_baseline_history(current: Mapping[str, Any], history: Iterable[Mapping[str, Any]], *, policy: Mapping[str, Any] | None = None) -> list[Mapping[str, Any]]:
    """Keep five comparable prior observations in one bounded-memory scan.

    Old artifacts with absent identity fields remain compatible with each other;
    an absent field never substitutes for an explicitly observed identity.
    Step and stage comparisons must consume this same cohort.
    """
    policy = policy or {}
    validate_baseline_policy(policy)
    observed = observation_time(current)
    if observed is None:
        return []

    def eligible():
        for item in history:
            if (any(item.get(key) != current.get(key) for key in BASELINE_IDENTITY)
                    or not workload_matches(current, item, policy)):
                continue
            duration = finite(item.get("step_duration_seconds"))
            window = item.get("analysis_window")
            if duration is None or duration < 0 or not isinstance(window, Mapping):
                continue
            start, end = finite(window.get("start")), finite(window.get("end"))
            if start is None or end is None or start >= end or window.get("accuracy") in {"unknown", "clock_discontinuity"}:
                continue
            stamp = observation_time(item)
            if stamp is not None and stamp < observed:
                yield stamp, item

    return [item for _, item in heapq.nlargest(5, eligible(), key=lambda pair: pair[0])]


def select_baseline(current: Mapping[str, Any], history: Iterable[Mapping[str, Any]], *, policy: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Use a recent, same-run peer nearest to the prior step-duration median."""
    eligible = recent_baseline_history(current, history, policy=policy)
    if not eligible:
        return None
    median = statistics.median(float(item["step_duration_seconds"]) for item in eligible)
    return min(eligible, key=lambda item: abs(float(item["step_duration_seconds"]) - median))


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
            **_APPLICATION_DURATION_METADATA.get(name, {}),
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

    def add(identifier: str, component: str, summary: str, checks: list[tuple[str, bool]], *, scope: str, related: Mapping[str, Any] | None = None, cap_state: str | None = None, unmatched: set[str] | None = None) -> None:
        unlinked=({'network_utilization_ratio'} if identifier=='device_limited_storage'
                  else {'storage_device_busy_ratio'} if identifier=='network_limited_storage' else set())
        excluded = unlinked | (unmatched or set())
        observed = [name for name, ok in checks if ok and name not in excluded]
        if not observed:
            return
        required = [name for name, _ in checks]
        missing = [name for name in required if val(name) is None]
        missing.extend(f'{name}:storage_path_unverified' for name in sorted(unlinked))
        missing.extend(f'{name}:entity_mismatch' for name in sorted(unmatched or set()))
        missing.extend(f"baseline:{name}" for name in required if name in BASELINE_REQUIRED and val(name, previous=True) is None)
        contrary = [name for name, ok in checks if name not in excluded and not ok and val(name) is not None and (name not in BASELINE_REQUIRED or val(name, previous=True) is not None)]
        # A single symptom is weak; two independent supporting signals are
        # supporting; all requirements must be present and true for strong.
        state = "strong_signal" if len(observed) == len(required) and not missing else (
            "supporting_signal" if len(observed) >= 2 else "weak_signal"
        )
        if cap_state == "supporting_signal" and state == "strong_signal":
            state = "supporting_signal"
        common_storage = identifier.startswith('mooncake_dfs_')
        if common_storage:
            missing.extend(['storage_backend_identity_unverified', 'storage_operation_attribution_unverified',
                            'dfs_latency_observes_batches_with_delivered_io_only'])
            for name in observed:
                if name.startswith('mooncake_') and not (labels_for(name).get('node') and labels_for(name).get('instance')):
                    missing.append(name + ':entity_unverified')
                    state = 'weak_signal'
        elif component == "storage" and finite(context.get("per_run_storage_bytes")) is None:
            missing.append("per_run_3fs_client_bytes")
        if component == 'storage' and scope == 'mixed' and not common_storage:
            # Shared 3FS reports do not identify which SSD/interface served it.
            missing.append('storage_service_resource_relation_unverified')
            if state=='strong_signal':
                state='supporting_signal'
            summary = ('Storage latency and device pressure coincide; network-path headroom is unverified'
                       if identifier=='device_limited_storage' else
                       'Storage latency and interface pressure coincide; storage-device headroom is unverified'
                       if identifier=='network_limited_storage' else
                       summary+'; resource observations are concurrent context, not a verified storage path')
        evidence = [{
            "signal": name,
            **_APPLICATION_DURATION_METADATA.get(name, {}),
            "value": val(name),
            "baseline": val(name, previous=True),
            "observation_scope": scope_for(name),
            "labels": labels_for(name),
            "source": context.get("sources", {}).get(name, "workload"),
            "query": context.get("queries", {}).get(name),
            "window": context.get("signal_windows", {}).get(name, context.get("window")),
            "boundary_accuracy": context.get("signal_boundary_accuracy", {}).get(name, context.get("boundary_accuracy", "unknown")),
        } for name in observed]
        gpu_signal = "gpu_memory_usage_ratio" if identifier == "gpu_memory_pressure" else "gpu_utilization_percent"
        gpu_label = labels_for(gpu_signal).get("gpu") or labels_for(gpu_signal).get("gpu_uuid")
        observed_nodes = sorted({
            str(item["labels"].get("node") or item["labels"].get("nodename"))
            for item in evidence if item["labels"].get("node") or item["labels"].get("nodename")
        })
        fallback_node = (context.get("compute_node") or context.get("node")) if component == "compute" else (
            context.get("rollout_node") if component == "rollout" else context.get("node") if component == "host" else None)
        related_devices = list((related or {}).get("devices", []))
        if not related_devices and component == "compute" and gpu_label is not None:
            related_devices = [gpu_label]
        candidates.append({
            "id": identifier, "component": component, "summary": summary,
            "state": state, "evidence": evidence,
            "counter_evidence": [{"signal": name, **_APPLICATION_DURATION_METADATA.get(name, {}), "value": val(name), "observation_scope": scope_for(name), "labels": labels_for(name)} for name in contrary],
            "missing_evidence": missing,
            "observation_scope": scope,
            "related_nodes": sorted(set((related or {}).get("nodes", [])) | set(observed_nodes)) or ([fallback_node] if fallback_node else []),
            "related_devices": related_devices,
            "related_spans": list((related or {}).get("spans", [])) or list(context.get("related_spans", [])),
        })

    slow = raised("step_duration_seconds", thresholds.get("step_slowdown_ratio", 1.5))
    # DFS latency/failed-key observations are client-local, backend independent.
    # A batch with no delivered I/O has no latency sample upstream. Failed keys
    # are useful on their own, but cannot be divided by successful keys: checksum
    # failures may also contribute delivered bytes/keys. Never infer a 3FS path.
    if slow:
        identity_keys = ('cluster', 'node', 'instance', 'component', 'client_mode', 'cluster_id')
        for direction in ('read', 'write'):
            latency = f'mooncake_dfs_{direction}_p95_seconds'
            errors = f'mooncake_dfs_{direction}_errors_per_second'
            regressed = raised(latency, thresholds.get('mooncake_dfs_latency_slowdown_ratio', 2))
            failed = val(errors) is not None and val(errors) > 0
            if not (regressed or failed):
                continue
            anchor = latency if regressed else errors
            other = errors if regressed else latency
            a, b = labels_for(anchor), labels_for(other)
            matched = bool(a.get('node') and a.get('instance') and b.get('node') and b.get('instance')) and all(a.get(key) == b.get(key) for key in identity_keys)
            unmatched = {other} if val(other) is not None and not matched else set()
            add(f'mooncake_dfs_{direction}_pressure', 'storage',
                f'Slow step overlaps Mooncake DFS {direction} latency or failed-key observations; backend path is unverified',
                [('step_duration_seconds', True), (latency, regressed), (errors, failed)],
                scope='mixed', cap_state='supporting_signal', unmatched=unmatched)
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
        ], scope=scope_for("gpu_memory_usage_ratio"))
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
    # PSI directly measures stalls, unlike busy percentage. These mixed-scope
    # overlaps justify inspection, but cannot establish the workload owns them.
    if slow:
        for resource in ("cpu", "memory", "io"):
            name = f"host_{resource}_pressure_ratio"
            if high(name, thresholds.get("host_pressure_ratio", 0.2)):
                add(f"host_{resource}_stalls", "host", f"Slow step overlaps measured host {resource.upper()} stalls", [
                    ("step_duration_seconds", slow),
                    (name, True),
                ], scope="mixed", cap_state="supporting_signal")
        if high("ray_mmap_disk_bytes", 1):
            add("ray_object_store_disk_pressure", "ray", "Slow step overlaps Ray objects using disk-backed mmap", [
                ("step_duration_seconds", slow), ("ray_mmap_disk_bytes", True),
            ], scope="mixed", cap_state="supporting_signal")
    for stage, resource in (("actor_update", "cpu"), ("critic_update", "cpu"), ("checkpoint", "io")):
        duration_name = f"{stage}_duration_seconds"
        pressure_name = f"host_{resource}_pressure_ratio"
        if raised(duration_name, thresholds.get("step_slowdown_ratio", 1.5)) and high(pressure_name, thresholds.get("host_pressure_ratio", 0.2)):
            add(f"{stage}_{resource}_stalls", "host", f"Slower {stage.replace('_', ' ')} overlaps step-window host {resource.upper()} stalls", [
                (duration_name, True), (pressure_name, True),
            ], scope="mixed", cap_state="supporting_signal")
    if raised("rollout_duration_seconds", thresholds.get("step_slowdown_ratio", 1.5)) and raised("vllm_queue_p95_seconds", thresholds.get("step_slowdown_ratio", 1.5)):
        add("rollout_queue_latency", "rollout", "Longer rollout overlaps increased vLLM queue latency", [
            ("rollout_duration_seconds", True), ("vllm_queue_p95_seconds", True),
        ], scope="mixed", cap_state="supporting_signal")
    # A cgroup and a device can overlap the tool interval without proving
    # ownership of that device's load. Never promote this candidate to strong.
    if raised("tool_duration_seconds", thresholds.get("tool_slowdown_ratio", 1.5)):
        add("sandbox_local_storage_pressure", "sandbox", "Slow tool execution overlaps sandbox I/O pressure and local device activity", [
            ("tool_duration_seconds", True),
            ("sandbox_io_pressure_ratio", high("sandbox_io_pressure_ratio", thresholds.get("sandbox_io_pressure_ratio", 0.2))),
            ("sandbox_device_busy_ratio", high("sandbox_device_busy_ratio", thresholds.get("sandbox_device_busy_ratio", 0.9))),
        ], scope="mixed", related={"nodes": [context["sandbox_node"]] if context.get("sandbox_node") else [],
                            "devices": [context["sandbox_device"]] if context.get("sandbox_device") else [],
                            "spans": list(context.get("tool_related_spans", []))},
            cap_state="supporting_signal")
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
                        "window": context.get("window"),
                        "boundary_accuracy": context.get("boundary_accuracy", "unknown"),
                    }],
                    "counter_evidence": [], "missing_evidence": [], "observation_scope": "worker",
                    "related_nodes": [], "related_devices": [], "related_spans": [],
                })
    return candidates
