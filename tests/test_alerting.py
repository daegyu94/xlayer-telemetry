import json
import os
import shutil
import subprocess
import platform
from pathlib import Path

import pytest

from xlayer_telemetry.alerting import build_delete_rules, build_rules


ROOT = Path(__file__).parents[1]


def test_alert_rules_cover_node_gpu_and_selected_filesystem() -> None:
    payload = build_rules("/mnt/3fs", 15)
    group = payload["groups"][0]
    assert group["interval"] == "30s"
    rules = {rule["uid"]: rule for rule in group["rules"]}
    assert set(rules) == {
        "xlayer-node-collector-down",
        "xlayer-gpu-sample-stale",
        "xlayer-filesystem-low-space",
    }
    assert 'up{job="telemetry"} == bool 0' == rules["xlayer-node-collector-down"]["data"][0]["model"]["expr"]
    gpu_query = rules["xlayer-gpu-sample-stale"]["data"][0]["model"]["expr"]
    assert "time() - telemetry_gpu_sample_timestamp_seconds" in gpu_query
    assert "unless on(cluster, instance)" in gpu_query
    assert 'and on(cluster, instance) (up{job="telemetry"} == 1)' in gpu_query
    assert 'unless on(cluster, instance) (telemetry_gpu_collection_enabled{job="telemetry"} == 0)' in gpu_query
    disk_query = rules["xlayer-filesystem-low-space"]["data"][0]["model"]["expr"]
    assert 'mountpoint="/mnt/3fs"' in disk_query
    assert "< bool 0.15" in disk_query
    dashboards = {
        dashboard["uid"]: dashboard
        for path in (ROOT / "examples/dashboards").glob("*.json")
        if (dashboard := json.loads(path.read_text())).get("uid")
    }
    for rule in rules.values():
        assert rule["data"][0]["datasourceUid"] == "telemetry-prometheus"
        assert rule["condition"] == "C"
        assert rule["noDataState"] == "OK"
        assert rule["execErrState"] == "Error"
        assert rule["labels"]["service"] == "xlayer-telemetry"
        dashboard = dashboards[rule["dashboardUid"]]
        assert rule["panelId"] in {panel["id"] for panel in dashboard["panels"]}


@pytest.mark.parametrize("mountpoint", ["relative", '/mnt/"bad', "/mnt/../data"])
def test_alert_mountpoint_rejects_unsafe_values(mountpoint: str) -> None:
    with pytest.raises(ValueError, match="ALERT_MOUNTPOINT"):
        build_rules(mountpoint)


@pytest.mark.parametrize("free_percent", [0, 100])
def test_alert_free_percent_rejects_invalid_values(free_percent: int) -> None:
    with pytest.raises(ValueError, match="ALERT_FREE_PERCENT"):
        build_rules(free_percent=free_percent)


def test_server_config_enables_and_disables_provisioned_alerts(tmp_path: Path) -> None:
    output = tmp_path / "monitoring"
    environment = os.environ | {
        "CLUSTER_NAME": "training-cluster",
        "TELEMETRY_TARGETS": "trainer-0=10.0.0.10",
        "OUTPUT_DIR": str(output),
        "SERVER_CONFIG_ONLY": "1",
        "ENABLE_ALERTS": "1",
        "ALERT_MOUNTPOINT": "/mnt/3fs",
        "ALERT_FREE_PERCENT": "15",
    }
    command = ["bash", str(ROOT / "scripts/run_telemetry.sh"), "server"]
    enabled = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True)
    assert enabled.returncode == 0, enabled.stderr
    path = output / "provisioning/alerting/operations.json"
    assert json.loads(path.read_text()) == build_rules("/mnt/3fs", 15)

    environment["ENABLE_ALERTS"] = "0"
    disabled = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True)
    assert disabled.returncode == 0, disabled.stderr
    assert json.loads(path.read_text()) == build_delete_rules()


def test_server_config_rejects_invalid_alert_mountpoint(tmp_path: Path) -> None:
    environment = os.environ | {
        "TELEMETRY_TARGETS": "trainer-0=10.0.0.10",
        "OUTPUT_DIR": str(tmp_path),
        "SERVER_CONFIG_ONLY": "1",
        "ENABLE_ALERTS": "1",
        "ALERT_MOUNTPOINT": "/mnt/bad\"path",
    }
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/run_telemetry.sh"), "server"],
        cwd=ROOT, env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "ALERT_MOUNTPOINT" in result.stderr


def test_cpu_only_collector_reports_gpu_disabled_and_cleans_marker(tmp_path):
    arch = "arm64" if platform.machine() in {"aarch64", "arm64"} else "amd64"
    tools = tmp_path / "tools"
    exporter = tools / f"node_exporter-1.9.1.linux-{arch}" / "node_exporter"
    exporter.parent.mkdir(parents=True)
    exporter.write_text('#!/bin/sh\ncp "$OUTPUT_DIR/textfile/collector.prom" "$MARKER_COPY"\nsleep .1\n')
    exporter.chmod(0o755)
    output = tmp_path / "output"
    copied = tmp_path / "collector.prom"
    result = subprocess.run(["bash", str(ROOT / "scripts/run_telemetry.sh"), "node"],
                            env=os.environ | {"TOOLS_DIR": str(tools), "OUTPUT_DIR": str(output),
                            "MARKER_COPY": str(copied), "NODE_ADDR": "127.0.0.1", "ENABLE_GPU_METRICS": "0",
                            "LOKI_PUSH_URL": "", "TELEMETRY_LOG_ROOTS": "", "TOPOLOGY_DIR": "",
                            "TELEMETRY_METRICS_DIR": "", "DURATION": "", "ENABLE_SSD_HEALTH": "0"},
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert "telemetry_gpu_collection_enabled 0" in copied.read_text()
    assert not (output / "textfile/collector.prom").exists()


def test_gpu_alert_expression_with_real_promtool(tmp_path):
    tool = os.environ.get("PROMTOOL") or shutil.which("promtool")
    if not tool:
        pytest.skip("set PROMTOOL or install promtool to evaluate real PromQL")
    query = build_rules()["groups"][0]["rules"][1]["data"][0]["model"]["expr"]
    def labels(node, metric_name=False):
        return ('{__name__="up",' if metric_name else '{') + f'cluster="lab",instance="{node}",job="telemetry"}}'
    inputs = [{"series": "up" + labels(node), "values": "1+0x5"}
              for node in ("cpu-only", "gpu-missing", "gpu-fresh", "legacy-missing")]
    inputs.extend({"series": "telemetry_gpu_collection_enabled" + labels(node), "values": f"{value}+0x5"}
                  for node, value in (("cpu-only", 0), ("gpu-missing", 1), ("gpu-fresh", 1)))
    inputs.append({"series": "telemetry_gpu_sample_timestamp_seconds" + labels("gpu-fresh"), "values": "290+0x5"})
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"groups": [{"name": "gpu", "rules": [{"record": "review_gpu", "expr": query}]}]}))
    tests = tmp_path / "tests.json"
    tests.write_text(json.dumps({"rule_files": [str(rules)], "evaluation_interval": "1m", "tests": [{
        "interval": "1m", "input_series": inputs, "promql_expr_test": [{"expr": query, "eval_time": "5m", "exp_samples": [
            {"labels": labels("gpu-missing", True), "value": 1},
            {"labels": labels("legacy-missing", True), "value": 1},
            {"labels": labels("gpu-fresh"), "value": 0},
        ]}]}]}))
    result = subprocess.run([tool, "test", "rules", str(tests)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
