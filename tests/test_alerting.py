import json
import os
import subprocess
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
