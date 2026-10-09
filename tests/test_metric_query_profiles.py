"""Exercise optional source contracts, entity comparisons and real PromQL."""
import json
import os
import shutil
import subprocess

import pytest

from xlayer_telemetry.analysis.diagnostics import DEFAULT_QUERIES, DiagnosticEngine, ThreeFSClient, load_config
from xlayer_telemetry.analysis.diagnosis_analysis import evaluate_rules
from xlayer_telemetry.analysis.metric_queries import METRIC_PROFILES, PROFILE_SIGNALS, profile_queries


def engine(profiles=(), **kwargs):
    return DiagnosticEngine({"cluster": "lab", "clock": {"enabled": False},
                             "prometheus": {"url": "http://unused", "metric_profiles": list(profiles)}}, **kwargs)


def rendered(profiles=METRIC_PROFILES):
    queries = engine(profiles)._queries("lab")
    return {name: query.replace("{cluster}", "lab").replace("{node}", "n")
            .replace("{compute_node}", "gpu").replace("{rollout_node}", "rollout").replace("{run_id}", "r")
            for name, query in queries.items()}


def test_profiles_are_opt_in_and_queries_can_override_them():
    assert set(engine()._queries("lab")) == set(DEFAULT_QUERIES)
    assert len(PROFILE_SIGNALS) <= 66  # Two version-conditional waiting gauges; default queries unchanged.
    selected = engine(["host"])
    selected.config["prometheus"]["queries"] = {"host_cpu_pressure_ratio": "custom_pressure"}
    assert selected._queries("lab")["host_cpu_pressure_ratio"] == "custom_pressure"
    assert "disk_queue_depth" not in selected._queries("lab")
    for name, query in rendered().items():
        if name in PROFILE_SIGNALS:
            assert 'cluster="lab"' in query
            native = name.startswith(("vllm_", "ray_", "gpu_", "mooncake_"))
            assert 'job="native"' in query if native else 'job="telemetry"' in query
    assert 'telemetry_source="dcgm"' in rendered()["gpu_last_xid_code"]


def test_waiting_reasons_are_opt_in_per_engine_context_not_a_kv_cause():
    assert 'vllm_waiting_deferred_requests' not in engine()._queries('lab')
    expressions = rendered(['vllm_waiting'])
    for reason in ('capacity', 'deferred'):
        name = f'vllm_waiting_{reason}_requests'
        query = expressions[name]
        assert 'vllm:num_requests_waiting_by_reason' in query and f'reason="{reason}"' in query
        assert 'job="native"' in query and 'node="rollout"' in query
        assert 'sum(' not in query and 'or vector(0)' not in query
        assert PROFILE_SIGNALS[name].scope == 'service'


@pytest.mark.parametrize("profiles", ["host", ["bad"], ["host", "host"], [1], None])
def test_invalid_profiles_are_rejected_by_config_and_programmatic_engine(tmp_path, profiles):
    config = {"schema_version": 1, "prometheus": {"url": "http://unused", "metric_profiles": profiles}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="metric_profiles"):
        load_config(path)
    with pytest.raises(ValueError, match="metric_profiles"):
        DiagnosticEngine(config)._queries("lab")


def test_dcgm_gauges_ray_bytes_and_rdma_ticks_keep_their_units():
    queries = rendered()
    for name in ("gpu_last_xid_code", "gpu_pcie_receive_bytes_per_second", "ray_spilled_bytes"):
        assert "rate(" not in queries[name]
    assert PROFILE_SIGNALS["rdma_transmit_wait_ticks_per_second"].unit == "ticks/s"
    assert "inter_token_latency" not in queries["vllm_tpot_p95_seconds"]
    assert "request_time_per_output_token_seconds" in queries["vllm_tpot_p95_seconds"]


def metric(value, **labels):
    return {"labels": {"cluster": "lab", "instance": "n", **labels},
            "stats": {"min": value, "max": value, "mean": value, "last": value}}


class SeriesPrometheus:
    def __init__(self, current, baseline):
        self.current, self.baseline = current, baseline

    def query_range_detail(self, query, start, end, step):
        items = []
        for needle, rows in (self.current if end == 100 else self.baseline).items():
            if needle in query:
                items = rows
                break
        values = [row["stats"]["max"] for row in items]
        stats = {"min": min(values), "max": max(values), "mean": sum(values)/len(values)} if values else None
        return {"aggregate": stats, "series": items}


def report(current, baseline):
    now = {"run_id": "r", "node": "n", "worker_id": "w", "record_id": "current", "step": 2,
           "observed_at": 100, "step_duration_seconds": 20,
           "stage_durations_seconds": {"save_checkpoint": 10, "update_actor": 5},
           "analysis_window": {"start": 80, "end": 100, "accuracy": "exact"}}
    before = {**now, "record_id": "previous", "step": 1, "observed_at": 70, "step_duration_seconds": 10,
              "stage_durations_seconds": {"save_checkpoint": 2, "update_actor": 2},
              "analysis_window": {"start": 60, "end": 70, "accuracy": "exact"}}
    return engine(["host", "disk"], prometheus=SeriesPrometheus(current, baseline)).analyze(now, [before])


def test_extended_comparisons_match_same_device_not_largest_baseline():
    result = report({"node_disk_io_time_weighted": [metric(3, device="a"), metric(1, device="b")]},
                    {"node_disk_io_time_weighted": [metric(.5, device="a"), metric(10, device="b")]})
    row = next(row for row in result["comparison"]["signals"] if row["signal"] == "disk_queue_depth")
    assert (row["current"], row["baseline"], row["labels"]["device"]) == (3, .5, "a")
    assert row["unit"] == "operations"
    assert row["query"] == rendered()["disk_queue_depth"]
    assert result["metric_profiles"] == ["host", "disk"]


def test_extended_entity_replacement_withholds_baseline():
    result = report({"node_disk_io_time_weighted": [metric(3, device="new")]},
                    {"node_disk_io_time_weighted": [metric(.5, device="old")]})
    row = next(row for row in result["comparison"]["signals"] if row["signal"] == "disk_queue_depth")
    assert row["baseline"] is None
    assert "prometheus:disk_queue_depth:baseline_entity_match" in result["missing_sources"]


def test_psi_supports_stalls_but_busy_percentage_alone_does_not():
    current = {"step_duration_seconds": 20, "host_cpu_busy_ratio": 1}
    assert evaluate_rules(current, {"step_duration_seconds": 10}, thresholds={}, context={})
    assert not any(row["id"] == "host_cpu_stalls" for row in evaluate_rules(current, {"step_duration_seconds": 10}, thresholds={}, context={}))
    result = report({"node_pressure_cpu_waiting": [metric(.4)], "node_pressure_io_waiting": [metric(.3)]}, {})
    candidates = {row["id"]: row for row in result["candidates"]}
    for name in ("host_cpu_stalls", "host_io_stalls", "actor_update_cpu_stalls", "checkpoint_io_stalls"):
        assert candidates[name]["state"] == "supporting_signal"
        assert candidates[name]["evidence"][1]["source"] == "prometheus"
        assert candidates[name]["evidence"][1]["query"]
    assert all(row["state"] != "strong_signal" for row in result["candidates"])


def test_threefs_distribution_cardinality_is_bounded(monkeypatch):
    client = ThreeFSClient("http://unused")
    queries = []
    def rows(query):
        queries.append(query)
        return [{"metricName": f"metric-{i}"} for i in range(1001)]
    monkeypatch.setattr(client, "_query_rows", rows)
    with pytest.raises(ValueError, match="1000 entity"):
        client.query_window(1, 2)
    assert "ORDER BY metricName, host" in queries[0] and "LIMIT 1001" in queries[0]


def promtool():
    tool = os.environ.get("PROMTOOL") or shutil.which("promtool")
    if not tool:
        pytest.skip("set PROMTOOL to evaluate optional profile queries")
    return tool


def evaluate(tmp_path, fixture):
    path = tmp_path / "profiles.json"
    fixture["fuzzy_compare"] = True
    path.write_text(json.dumps(fixture))
    process = subprocess.run([promtool(), "test", "rules", str(path)], capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stdout + process.stderr


def test_every_profile_expression_parses_with_real_prometheus(tmp_path):
    evaluate(tmp_path, {"evaluation_interval": "15s", "tests": [{"promql_expr_test": [
        {"expr": query, "eval_time": "1m", "exp_samples": []}
        for query in rendered().values()]}]})


def test_exporter_profile_values_zero_denominators_and_offload_compatibility(tmp_path):
    q = rendered()
    node = 'cluster="lab",job="telemetry",instance="n"'
    native = 'cluster="lab",job="native",telemetry_source="vllm",node="rollout",instance="v:8000",model_name="m",engine="0"'
    rows = []
    def series(name, labels, values):
        rows.append({"series": name + "{" + labels + "}", "values": values})
    disk = node + ',device="nvme0n1"'
    idle = node + ',device="nvme1n1"'
    series("node_disk_read_time_seconds_total", disk, "0+0.3x4")
    series("node_disk_reads_completed_total", disk, "0+30x4")
    series("node_disk_read_time_seconds_total", idle, "0+0x4")
    series("node_disk_reads_completed_total", idle, "0+0x4")
    series("node_disk_io_time_weighted_seconds_total", disk, "0+7.5x4")
    series("node_pressure_cpu_waiting_seconds_total", node, "0+3x4")
    fs = node + ',device="/dev/nvme0n1",mountpoint="/data",fstype="xfs"'
    series("node_filesystem_avail_bytes", fs, "10+0x4")
    series("node_filesystem_size_bytes", fs, "100+0x4")
    series("node_filesystem_avail_bytes", fs.replace('/data', '/zero'), "0+0x4")
    series("node_filesystem_size_bytes", fs.replace('/data', '/zero'), "0+0x4")
    for le, step in (("0.1", 9), ("1", 10), ("+Inf", 10)):
        series("vllm:time_to_first_token_seconds_bucket", native + f',le="{le}"', f"0+{step}x4")
    # Both old and new counters exported: select new once, not their sum.
    series("vllm:kv_offload_load_bytes_total", native, "0+1500x4")
    series("vllm:kv_offload_total_bytes_total", native + ',transfer_type="CPU_to_GPU"', "0+1500x4")
    # A different endpoint exports only the deprecated name; retain it.
    legacy = native.replace('v:8000', 'legacy:8000')
    series("vllm:kv_offload_total_bytes_total", legacy + ',transfer_type="CPU_to_GPU"', "0+750x4")
    tests = [
        ("disk_read_latency_seconds", [(disk, .01)]),
        ("disk_queue_depth", [(disk, .5)]),
        ("host_cpu_pressure_ratio", [(node, .2)]),
        ("filesystem_available_ratio", [(fs, .1)]),
        ("vllm_ttft_p95_seconds", [(native, .55)]),
        ("vllm_kv_offload_load_bytes_per_second", [(native, 100), (legacy, 50)]),
    ]
    evaluate(tmp_path, {"evaluation_interval": "15s", "tests": [{"interval": "15s", "input_series": rows,
        "promql_expr_test": [{"expr": q[name], "eval_time": "1m", "exp_samples": [
            {"labels": "{" + labels + "}", "value": value} for labels, value in values]} for name, values in tests]}]})


def test_dcgm_unsupported_finite_sentinels_are_not_health_signals(tmp_path):
    q = rendered()
    labels = 'cluster="lab",job="native",telemetry_source="dcgm",node="gpu",instance="dcgm:9400",gpu="0",UUID="GPU-0"'
    valid = labels.replace('gpu="0"', 'gpu="1"').replace('GPU-0', 'GPU-1')
    metrics = {"DCGM_FI_DEV_GPU_UTIL": ("gpu_dcgm_utilization_percent", 60),
               "DCGM_FI_PROF_PIPE_TENSOR_ACTIVE": ("gpu_tensor_active_ratio", .7),
               "DCGM_FI_PROF_DRAM_ACTIVE": ("gpu_dram_active_ratio", .8),
               "DCGM_FI_PROF_PCIE_RX_BYTES": ("gpu_pcie_receive_bytes_per_second", 1000),
               "DCGM_FI_DEV_XID_ERRORS": ("gpu_last_xid_code", 31)}
    rows, tests = [], []
    for metric_name, (signal, value) in metrics.items():
        for label_set, number in ((labels, 9223372036854775794), (valid, value)):
            rows.append({"series": metric_name + "{" + label_set + "}", "values": f"{number}+0x4"})
        # Filtering gauge comparisons retains the original metric name.
        tests.append({"expr": q[signal], "eval_time": "1m", "exp_samples": [
            {"labels": metric_name + "{" + valid + "}", "value": value}]})
    rows.append({"series": "DCGM_FI_DEV_PCIE_REPLAY_COUNTER{" + labels + "}",
                 "values": "0 0 9223372036854775794 1 2"})
    tests.append({"expr": q["gpu_pcie_replays_per_second"], "eval_time": "1m", "exp_samples": []})
    evaluate(tmp_path, {"evaluation_interval": "15s", "tests": [{"interval": "15s", "input_series": rows, "promql_expr_test": tests}]})
