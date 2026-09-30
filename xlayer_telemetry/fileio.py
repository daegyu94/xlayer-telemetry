"""Shared file primitives for producer snapshots and append-only telemetry."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, TextIO
import uuid


@contextmanager
def atomic_text_writer(path: Path) -> Iterator[TextIO]:
    """Replace a snapshot with a unique temporary file, even within one PID.

    A short temporary basename also accommodates long producer identities.
    New files retain the normal 0666/umask permission policy of Path.write_text.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".xlayer-{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            yield stream
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    with atomic_text_writer(path) as stream:
        stream.write(text)


def json_objects(path: Path) -> Iterator[dict[str, Any]]:
    """Stream JSONL objects, skipping malformed lines and other JSON values."""
    with path.open("rb") as stream:
        for line in stream:
            try:
                value = json.loads(line.decode("utf-8"))
            except ValueError:
                continue
            if isinstance(value, dict):
                yield value
