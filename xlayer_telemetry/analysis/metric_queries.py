"""Opt-in, bounded diagnostic queries for existing upstream exporters.

Profiles add no collectors and never imply per-run ownership. Counter rates use
one-minute windows (which may extend before a short step); histogram quantiles
are per exporter entity, not a quantile of already-computed quantiles. Missing
collectors, idle denominators and unsupported fields remain missing, not zero.

Metric contracts: Node Exporter v1.9.1; vLLM 5f30fc7031ca; Ray
43b706d733c5; DCGM Exporter fafd15114805. See docs/diagnosis.md for pinned
source links and version-dependent fields.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class MetricQuery:
    expression: str
    scope: str
    unit: str
    statistic: str = "max"


# The existing Node Exporter scrape identifies nodes using instance. Native
# endpoints require an explicit source kind and the registered node label.
NODE = '{instance="{node}"}'
VLLM = '{telemetry_source="vllm",node="{rollout_node}"}'
RAY = '{telemetry_source="ray",node="{rollout_node}"}'
DCGM = '{telemetry_source="dcgm",node="{compute_node}"}'


def rate(metric: str, selector: str = NODE) -> str:
    return f"rate({metric}{selector}[1m])"


def fraction(numerator: str, denominator: str) -> str:
    # A comparison without bool filters idle/invalid denominator series and
    # retains the denominator value. clamp_min would invent an idle ratio.
    return f"({numerator}) / ({denominator} > 0)"


def dcgm_gauge(metric: str, maximum: str, *, inclusive: bool = False) -> str:
    selector = metric + DCGM
    return f"({selector} >= 0 {'<=' if inclusive else '<'} {maximum})"


def dcgm_counter_rate(metric: str) -> str:
    selector = metric + DCGM
    # DCGM's finite unsupported/error sentinels are around int64 max. Reject
    # the full rate window so a sentinel transition cannot look like retries.
    return f"{rate(metric, DCGM)} and (max_over_time({selector}[1m]) < 1e18) and (min_over_time({selector}[1m]) >= 0)"


def quantile(metric: str) -> str:
    return f"histogram_quantile(0.95, {rate(metric + '_bucket', VLLM)})"


def offload_rate(direction: str) -> str:
    legacy = VLLM[:-1] + ',transfer_type="' + ("CPU_to_GPU" if direction == "load" else "GPU_to_CPU") + '"}'
    # Prefer flat names when both generations are emitted; remove only the
    # deprecated direction label so endpoint/model/engine identity is retained.
    return '(' + rate(f"vllm:kv_offload_{direction}_bytes_total", VLLM) + ') or (sum without (transfer_type) (' + rate("vllm:kv_offload_total_bytes_total", legacy) + '))'


METRIC_PROFILES: dict[str, dict[str, MetricQuery]] = {
    "host": {
        "host_cpu_busy_ratio": MetricQuery('1 - avg without (cpu) (' + rate('node_cpu_seconds_total', '{instance="{node}",mode="idle"}') + ')', "node", "ratio"),
        "host_cpu_pressure_ratio": MetricQuery(rate("node_pressure_cpu_waiting_seconds_total"), "node", "ratio"),
        "host_memory_pressure_ratio": MetricQuery(rate("node_pressure_memory_waiting_seconds_total"), "node", "ratio"),
        "host_io_pressure_ratio": MetricQuery(rate("node_pressure_io_waiting_seconds_total"), "node", "ratio"),
        "host_major_faults_per_second": MetricQuery(rate("node_vmstat_pgmajfault"), "node", "faults/s"),
        "host_runnable_processes": MetricQuery("node_procs_running" + NODE, "node", "processes"),
    },
    "disk": {
        "disk_read_latency_seconds": MetricQuery(fraction(rate("node_disk_read_time_seconds_total"), rate("node_disk_reads_completed_total")), "device", "seconds/operation"),
        "disk_write_latency_seconds": MetricQuery(fraction(rate("node_disk_write_time_seconds_total"), rate("node_disk_writes_completed_total")), "device", "seconds/operation"),
        "disk_flush_latency_seconds": MetricQuery(fraction(rate("node_disk_flush_requests_time_seconds_total"), rate("node_disk_flush_requests_total")), "device", "seconds/operation"),
        "disk_queue_depth": MetricQuery(rate("node_disk_io_time_weighted_seconds_total"), "device", "operations"),
        "disk_read_operations_per_second": MetricQuery(rate("node_disk_reads_completed_total"), "device", "operations/s", "mean"),
        "disk_write_operations_per_second": MetricQuery(rate("node_disk_writes_completed_total"), "device", "operations/s", "mean"),
        "disk_write_bytes_per_second": MetricQuery(rate("node_disk_written_bytes_total"), "device", "bytes/s", "mean"),
    },
    "filesystem": {
        "filesystem_available_ratio": MetricQuery(fraction("node_filesystem_avail_bytes" + NODE, "node_filesystem_size_bytes" + NODE), "node", "ratio", "min"),
        "filesystem_inode_available_ratio": MetricQuery(fraction("node_filesystem_files_free" + NODE, "node_filesystem_files" + NODE), "node", "ratio", "min"),
        "filesystem_readonly": MetricQuery("node_filesystem_readonly" + NODE, "node", "boolean"),
        "filesystem_device_error": MetricQuery("node_filesystem_device_error" + NODE, "node", "boolean"),
    },
    "network": {
        **{f"network_{direction}_bytes_per_second": MetricQuery(rate(f"node_network_{direction}_bytes_total", '{instance="{node}",device!="lo"}'), "network-interface", "bytes/s", "mean") for direction in ("receive", "transmit")},
        **{f"network_{direction}_{kind}_per_second": MetricQuery(rate(f"node_network_{direction}_{metric}_total", '{instance="{node}",device!="lo"}'), "network-interface", "packets/s") for direction in ("receive", "transmit") for kind, metric in (("errors", "errs"), ("drops", "drop"))},
        "tcp_retransmits_per_second": MetricQuery(rate("node_netstat_Tcp_RetransSegs"), "node", "segments/s"),
    },
    "rdma": {
        "rdma_receive_errors_per_second": MetricQuery(rate("node_infiniband_port_errors_received_total"), "network-interface", "packets/s"),
        "rdma_transmit_discards_per_second": MetricQuery(rate("node_infiniband_port_discards_transmitted_total"), "network-interface", "packets/s"),
        # Hardware-defined ticks cannot safely be converted to stall seconds.
        "rdma_transmit_wait_ticks_per_second": MetricQuery(rate("node_infiniband_port_transmit_wait_total"), "network-interface", "ticks/s"),
    },
    "vllm": {
        "vllm_ttft_p95_seconds": MetricQuery(quantile("vllm:time_to_first_token_seconds"), "service", "seconds"),
        "vllm_tpot_p95_seconds": MetricQuery(quantile("vllm:request_time_per_output_token_seconds"), "service", "seconds"),
        "vllm_queue_p95_seconds": MetricQuery(quantile("vllm:request_queue_time_seconds"), "service", "seconds"),
        "vllm_e2e_p95_seconds": MetricQuery(quantile("vllm:e2e_request_latency_seconds"), "service", "seconds"),
        "vllm_generation_tokens_per_second": MetricQuery(rate("vllm:generation_tokens_total", VLLM), "service", "tokens/s", "mean"),
        "vllm_prompt_tokens_per_second": MetricQuery(rate("vllm:prompt_tokens_total", VLLM), "service", "tokens/s", "mean"),
    },
    "kv_offload": {
        "vllm_kv_offload_load_bytes_per_second": MetricQuery(offload_rate("load"), "service", "bytes/s", "mean"),
        "vllm_kv_offload_store_bytes_per_second": MetricQuery(offload_rate("store"), "service", "bytes/s", "mean"),
        "vllm_kv_offload_allocation_failures_per_second": MetricQuery(rate("vllm:kv_offload_allocation_failure_total", VLLM), "service", "failures/s"),
        "vllm_kv_offload_lookup_p95_seconds": MetricQuery(quantile("vllm:kv_offload_lookup_sync_delay_seconds"), "service", "seconds"),
        "vllm_kv_offload_async_lookup_p95_seconds": MetricQuery(quantile("vllm:kv_offload_lookup_async_delay_seconds"), "service", "seconds"),
    },
    # Expressions are borrowed lazily from the canonical dashboard references
    # below. Metadata is validated against its native panel units on selection.
    # No source/entity relationship or new storage verdict is inferred here.
    "mooncake": {
        "mooncake_connector_rpc_p95_seconds": MetricQuery("", "shared-service", "seconds"),
        "mooncake_dfs_read_p95_seconds": MetricQuery("", "shared-service", "seconds"),
        "mooncake_dfs_write_p95_seconds": MetricQuery("", "shared-service", "seconds"),
        "mooncake_dfs_write_staging_p95_seconds": MetricQuery("", "shared-service", "seconds"),
        "mooncake_dfs_read_bytes_per_second": MetricQuery("", "shared-service", "bytes/s", "mean"),
        "mooncake_dfs_read_errors_per_second": MetricQuery("", "shared-service", "keys/s"),
    },
    "ray": {
        # These are current bytes, not spill/restore throughput counters.
        "ray_spilled_bytes": MetricQuery('sum without (Location, ObjectState) (ray_object_store_memory' + RAY[:-1] + ',Location="SPILLED"})', "service", "bytes"),
        "ray_mmap_disk_bytes": MetricQuery('sum without (Location, ObjectState) (ray_object_store_memory' + RAY[:-1] + ',Location="MMAP_DISK"})', "service", "bytes"),
        # Raylet implementation metrics, version-dependent (not stable API).
        "ray_pending_spill_bytes": MetricQuery("ray_spill_manager_objects_bytes" + RAY[:-1] + ',State="PendingSpill"}', "service", "bytes"),
        "ray_pending_restore_bytes": MetricQuery("ray_spill_manager_objects_bytes" + RAY[:-1] + ',State="PendingRestore"}', "service", "bytes"),
        "ray_worker_evictions_per_second": MetricQuery('sum without (Type, Name) (' + rate("ray_memory_manager_worker_eviction_total", RAY) + ')', "service", "evictions/s"),
    },
    "dcgm": {
        "gpu_dcgm_utilization_percent": MetricQuery(dcgm_gauge("DCGM_FI_DEV_GPU_UTIL", "100", inclusive=True), "device", "percent", "mean"),
        "gpu_tensor_active_ratio": MetricQuery(dcgm_gauge("DCGM_FI_PROF_PIPE_TENSOR_ACTIVE", "1", inclusive=True), "device", "ratio", "mean"),
        "gpu_dram_active_ratio": MetricQuery(dcgm_gauge("DCGM_FI_PROF_DRAM_ACTIVE", "1", inclusive=True), "device", "ratio", "mean"),
        "gpu_pcie_receive_bytes_per_second": MetricQuery(dcgm_gauge("DCGM_FI_PROF_PCIE_RX_BYTES", "1e18"), "device", "bytes/s", "mean"),
        "gpu_pcie_transmit_bytes_per_second": MetricQuery(dcgm_gauge("DCGM_FI_PROF_PCIE_TX_BYTES", "1e18"), "device", "bytes/s", "mean"),
        "gpu_pcie_replays_per_second": MetricQuery(dcgm_counter_rate("DCGM_FI_DEV_PCIE_REPLAY_COUNTER"), "device", "replays/s"),
        # XID is a persistent last-code gauge, not a count of recent failures.
        "gpu_last_xid_code": MetricQuery(dcgm_gauge("DCGM_FI_DEV_XID_ERRORS", "1e9"), "device", "error_code", "last"),
    },
}

# Profile selection is fixed and opt-in, keeping the default request count and
# the existing global per-analysis deadline unchanged.
PROFILE_SIGNALS = {name: spec for profile in METRIC_PROFILES.values() for name, spec in profile.items()}
_MOONCAKE_REFERENCES = {
    "mooncake_connector_rpc_p95_seconds": (60, "A"),
    "mooncake_dfs_read_p95_seconds": (66, "A"),
    "mooncake_dfs_write_p95_seconds": (66, "B"),
    "mooncake_dfs_write_staging_p95_seconds": (66, "C"),
    "mooncake_dfs_read_bytes_per_second": (64, "A"),
    "mooncake_dfs_read_errors_per_second": (67, "A"),
}


def validate_metric_profiles(settings: Mapping) -> list[str]:
    profiles = settings.get("metric_profiles", [])
    if (not isinstance(profiles, list) or len(profiles) > len(METRIC_PROFILES)
            or any(not isinstance(name, str) or name not in METRIC_PROFILES for name in profiles)
            or len(set(profiles)) != len(profiles)):
        raise ValueError("prometheus.metric_profiles must be a list of unique supported profiles: " + ", ".join(METRIC_PROFILES))
    return profiles


def profile_queries(settings: Mapping, cluster: str) -> dict[str, str]:
    queries = {}
    for profile in validate_metric_profiles(settings):
        if profile == "mooncake":
            from .canonical_queries import borrow_mooncake_queries
            references = {name: (*reference, METRIC_PROFILES[profile][name].unit)
                          for name, reference in _MOONCAKE_REFERENCES.items()}
            queries.update(borrow_mooncake_queries(references, cluster=bool(cluster)))
            continue
        for name, spec in METRIC_PROFILES[profile].items():
            job = "native" if profile in {"vllm", "kv_offload", "ray", "dcgm"} else "telemetry"
            labels = 'job="' + job + '",'
            if cluster:
                labels = 'cluster="{cluster}",' + labels
            # Placeholders also use braces, so only substitute actual selectors.
            expression = spec.expression.replace('{instance=', '{' + labels + 'instance=')
            expression = expression.replace('{telemetry_source=', '{' + labels + 'telemetry_source=')
            queries[name] = expression
    return queries
