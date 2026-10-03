import json
from pathlib import Path

import pytest

from xlayer_telemetry.source_discovery import (
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


@pytest.mark.parametrize('labels', [
    {'__scrape_interval__': '1ms'}, {'__param_target': 'internal'},
    {'job': 'bypass-native-selection'}, {'bad': float('nan')},
    {'long': '한' * 100}, {'nested': {'id': 'x'}},
    {f'label{i}': 'x' for i in range(17)},
])
def test_source_labels_cannot_override_scrape_budgets_or_expand_unboundedly(labels):
    with pytest.raises(ValueError):
        build_file_discovery({'schema_version': 1, 'sources': [
            {'name': 'test', 'kind': 'ray', 'target': 'node:8080', 'labels': labels}]})


def test_duplicate_exporter_is_not_scraped_twice_with_different_labels():
    with pytest.raises(ValueError, match='duplicate scrape endpoint'):
        build_file_discovery({'schema_version': 1, 'sources': [
            {'name': 'a', 'kind': 'ray', 'target': 'node:8080'},
            {'name': 'b', 'kind': 'ray', 'target': 'NODE:8080', 'labels': {'role': 'trainer'}},
        ]})


def test_source_count_and_path_are_bounded():
    sources = [{'name': f'ray-{i}', 'kind': 'ray', 'target': f'node-{i}:8080'} for i in range(1025)]
    with pytest.raises(ValueError, match='endpoint limit'):
        build_file_discovery({'schema_version': 1, 'sources': sources})
    for path in ('/metrics?' + 'x' * 5, '/metrics#fragment', '/' + 'x' * 1024):
        with pytest.raises(ValueError, match='metrics_path'):
            build_file_discovery({'schema_version': 1, 'sources': [dict(sources[0], metrics_path=path)]})


def test_source_file_size_is_checked_before_json_decode(tmp_path):
    from xlayer_telemetry.source_discovery import load_file_discovery
    path = tmp_path / "sources.json"
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="1 MiB"):
        load_file_discovery(path)
