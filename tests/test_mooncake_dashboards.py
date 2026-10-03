"""Keep Mooncake native evidence scoped and exercise its real PromQL semantics."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from xlayer_telemetry.demos.live import Demo


ROOT = Path(__file__).parents[1]


def mooncake_row():
    dashboard = json.loads((ROOT / "examples/dashboards/agent-rl-stages.json").read_text())
    return next(panel for panel in dashboard["panels"] if panel["id"] == 94)


def render(expression):
    for variable, value in {"$cluster": "lab", "$node": "rollout", "$engine": ".*",
                            "$__rate_interval": "1m"}.items():
        expression = expression.replace(variable, value)
    return expression


def test_mooncake_panels_preserve_source_scope_and_units():
    row = mooncake_row()
    assert row["collapsed"] and len(row["panels"]) == 8
    panels = {panel["id"]: panel for panel in row["panels"]}
    for panel in panels.values():
        assert panel["fieldConfig"]["defaults"]["noValue"] == "N/A"
        for target in panel["targets"]:
            query = target["expr"]
            assert 'job="native"' in query and 'cluster=~"$cluster"' in query
            assert 'node=~"$node"' in query and "run_id" not in query
            assert "or vector(0)" not in query
            assert "{{instance}}" in target["legendFormat"]
            if "vllm:" in query:
                assert 'telemetry_source="vllm"' in query and 'instance=~"$engine"' in query
            else:
                assert 'telemetry_source="mooncake"' in query
    assert panels[30]["fieldConfig"]["defaults"]["unit"] == "s"
    assert panels[31]["fieldConfig"]["defaults"]["unit"] == "Bps"
    assert panels[32]["fieldConfig"]["defaults"]["unit"] == "bytes"
    assert "file_capacity" not in str(panels[32]["targets"])
    assert "master_total_capacity_bytes" in str(panels[32]["targets"])
    assert panels[34]["fieldConfig"]["defaults"]["unit"] == "Bps"
    assert panels[36]["fieldConfig"]["defaults"]["unit"] == "s"
    for target in panels[36]["targets"]:
        assert "/ 1000000" in target["expr"]
        assert "client_mode, cluster_id, le" in target["expr"]
        assert 'le="+Inf"' in target["expr"] and "> 0" in target["expr"]
    assert "failed keys/s" in str(panels[37]["targets"])
    assert "error RPC/s" in str(panels[37]["targets"])
    assert "master_put_start_failures_total" in str(panels[37]["targets"])


def test_mooncake_synthetic_counters_match_native_identity_and_bucket_counts(monkeypatch):
    import xlayer_telemetry.demos.live as live
    demo = Demo(ROOT / "examples/live-demo")
    origin = demo.started
    monkeypatch.setattr(live.time, "monotonic", lambda: origin + 1)
    before = {endpoint: demo.metrics(endpoint) for endpoint in ("vllm", "mooncake-master", "mooncake-client")}
    monkeypatch.setattr(live.time, "monotonic", lambda: origin + 3)
    for endpoint, first in before.items():
        later = demo.metrics(endpoint)
        assert len(first) == len(later)
        for a, b in zip(first, later):
            assert (a.name, a.labels) == (b.name, b.labels)
            if b.kind == "counter":
                assert b.value >= a.value
            assert "run_id" not in b.labels
        if endpoint == "mooncake-client":
            assert all(sample.labels["client_mode"] == "real" for sample in later if not sample.name.endswith("_count"))
            assert all(sample.labels["cluster_id"] == "synthetic-store" for sample in later if not sample.name.endswith("_count"))
            assert all(not sample.labels for sample in later if sample.name.endswith("_count"))
        counts = {(sample.name.removesuffix("_count"), tuple(sorted(sample.labels.items()))): sample.value
                  for sample in later if sample.name.endswith("_count")}
        for sample in later:
            if sample.name.endswith("_bucket") and sample.labels.get("le") == "+Inf" and "mooncake" in sample.name:
                labels = tuple(sorted((key, value) for key, value in sample.labels.items() if key != "le"))
                if endpoint == "mooncake-client":
                    labels = ()  # Current Mooncake exports _count without native client labels.
                assert sample.value == counts[(sample.name.removesuffix("_bucket"), labels)]


def test_mooncake_queries_with_real_promtool(tmp_path):
    tool = os.environ.get("PROMTOOL") or shutil.which("promtool")
    if not tool:
        pytest.skip("set PROMTOOL to evaluate Mooncake queries")
    panels = {panel["id"]: panel for panel in mooncake_row()["panels"]}
    rows = []

    def add_histogram(name, labels, bounds, *, idle=False, missing_count=False):
        identity = ",".join(f'{key}="{value}"' for key, value in labels.items())
        for bound, count in bounds:
            rows.append({"series": f'{name}_bucket{{{identity},le="{bound}"}}',
                         "values": f'0+{0 if idle else count}x4'})
        if not missing_count:
            count_labels = {key: value for key, value in labels.items()
                            if not (name.startswith("mooncake_dfs_") and key in {"client_mode", "cluster_id"})}
            count_identity = ",".join(f'{key}="{value}"' for key, value in count_labels.items())
            rows.append({"series": f'{name}_count{{{count_identity}}}', "values": f'0+{0 if idle else 100}x4'})
        return "{" + ",".join(f'{key}="{value}"' for key, value in labels.items()
                                if key not in {"job", "telemetry_source"}) + "}"

    def client(instance, cluster="lab"):
        return {"job": "native", "telemetry_source": "mooncake", "cluster": cluster,
                "node": "rollout", "instance": instance, "component": instance,
                "client_mode": "real", "cluster_id": "store"}

    expected = []
    for instance, scale in (("client-a", 1), ("client-b", 2)):
        labels = client(instance)
        result = add_histogram("mooncake_dfs_read_latency_us", labels,
                               [(str(100 * scale), 10), (str(1000 * scale), 95),
                                (str(10000 * scale), 100), ("+Inf", 100)])
        expected.append({"labels": result, "value": .001 * scale})
    for instance, idle, missing_count in (("idle", True, False), ("missing-count", False, True)):
        result = add_histogram("mooncake_dfs_read_latency_us", client(instance),
                               [("100", 10), ("1000", 95), ("+Inf", 100)], idle=idle, missing_count=missing_count)
        if missing_count:
            expected.append({"labels": result, "value": .001})
    add_histogram("mooncake_dfs_read_latency_us", client("other-cluster", "other"),
                  [("100", 10), ("1000", 95), ("+Inf", 100)])
    master_labels = '{job="native",telemetry_source="mooncake",cluster="lab",node="rollout",instance="master-a",component="master-a"}'
    rows.append({"series": "master_put_start_failures_total" + master_labels, "values": "0+1x4"})
    connector_expected = []
    for engine, scale in (("0", 1), ("1", 2)):
        connector_labels = {"job": "native", "telemetry_source": "vllm", "cluster": "lab",
                            "node": "rollout", "instance": "engine-a", "component": "rollout-a",
                            "model_name": "model", "engine": engine, "operation": "load_get", "status": "ok"}
        connector_result = add_histogram("vllm:mooncake_store_operation_time_seconds", connector_labels,
                                        [(str(.001 * scale), 10), (str(.01 * scale), 95),
                                         (str(.1 * scale), 100), ("+Inf", 100)])
        connector_expected.append({"labels": connector_result, "value": .01 * scale})
    fixture = {"evaluation_interval": "30s", "fuzzy_compare": True,
        "tests": [{"interval": "30s", "input_series": rows,
        "promql_expr_test": [
            {"expr": render(panels[36]["targets"][0]["expr"]), "eval_time": "2m", "exp_samples": expected},
            {"expr": render(panels[36]["targets"][1]["expr"]), "eval_time": "2m", "exp_samples": []},
            {"expr": render(panels[30]["targets"][0]["expr"]), "eval_time": "2m",
             "exp_samples": connector_expected},
            {"expr": render(panels[37]["targets"][5]["expr"]), "eval_time": "2m",
             "exp_samples": [{"labels": master_labels, "value": 1 / 30}]},
            {"expr": render(panels[37]["targets"][1]["expr"]), "eval_time": "2m",
             "exp_samples": []},  # Master admission failure does not fabricate DFS I/O errors.
        ]}]}
    path = tmp_path / "mooncake-queries.json"
    path.write_text(json.dumps(fixture))
    result = subprocess.run([tool, "test", "rules", str(path)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
