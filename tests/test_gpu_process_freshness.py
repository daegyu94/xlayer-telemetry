"""Independent GPU process freshness, including saved/older collector data."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from xlayer_telemetry.collectors import gpu_sampler
from xlayer_telemetry.demos.live import Demo


ROOT = Path(__file__).parents[1]
PROCESS_METRIC = "telemetry_gpu_process_memory_bytes"
PROCESS_TIME = "telemetry_gpu_process_sample_timestamp_seconds"
DEVICE_TIME = "telemetry_gpu_sample_timestamp_seconds"


@pytest.mark.parametrize("device_observed", [False, True])
@pytest.mark.parametrize("case,observed", [
    ("valid", True), ("zero", True), ("partial", True),
    ("disabled", False), ("failure", False), ("unavailable", False), ("empty", False),
])
def test_sampler_emits_process_freshness_only_for_actual_opt_in_measurements(
        tmp_path, monkeypatch, device_observed, case, observed):
    calls = []
    def query(kind, fields):
        calls.append(kind)
        if kind == "gpu":
            return [["0", "47" if device_observed else "N/A", "N/A", "N/A", "N/A",
                     "N/A", "N/A", "GPU-example"]]
        if case == "failure":
            raise subprocess.CalledProcessError(15, "nvidia-smi")
        if case == "empty":
            return []
        memory = "0" if case == "zero" else "N/A" if case == "unavailable" else "512"
        rows = [["GPU-example", "123", "python", memory]]
        return rows + [["bad-row"]] if case == "partial" else rows

    monkeypatch.setattr(gpu_sampler, "query", query)
    monkeypatch.setattr(gpu_sampler, "time", SimpleNamespace(
        monotonic=iter((0, 0, 0, 2)).__next__, time=lambda: 123, sleep=lambda _: None,
    ))
    monkeypatch.setattr(sys, "argv", ["gpu_sampler", "--output", str(tmp_path / "gpu.jsonl"),
        "--textfile-dir", str(tmp_path), "--duration", "1"]
        + (["--process-metrics"] if case != "disabled" else []))
    # A previous process sample must not retain its freshness after failure/disable.
    (tmp_path / "gpu.prom").write_text(f"{PROCESS_TIME} 100\n")
    gpu_sampler.main()
    text = (tmp_path / "gpu.prom").read_text()
    row = json.loads((tmp_path / "gpu.jsonl").read_text())
    assert (PROCESS_TIME in text) is observed
    assert (PROCESS_METRIC in text) is observed
    assert (DEVICE_TIME in text) is device_observed
    assert row["collection_success"] is device_observed
    if observed:
        assert f"{PROCESS_TIME} 123" in text
        amount = 0 if case == "zero" else 512 * 1024**2
        assert f'{PROCESS_METRIC}{{gpu="0",gpu_uuid="GPU-example",pid="123"}} {amount}' in text
    if case == "disabled":
        assert calls == ["gpu"]
    if case == "failure":
        assert row["compute_processes_error"] == "CalledProcessError"
    if case == "partial":
        assert row["compute_processes_error"] == "invalid_compute_process_row"


def memory_targets():
    dashboard = json.loads((ROOT / "examples/dashboards/compute-communication.json").read_text())
    panel = next(panel for panel in dashboard["panels"] if panel["id"] == 6)
    return {target["refId"]: target["expr"] for target in panel["targets"]}


def test_only_process_panel_uses_independent_freshness_with_explicit_legacy_fallback():
    targets = memory_targets()
    process = targets["A"]
    assert PROCESS_TIME in process
    assert f"unless on(cluster, instance) {PROCESS_TIME}" in process
    assert DEVICE_TIME in process
    assert 'gpu=~"$gpu"' in process
    assert 'nodename=~"$node"' in process
    assert 'cluster=~"$cluster"' in process
    assert "run_id" not in process
    assert "and on(cluster, instance)" in process
    for ref in ("B", "C"):
        assert DEVICE_TIME in targets[ref]
        assert PROCESS_TIME not in targets[ref]


def test_live_demo_emits_explicit_synthetic_process_freshness():
    demo = Demo(ROOT / "examples/live-demo")
    samples = demo.metrics("gpu-node-0")
    freshness = [sample for sample in samples if sample.name == PROCESS_TIME]
    assert len(freshness) == 1
    assert "Synthetic" in freshness[0].help
    assert freshness[0].value > 0
    assert not freshness[0].labels
    assert any(sample.name == PROCESS_METRIC for sample in samples)


def test_actual_process_panel_query_preserves_freshness_fallback_and_scope(tmp_path):
    tool = os.environ.get("PROMTOOL") or shutil.which("promtool")
    if not tool:
        pytest.skip("set PROMTOOL to evaluate GPU process freshness with real PromQL")
    query = memory_targets()["A"].replace("$gpu", "0").replace("$cluster", "lab").replace("$node", "node-a")
    base = {"job": "telemetry", "cluster": "lab", "nodename": "node-a", "instance": "node-a:19100"}
    process_labels = {**base, "gpu": "0", "gpu_uuid": "GPU-example", "pid": "123"}

    def labels(values):
        return "{" + ",".join(f'{key}="{value}"' for key, value in values.items()) + "}"

    def series(name, value, scope):
        return {"series": name + labels(scope), "values": f"{value}+0x5"}

    fixtures = []
    for name, process_time, device_time, expected, memory in [
        ("process-only", 290, None, True, 512),
        ("stale-process-fresh-device", 0, 290, False, 512),
        ("process-at-freshness-boundary", 270, 290, False, 512),
        ("fresh-process-stale-device", 290, 0, True, 512),
        ("older-collector-fresh", None, 290, True, 512),
        ("older-collector-stale", None, 0, False, 512),
        ("missing-freshness", None, None, False, 512),
        ("observed-zero", 290, None, True, 0),
    ]:
        inputs = [series(PROCESS_METRIC, memory, process_labels)]
        if process_time is not None:
            inputs.append(series(PROCESS_TIME, process_time, base))
        if device_time is not None:
            inputs.append(series(DEVICE_TIME, device_time, base))
        fixtures.append({"name": name, "interval": "1m", "input_series": inputs,
            "promql_expr_test": [{"expr": query, "eval_time": "5m", "exp_samples": [
                {"labels": PROCESS_METRIC + labels(process_labels), "value": memory},
            ] if expected else []}]})
    for field, other in (("cluster", "other"), ("instance", "other:19100"), ("nodename", "other")):
        fixtures.append({"name": f"different-{field}", "interval": "1m", "input_series": [
            series(PROCESS_METRIC, 512, process_labels), series(PROCESS_TIME, 290, {**base, field: other}),
        ], "promql_expr_test": [{"expr": query, "eval_time": "5m", "exp_samples": []}]})
    fixtures.append({"name": "other-gpu", "interval": "1m", "input_series": [
        series(PROCESS_METRIC, 512, {**process_labels, "gpu": "1", "gpu_uuid": "GPU-other"}),
        series(PROCESS_TIME, 290, base),
    ], "promql_expr_test": [{"expr": query, "eval_time": "5m", "exp_samples": []}]})
    legacy_scope = {**base, "instance": "legacy:19100"}
    legacy_process = {**process_labels, "instance": "legacy:19100", "pid": "456"}
    fixtures.append({"name": "mixed-new-stale-and-legacy-fresh", "interval": "1m", "input_series": [
        series(PROCESS_METRIC, 512, process_labels), series(PROCESS_TIME, 0, base),
        series(DEVICE_TIME, 290, base), series(PROCESS_METRIC, 256, legacy_process),
        series(DEVICE_TIME, 290, legacy_scope),
    ], "promql_expr_test": [{"expr": query, "eval_time": "5m", "exp_samples": [
        {"labels": PROCESS_METRIC + labels(legacy_process), "value": 256},
    ]}]})
    fixture = tmp_path / "gpu-process-freshness.json"
    fixture.write_text(json.dumps({"evaluation_interval": "1m", "tests": fixtures}))
    result = subprocess.run([tool, "test", "rules", str(fixture)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
