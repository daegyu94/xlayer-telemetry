import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/run_telemetry.sh"


def test_server_config_keeps_optional_features_on_restart(tmp_path: Path) -> None:
    loki = tmp_path / "loki"
    loki.write_text("#!/usr/bin/env bash\nexit 0\n")
    loki.chmod(0o755)
    output = tmp_path / "state"
    config = tmp_path / "server.conf"
    config.write_text(
        f"OUTPUT_DIR='{output}'\n"
        "CLUSTER_NAME='training-cluster'\n"
        "TELEMETRY_TARGETS='gpu-local=127.0.0.1'\n"
        "ENABLE_ALERTS=1\n"
        "ENABLE_LOGS=1\n"
        f"LOKI='{loki}'\n"
    )
    environment = os.environ | {
        "SERVER_CONFIG_ONLY": "1",
        "ENABLE_ALERTS": "0",
        "ENABLE_LOGS": "0",
    }
    for _ in range(2):
        result = subprocess.run(
            ["bash", str(SCRIPT), "server", "--config", "server.conf"],
            cwd=tmp_path, env=environment, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        rules = json.loads((output / "provisioning/alerting/operations.json").read_text())
        assert len(rules["groups"][0]["rules"]) == 3
        assert "uid: telemetry-loki" in (output / "provisioning/datasources/default.yaml").read_text()
        assert (output / "dashboards/run-logs.json").is_file()


def test_server_config_rejects_missing_file_and_other_roles(tmp_path: Path) -> None:
    missing = subprocess.run(
        ["bash", str(SCRIPT), "server", "--config", "missing.conf"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert missing.returncode == 2
    assert "Server config file not found" in missing.stderr

    other_role = subprocess.run(
        ["bash", str(SCRIPT), "node", "--config", "server.conf"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert other_role.returncode == 2
    assert "Usage: run_telemetry.sh server" in other_role.stderr
