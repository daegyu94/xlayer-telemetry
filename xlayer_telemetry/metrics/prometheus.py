"""Write metrics for the Prometheus Node Exporter textfile collector."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from xlayer_telemetry.fileio import atomic_write_text
from xlayer_telemetry.measurements import finite_number


_METRIC_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


@dataclass(frozen=True)
class GaugeSample:
    name: str
    help: str
    value: float
    labels: Mapping[str, str] = field(default_factory=dict)
    kind: str = "gauge"


def _escape_help(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n")


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def validate_sample(sample: GaugeSample) -> None:
    """Validate one sample without formatting an entire exposition."""
    if not isinstance(sample.name, str) or not _METRIC_NAME.fullmatch(sample.name):
        raise ValueError(f"invalid metric name: {sample.name}")
    if sample.kind not in {"gauge", "counter"}:
        raise ValueError(f"invalid metric kind: {sample.kind}")
    if not isinstance(sample.help, str) or finite_number(sample.value) is None:
        raise ValueError(f"invalid measurement for metric: {sample.name}")
    if sample.kind == "counter" and sample.value < 0:
        raise ValueError(f"negative counter: {sample.name}")
    for label in sample.labels:
        if not isinstance(label, str) or not _LABEL_NAME.fullmatch(label):
            raise ValueError(f"invalid label name: {label}")
        str(sample.labels[label]).encode("utf-8")


def format_gauges(samples: Iterable[GaugeSample]) -> str:
    """Return Prometheus text exposition for metric samples."""
    definitions: dict[str, tuple[str, str]] = {}
    materialized = list(samples)
    for sample in materialized:
        validate_sample(sample)
        definition = (sample.help, sample.kind)
        if sample.name in definitions and definitions[sample.name] != definition:
            raise ValueError(f"inconsistent definition for metric: {sample.name}")
        definitions[sample.name] = definition

    lines: list[str] = []
    emitted: set[str] = set()
    for sample in materialized:
        if sample.name not in emitted:
            lines.append(f"# HELP {sample.name} {_escape_help(sample.help)}")
            lines.append(f"# TYPE {sample.name} {sample.kind}")
            emitted.add(sample.name)
        label_text = ""
        if sample.labels:
            pairs = [f'{key}="{_escape_label(str(value))}"' for key, value in sorted(sample.labels.items())]
            label_text = "{" + ",".join(pairs) + "}"
        lines.append(f"{sample.name}{label_text} {sample.value}")

    return "\n".join(lines) + "\n"


def write_gauges(directory: Path, filename: str, samples: Iterable[GaugeSample]) -> Path:
    """Atomically replace one producer-owned ``.prom`` file."""
    if Path(filename).name != filename or not filename.endswith(".prom"):
        raise ValueError("filename must be a basename ending in .prom")
    destination = directory / filename
    atomic_write_text(destination, format_gauges(samples))
    return destination
