import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_server_config_registers_native_agent_rl_sources(tmp_path: Path) -> None:
    sources = tmp_path / "sources.json"
    sources.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "name": "rollout-0",
                        "kind": "vllm",
                        "target": "rollout.internal:8000",
                        "labels": {
                            "role": "rollout",
                            "node": "gpu-b",
                            "replica": "0",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "monitoring"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "run_telemetry.sh"), "server"],
        cwd=ROOT,
        env=os.environ
        | {
            "CLUSTER_NAME": "agent-rl",
            "TELEMETRY_TARGETS": "trainer-0=10.0.0.10",
            "TELEMETRY_SOURCES_FILE": str(sources),
            "SERVER_CONFIG_ONLY": "1",
            "OUTPUT_DIR": str(output),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    config = (output / "prometheus.yml").read_text(encoding="utf-8")
    assert "job_name: native" in config
    assert f"- '{output}/native-targets.json'" in config
    assert "replacement: agent-rl" in config
    discovery = json.loads(
        (output / "native-targets.json").read_text(encoding="utf-8")
    )
    assert discovery[0]["targets"] == ["rollout.internal:8000"]
    assert discovery[0]["labels"]["telemetry_source"] == "vllm"
    assert discovery[0]["labels"]["replica"] == "0"
