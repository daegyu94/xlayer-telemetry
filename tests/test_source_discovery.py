import json
from pathlib import Path

import pytest

from post_training_telemetry.source_discovery import (
    build_file_discovery,
    write_file_discovery,
)


def test_native_sources_become_prometheus_file_discovery(tmp_path: Path) -> None:
    groups = build_file_discovery(
        {
            "schema_version": 1,
            "sources": [
                {
                    "name": "rollout-0",
                    "kind": "vllm",
                    "target": "gpu-b:8000",
                    "labels": {"role": "rollout", "node": "gpu-b"},
                },
                {
                    "name": "ray-head",
                    "kind": "ray",
                    "target": "10.0.0.10:8080",
                    "metrics_path": "/metrics",
                },
            ],
        }
    )

    path = write_file_discovery(tmp_path / "native-targets.json", groups)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded[0] == {
        "labels": {
            "__metrics_path__": "/metrics",
            "__scheme__": "http",
            "component": "rollout-0",
            "node": "gpu-b",
            "role": "rollout",
            "telemetry_source": "vllm",
        },
        "targets": ["gpu-b:8000"],
    }
    assert loaded[1]["labels"]["telemetry_source"] == "ray"


@pytest.mark.parametrize(
    "source",
    [
        {"name": "bad name", "kind": "vllm", "target": "gpu:8000"},
        {"name": "x", "kind": "vllm", "target": "http://gpu:8000"},
        {
            "name": "x",
            "kind": "vllm",
            "target": "gpu:8000",
            "labels": {"component": "override"},
        },
    ],
)
def test_native_source_rejects_ambiguous_or_reserved_values(source: dict) -> None:
    with pytest.raises(ValueError):
        build_file_discovery({"schema_version": 1, "sources": [source]})
