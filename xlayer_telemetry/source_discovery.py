"""Validate native metric endpoints and write Prometheus file discovery."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

from .fileio import atomic_write_text


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_TARGET = re.compile(r"^(?:[A-Za-z0-9_.-]+|\[[0-9A-Fa-f:]+\]):[0-9]{1,5}$")
_LABEL = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
MAX_SOURCES = 1024
MAX_SOURCE_LABELS = 16
MAX_LABEL_VALUE_BYTES = 256
MAX_CONFIG_BYTES = 1024 * 1024
_RESERVED_LABELS = {
    "__address__",
    "__metrics_path__",
    "__scheme__",
    "component",
    "telemetry_source",
    "job",
}


def build_file_discovery(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(config, Mapping):
        raise ValueError("source config must be an object")
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported schema_version")
    sources = config.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources must be a non-empty list")
    if len(sources) > MAX_SOURCES:
        raise ValueError(f"sources exceeds the {MAX_SOURCES}-endpoint limit; shard discovery")

    names: set[str] = set()
    endpoints: set[tuple[str, str, str]] = set()
    groups = []
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("each source must be an object")
        name = source.get("name")
        kind = source.get("kind")
        target = source.get("target")
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            raise ValueError(f"invalid source name: {name!r}")
        if name in names:
            raise ValueError(f"duplicate source name: {name}")
        names.add(name)
        if not isinstance(kind, str) or not _IDENTIFIER.fullmatch(kind):
            raise ValueError(f"invalid source kind: {kind!r}")
        if not isinstance(target, str) or len(target) > 320 or not _TARGET.fullmatch(target):
            raise ValueError(f"invalid source target: {target!r}")
        port = int(target.rsplit(":", 1)[1])
        if not 1 <= port <= 65535:
            raise ValueError(f"invalid source target port: {target!r}")
        scheme = source.get("scheme", "http")
        if scheme not in {"http", "https"}:
            raise ValueError(f"invalid source scheme: {scheme!r}")
        metrics_path = source.get("metrics_path", "/metrics")
        if (
            not isinstance(metrics_path, str)
            or not metrics_path.startswith("/")
            or len(metrics_path) > 1024
            or any(character in metrics_path for character in "?#")
            or any(character.isspace() for character in metrics_path)
        ):
            raise ValueError(f"invalid metrics_path: {metrics_path!r}")
        user_labels = source.get("labels", {})
        if not isinstance(user_labels, dict):
            raise ValueError("source labels must be an object")
        if len(user_labels) > MAX_SOURCE_LABELS:
            raise ValueError(f"source labels exceeds the {MAX_SOURCE_LABELS}-label limit")
        if any(
            not isinstance(label, str)
            or not _LABEL.fullmatch(label)
            or len(label) > 128
            or label.startswith("__")
            or label in _RESERVED_LABELS
            for label in user_labels
        ):
            raise ValueError(f"invalid or reserved source label in {name}")
        if any(not isinstance(value, (str, int, float, bool))
               or (isinstance(value, float) and not math.isfinite(value))
               or len(str(value).encode("utf-8")) > MAX_LABEL_VALUE_BYTES
               for value in user_labels.values()):
            raise ValueError(f"source label values must be scalars up to {MAX_LABEL_VALUE_BYTES} bytes")
        endpoint = (scheme, target.lower(), metrics_path)
        if endpoint in endpoints:
            raise ValueError(f"duplicate scrape endpoint in {name}; register an exporter once")
        endpoints.add(endpoint)
        labels = {
            "component": name,
            "telemetry_source": kind,
            "__scheme__": scheme,
            "__metrics_path__": metrics_path,
            **{label: str(value) for label, value in user_labels.items()},
        }
        groups.append({"targets": [target], "labels": labels})
    return groups


def write_file_discovery(path: Path, groups: list[dict[str, Any]]) -> Path:
    atomic_write_text(path, json.dumps(groups, indent=2, sort_keys=True) + "\n")
    return path


def load_file_discovery(path: Path) -> list[dict[str, Any]]:
    """Read registration with a fixed input budget before decoding JSON."""
    with path.open("rb") as stream:
        body = stream.read(MAX_CONFIG_BYTES + 1)
    if len(body) > MAX_CONFIG_BYTES:
        raise ValueError("source config exceeds the 1 MiB limit")
    return build_file_discovery(json.loads(body))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        write_file_discovery(args.output, load_file_discovery(args.input))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
