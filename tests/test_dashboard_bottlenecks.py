"""Exporter contracts and executable PromQL for optional bottleneck panels."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "examples/dashboards"


def panels(name):
    def walk(items):
        for panel in items:
            yield panel
            yield from walk(panel.get("panels", []))
    return list(walk(json.loads((SOURCE / f"{name}.json").read_text())["panels"]))


def query(name, title, ref="A"):
    panel = next(p for p in panels(name) if p["title"] == title)
    return next(t["expr"] for t in panel["targets"] if t["refId"] == ref)


def render(expr, **overrides):
    values = dict(cluster="lab", node=".*", source_node=".*", sandbox_node=".*",
                  run_id=".*", gpu=".*", engine=".*", device=".*", mount=".*",
                  storage_system=".*", storage_node=".*", ssd=".*", phase=".*",
                  role=".*", worker=".*", training_max_age="300", __rate_interval="1m")
    values.update(overrides)
    for key in sorted(values, key=len, reverse=True):
        expr = expr.replace("$" + key, values[key])
    assert not re.search(r"\$[a-zA-Z_]", expr), expr
    return expr


def promtool(tmp_path, tests):
    tool = os.environ.get("PROMTOOL") or shutil.which("promtool")
    if not tool:
        pytest.skip("set PROMTOOL to evaluate dashboard bottleneck queries")
    path = tmp_path / "dashboard-queries.json"
    path.write_text(json.dumps({"evaluation_interval": "15s", "fuzzy_compare": True, "tests": tests}))
    result = subprocess.run([tool, "test", "rules", str(path)], capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def series(metric, labels, values):
    return {"series": metric + "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}", "values": values}


def expected(labels, value):
    return {"labels": "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}", "value": value}


def test_optional_bottleneck_rows_preserve_scope_and_missing_evidence():
    additions = {
        "compute-communication": {94, 95, 96},
        "data-storage": {92},
        "agent-rl-stages": {94},
    }
    for name, ids in additions.items():
        dashboard = json.loads((SOURCE / f"{name}.json").read_text())
        for row in (p for p in dashboard["panels"] if p["id"] in ids):
            assert row["collapsed"] and row["panels"]
            for panel in row["panels"]:
                assert panel["fieldConfig"]["defaults"]["noValue"] == "N/A"
                for target in panel["targets"]:
                    expr = target["expr"]
                    assert 'cluster=~"$cluster"' in expr
                    assert 'node=~"$node"' in expr or 'nodename=~"$node"' in expr
                    assert "run_id" not in expr
                    assert "or vector(0)" not in expr
                    if "rate(" in expr:
                        assert "[$__rate_interval]" in expr
    # Existing host aggregations must retain cluster even when instances collide.
    for title in ("TCP/Ethernet throughput by interface", "RDMA throughput by port"):
        assert "sum by (cluster, nodename, instance, device" in query("compute-communication", title)


def test_optional_subsystem_panels_are_collapsed_but_storage_performance_is_primary():
    additions = {
        "compute-communication": set(range(30, 36)) | set(range(40, 46)) | set(range(50, 57)),
        "agent-rl-stages": set(range(27, 35)) | set(range(40, 43)),
    }
    assert sum(map(len, additions.values())) == 30
    for name, expected_ids in additions.items():
        dashboard = json.loads((SOURCE / f"{name}.json").read_text())
        assert not expected_ids.intersection(p["id"] for p in dashboard["panels"])
        collapsed_ids = {p["id"] for row in dashboard["panels"] if row.get("collapsed")
                         for p in row.get("panels", [])}
        assert expected_ids <= collapsed_ids
    storage=json.loads((SOURCE / "data-storage.json").read_text())
    assert set(range(30,35)) <= {panel["id"] for panel in storage["panels"]}


def test_dcgm_and_native_queries_keep_exporter_units_and_identity():
    for title in ("DCGM PCIe throughput", "DCGM last XID error", "DCGM framebuffer memory"):
        expr = query("compute-communication", title)
        assert "rate(" not in expr  # PCIe profiling is already bytes/s; XID is a code.
        assert 'job="native",telemetry_source="dcgm"' in expr
        assert 'gpu=~"$gpu"' in expr
        assert "telemetry_gpu_" not in expr  # Never sum sampler and DCGM measurements.
    assert "/ 1e9" in query("compute-communication", "DCGM power and thermal throttling")
    wait = next(p for p in panels("compute-communication") if p["title"] == "RDMA transmit wait ticks")
    assert wait["fieldConfig"]["defaults"]["unit"] == "suffix:ticks/s"
    assert "rate(" not in query("agent-rl-stages", "Ray retried task states")
    assert "sum by (cluster, SessionName, State)" in query("agent-rl-stages", "Ray retried task states")
    assert 'IsRetry="1"' in query("agent-rl-stages", "Ray retried task states")
    for title in ("vLLM request latency p95", "vLLM prefill and decode latency p95"):
        expr = query("agent-rl-stages", title)
        assert "model_name, engine, le" in expr
    for title in ("Disk mean completed I/O latency", "Filesystem available inodes"):
        assert " > 0)" in query("data-storage", title)
    assert "or on (cluster, node, instance, model_name, engine)" in query("agent-rl-stages", "vLLM KV offload store and load")
    compute = json.loads((SOURCE / "compute-communication.json").read_text())
    variables = {v["name"]: v for v in compute["templating"]["list"]}
    assert 'job="native"' in variables["node"]["query"]
    assert "DCGM_FI_DEV_GPU_UTIL" in variables["gpu"]["query"]


def test_all_dashboard_promql_is_valid_and_empty_stays_empty(tmp_path):
    expressions = set()
    for path in SOURCE.glob("*.json"):
        for panel in panels(path.stem):
            if panel.get("datasource", {}).get("uid") == "telemetry-prometheus":
                expressions.update(render(t["expr"]) for t in panel["targets"])
    promtool(tmp_path, [{"interval": "15s", "input_series": [], "promql_expr_test": [
        {"expr": expr, "eval_time": "2m", "exp_samples": []} for expr in sorted(expressions)
    ]}])


def test_disk_latency_excludes_idle_and_preserves_colliding_clusters(tmp_path):
    data, samples = [], []
    for cluster, device, ops, io_time in (("lab", "nvme0n1", 30, 6), ("other", "nvme0n1", 30, 9),
                                           ("lab", "nvme1n1", 0, 0)):
        labels = dict(job="telemetry", cluster=cluster, nodename="worker", instance="worker:9100", device=device)
        data += [series("node_disk_reads_completed_total", labels, f"0+{ops}x8"),
                 series("node_disk_read_time_seconds_total", labels, f"0+{io_time}x8")]
        if ops:
            samples.append(expected(labels, io_time / ops))
    expr = render(query("data-storage", "Disk mean completed I/O latency"), cluster="lab|other")
    promtool(tmp_path, [{"interval": "15s", "input_series": data, "promql_expr_test": [
        {"expr": expr, "eval_time": "2m", "exp_samples": samples}
    ]}])


def test_offload_prefers_flat_metric_without_doubling_legacy_engine(tmp_path):
    base = dict(job="native", telemetry_source="vllm", cluster="lab", node="rollout", instance="vllm:8000", model_name="model")
    current, old = {**base, "engine": "0"}, {**base, "engine": "0", "transfer_type": "GPU_to_CPU"}
    legacy_only = {**base, "engine": "1", "transfer_type": "GPU_to_CPU"}
    data = [series("vllm:kv_offload_store_bytes_total", current, "0+300x8"),
            series("vllm:kv_offload_total_bytes_total", old, "0+600x8"),
            series("vllm:kv_offload_total_bytes_total", legacy_only, "0+1500x8")]
    expr = render(query("agent-rl-stages", "vLLM KV offload store and load"))
    promtool(tmp_path, [{"interval": "15s", "input_series": data, "promql_expr_test": [
        {"expr": expr, "eval_time": "2m", "exp_samples": [expected(current, 20), expected(legacy_only, 100)]}
    ]}])


def test_prefix_hit_ratio_keeps_observed_zero_but_not_no_queries(tmp_path):
    base = dict(job="native", telemetry_source="vllm", cluster="lab", node="rollout", instance="vllm:8000", model_name="model")
    data = []
    for engine, queries in (("0", 30), ("1", 0)):
        data += [series("vllm:prefix_cache_hits_total", {**base, "engine": engine}, "0+0x8"),
                 series("vllm:prefix_cache_queries_total", {**base, "engine": engine}, f"0+{queries}x8")]
    expr = render(query("agent-rl-stages", "vLLM prefix cache token hit ratio"))
    promtool(tmp_path, [{"interval": "15s", "input_series": data, "promql_expr_test": [
        {"expr": expr, "eval_time": "2m", "exp_samples": [expected({**base, "engine": "0"}, 0)]}
    ]}])


def test_dcgm_gauges_do_not_become_rates_and_sentinels_are_excluded(tmp_path):
    base = dict(job="native", telemetry_source="dcgm", cluster="lab", node="worker", instance="dcgm:9400")
    good, unsupported = {**base, "gpu": "0"}, {**base, "gpu": "1"}
    metric = "DCGM_FI_PROF_PCIE_RX_BYTES"
    expr = render(query("compute-communication", "DCGM PCIe throughput"))
    promtool(tmp_path, [{"interval": "15s", "input_series": [series(metric, good, "1000+0x8"),
                       series(metric, unsupported, "9223372036854775794+0x8")], "promql_expr_test": [
        {"expr": expr, "eval_time": "2m", "exp_samples": [expected({"__name__": metric, **good}, 1000)]}
    ]}])
