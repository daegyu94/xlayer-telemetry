"""Validate native metric endpoints and write Prometheus file discovery."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_TARGET = re.compile(r"^(?:[A-Za-z0-9_.-]+|\[[0-9A-Fa-f:]+\]):[0-9]{1,5}$")
_LABEL = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_RESERVED_LABELS = {
    "__address__",
    "__metrics_path__",
    "__scheme__",
    "component",
    "telemetry_source",
}


def build_file_discovery(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported schema_version")
    sources = config.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources must be a non-empty list")

    names: set[str] = set()
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
        if not isinstance(target, str) or not _TARGET.fullmatch(target):
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
            or any(character.isspace() for character in metrics_path)
        ):
            raise ValueError(f"invalid metrics_path: {metrics_path!r}")
        user_labels = source.get("labels", {})
        if not isinstance(user_labels, dict):
            raise ValueError("source labels must be an object")
        if any(
            not isinstance(label, str)
            or not _LABEL.fullmatch(label)
            or label in _RESERVED_LABELS
            for label in user_labels
        ):
            raise ValueError(f"invalid or reserved source label in {name}")
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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(groups, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        config = json.loads(args.input.read_text(encoding="utf-8"))
        write_file_discovery(args.output, build_file_discovery(config))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
