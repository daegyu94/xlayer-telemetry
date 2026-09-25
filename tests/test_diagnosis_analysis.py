"""Rule checks use measured scenarios rather than mirroring implementation."""

import json

import pytest

from xlayer_telemetry.diagnosis_analysis import compare_signals, evaluate_rules, select_baseline
from xlayer_telemetry.diagnostics import _investigation_rows


BASE = {
    "step_duration_seconds": 10,
    "rollout_duration_seconds": 5,
    "communication_duration_seconds": 1,
    "gpu_utilization_percent": 90,
    "gpu_memory_usage_ratio": 0.5,
    "gpu_evictions_delta": 0,
    "host_memory_available_ratio": 0.5,
    "host_swap_activity": 0,
    "disk_busy_ratio": 0.2,
    "storage_device_busy_ratio": 0.2,
    "threefs_p99_latency": 2,
    "threefs_throughput_bytes_per_second": 100,
    "network_utilization_ratio": 0.2,
    "rdma_bytes_per_second": 10,
    "vllm_requests_waiting": 0,
    "vllm_kv_cache_usage": 0.4,
    "vllm_preemptions_delta": 0,
}


@pytest.mark.parametrize("identifier,changes,context", [
    ("storage_queue_saturation", {"threefs_p99_latency": 12, "storage_device_busy_ratio": 0.96, "threefs_throughput_bytes_per_second": 110}, {}),
    ("device_limited_storage", {"threefs_p99_latency": 12, "storage_device_busy_ratio": 0.96, "network_utilization_ratio": 0.2}, {}),
    ("network_limited_storage", {"threefs_p99_latency": 12, "storage_device_busy_ratio": 0.3, "network_utilization_ratio": 0.9}, {}),
    ("gpu_starvation", {"step_duration_seconds": 20, "gpu_utilization_percent": 45, "vllm_requests_waiting": 3}, {}),
    ("gpu_memory_pressure", {"gpu_memory_usage_ratio": 0.96, "gpu_evictions_delta": 2}, {}),
    ("rollout_queue_backlog", {"rollout_duration_seconds": 9, "vllm_requests_waiting": 3}, {}),
    ("kv_cache_pressure", {"vllm_kv_cache_usage": 0.95, "vllm_preemptions_delta": 3, "vllm_requests_waiting": 3}, {}),
    ("communication_bound", {"communication_duration_seconds": 3, "rdma_bytes_per_second": 30, "gpu_utilization_percent": 50}, {}),
    ("host_memory_pressure", {"host_memory_available_ratio": 0.05, "host_swap_activity": 10}, {}),
    ("straggler", {}, {"participant_durations_seconds": {"rank-0": 10, "rank-1": 11, "rank-2": 22}}),
])
def test_strong_synthetic_scenarios(identifier, changes, context):
    candidates = evaluate_rules(BASE | changes, BASE, thresholds={}, context=context)
    selected = next(item for item in candidates if item["id"] == identifier)
    assert selected["state"] == "strong_signal"
    assert selected["evidence"]
    assert selected["missing_evidence"] == (["per_run_3fs_client_bytes"] if selected["component"] == "storage" else [])


def test_normal_and_insufficient_evidence():
    assert evaluate_rules(BASE, BASE, thresholds={}, context={}) == []
    partial = evaluate_rules(
        {"threefs_p99_latency": 12, "storage_device_busy_ratio": 0.96},
        {"threefs_p99_latency": 2}, thresholds={}, context={},
    )
    queue = next(item for item in partial if item["id"] == "storage_queue_saturation")
    assert queue["state"] != "strong_signal"
    assert "threefs_throughput_bytes_per_second" in queue["missing_evidence"]
    assert "baseline:threefs_throughput_bytes_per_second" in queue["missing_evidence"]
    no_baseline = evaluate_rules({"threefs_p99_latency": 12, "storage_device_busy_ratio": 0.96}, {}, thresholds={}, context={})
    assert all(item["state"] != "strong_signal" for item in no_baseline)


def test_small_io_requires_request_size():
    observed = BASE | {"threefs_p99_latency": 12}
    assert not any(item["id"] == "small_io_pressure" for item in evaluate_rules(observed, BASE, thresholds={}, context={}))
    observed["storage_request_bytes"] = 1024
    small = next(item for item in evaluate_rules(observed, BASE, thresholds={}, context={}) if item["id"] == "small_io_pressure")
    assert small["state"] == "strong_signal"


def test_baseline_selects_same_run_worker_and_reports_deltas():
    current = {"run_id": "a", "worker_id": "driver", "boundary_scope": "rl_step", "observed_at": 40}
    def item(run, worker, time, duration):
        return {"run_id": run, "worker_id": worker, "boundary_scope": "rl_step", "observed_at": time,
                "step_duration_seconds": duration, "analysis_window": {"start": time-duration, "end": time}}
    history = [item("a", "driver", 10, 10), item("b", "driver", 20, 1), item("a", "other", 25, 1), item("a", "driver", 30, 12)]
    assert select_baseline(current, history)["step_duration_seconds"] == 12
    rows = compare_signals({"threefs_p99_latency": 12, "gpu_utilization_percent": 40}, {"threefs_p99_latency": 2, "gpu_utilization_percent": 80})
    assert rows[0]["signal"] == "threefs_p99_latency"
    assert rows[0]["scope"] == "shared-service"
    assert rows[0]["delta_percent"] == 500


def test_investigation_projection_keeps_scope_and_window():
    report = {"run_id": "r", "node": "n", "step": 3, "trigger_record_id": "abc",
              "analysis_window": {"start": 10, "end": 20, "accuracy": "approximate"},
              "verdict": "bottleneck_suspected", "symptom": {"step_duration_seconds": 10},
              "comparison": {"baseline_interval": {"start": 0, "end": 8}, "baseline_record_id": "prior", "signals": []},
              "candidates": [{"id": "storage_queue_saturation", "component": "storage", "state": "supporting_signal", "summary": "x", "observation_scope": "shared-service", "evidence": [{"signal": "threefs_p99_latency", "value": 10, "observation_scope": "shared-service"}], "missing_evidence": ["per-run client bytes"]}]}
    rows = _investigation_rows(report)
    assert json.loads(json.dumps(rows))[0]["window_start_ms"] == 10000
    assert rows[0]["primary_candidate"] is None
    assert rows[1]["observation_scope"] == "shared-service"
    assert "per-run client bytes" in rows[1]["missing_evidence_summary"]
    assert {row["evidence_type"] for row in rows if row["row_kind"] == "evidence"} == {"supporting", "missing"}
