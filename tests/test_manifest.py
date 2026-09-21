import json
from pathlib import Path

from post_training_telemetry.manifest import make_agent_rl_manifest, write_manifest


def test_manifest_links_deployment_sources_and_artifacts(tmp_path: Path) -> None:
    manifest = make_agent_rl_manifest(
        run_id="grpo-001",
        roles={"trainer": "gpu-a", "rollout": "gpu-b"},
        sources={
            "ray": "http://gpu-a:8080",
            "vllm": "http://gpu-b:8000/metrics",
        },
        artifacts={
            "events": "/runs/grpo-001/telemetry-events",
            "profiles": "/runs/grpo-001/profiles",
        },
        configuration={"model_id": "Qwen/Qwen2.5-0.5B-Instruct"},
        created_at="2026-09-21T00:00:00+00:00",
    )

    path = write_manifest(tmp_path / "telemetry-manifest.json", manifest)
    loaded = json.loads(path.read_text(encoding="utf-8"))

    assert loaded["run_id"] == "grpo-001"
    assert loaded["deployment"]["roles"] == [
        {"node": "gpu-b", "role": "rollout"},
        {"node": "gpu-a", "role": "trainer"},
    ]
    assert loaded["sources"]["vllm"].endswith("/metrics")
    assert loaded["configuration"]["model_id"].endswith("0.5B-Instruct")
    assert {"run_id", "phase", "node", "gpu", "rank", "time"} <= set(
        loaded["correlation_keys"]
    )
    assert not list(tmp_path.glob(".*.tmp"))


def test_manifest_allows_one_role_on_multiple_nodes() -> None:
    manifest = make_agent_rl_manifest(
        run_id="grpo-multi-node",
        roles=[
            ("trainer", "gpu-a"),
            ("rollout", "gpu-b"),
            ("rollout", "gpu-c"),
        ],
    )

    assert manifest["deployment"]["roles"] == [
        {"node": "gpu-b", "role": "rollout"},
        {"node": "gpu-c", "role": "rollout"},
        {"node": "gpu-a", "role": "trainer"},
    ]
