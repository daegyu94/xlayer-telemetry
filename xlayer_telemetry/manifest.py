"""Create an Agent RL run manifest that links telemetry sources and artifacts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
CORRELATION_KEYS = [
    "run_id",
    "role",
    "phase",
    "node",
    "gpu",
    "rank",
    "worker_id",
    "time",
]


def _pairs(values: Iterable[str], *, option: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        name, separator, item = value.partition("=")
        if not separator or not _IDENTIFIER.fullmatch(name) or not item:
            raise ValueError(f"{option} values must use NAME=VALUE: {value!r}")
        if name in parsed:
            raise ValueError(f"duplicate {option} name: {name}")
        parsed[name] = item
    return parsed


def _role_assignments(values: Iterable[str]) -> list[tuple[str, str]]:
    parsed = []
    seen = set()
    for value in values:
        role, separator, node = value.partition("=")
        assignment = (role, node)
        if (
            not separator
            or not _IDENTIFIER.fullmatch(role)
            or not _IDENTIFIER.fullmatch(node)
        ):
            raise ValueError(f"--role values must use ROLE=NODE: {value!r}")
        if assignment in seen:
            raise ValueError(f"duplicate --role assignment: {value}")
        seen.add(assignment)
        parsed.append(assignment)
    return parsed


def _normalize_roles(
    roles: Mapping[str, str] | Iterable[tuple[str, str]] | None,
) -> list[tuple[str, str]]:
    assignments = roles.items() if isinstance(roles, Mapping) else (roles or [])
    normalized = []
    for role, node in assignments:
        if not _IDENTIFIER.fullmatch(role) or not _IDENTIFIER.fullmatch(node):
            raise ValueError(f"invalid role assignment: {role!r}={node!r}")
        normalized.append((role, node))
    if len(set(normalized)) != len(normalized):
        raise ValueError("duplicate role assignment")
    return sorted(normalized)


def make_agent_rl_manifest(
    *,
    run_id: str,
    roles: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
    sources: Mapping[str, str] | None = None,
    artifacts: Mapping[str, str] | None = None,
    configuration: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    if not _IDENTIFIER.fullmatch(run_id):
        raise ValueError(f"invalid run_id: {run_id!r}")
    return {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "correlation_keys": CORRELATION_KEYS,
        "deployment": {
            "roles": [
                {"role": role, "node": node}
                for role, node in _normalize_roles(roles)
            ]
        },
        "sources": dict(sorted((sources or {}).items())),
        "artifacts": dict(sorted((artifacts or {}).items())),
        "configuration": dict(configuration or {}),
    }


def write_manifest(path: Path, manifest: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def _environment_sources() -> dict[str, str]:
    names = {
        "rl_insight": "RL_INSIGHT_SERVER_URL",
        "ray": "RAY_ADDRESS",
    }
    return {
        name: os.environ[variable]
        for name, variable in names.items()
        if os.environ.get(variable)
    }


def _environment_artifacts() -> dict[str, str]:
    names = {
        "metrics": "TELEMETRY_METRICS_DIR",
        "events": "TELEMETRY_EVENTS_DIR",
        "verl_file_log": "VERL_FILE_LOGGER_PATH",
    }
    return {
        name: os.environ[variable]
        for name, variable in names.items()
        if os.environ.get(variable)
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-id", default=os.environ.get("TELEMETRY_RUN_ID"))
    parser.add_argument("--role", action="append", default=[], metavar="ROLE=NODE")
    parser.add_argument("--source", action="append", default=[], metavar="NAME=ENDPOINT")
    parser.add_argument("--artifact", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--set", action="append", default=[], dest="settings", metavar="NAME=VALUE")
    args = parser.parse_args()
    if not args.run_id:
        parser.error("--run-id or TELEMETRY_RUN_ID is required")
    try:
        sources = _environment_sources() | _pairs(args.source, option="--source")
        artifacts = _environment_artifacts() | _pairs(args.artifact, option="--artifact")
        manifest = make_agent_rl_manifest(
            run_id=args.run_id,
            roles=_role_assignments(args.role),
            sources=sources,
            artifacts=artifacts,
            configuration=_pairs(args.settings, option="--set"),
        )
        write_manifest(args.output, manifest)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
