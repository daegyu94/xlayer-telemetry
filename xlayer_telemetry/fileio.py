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


def append_jsonl(path: Path, line: str, *, mode: int = 0o666) -> None:
    """Keep a restarted append separate from a crash-truncated final record.

    Preserve the old bytes: a complete JSON object without a newline remains
    readable, while a partial object is skipped by readers. POSIX writers lock
    the repair and append together so concurrent producers cannot join rows.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, mode)
    with os.fdopen(descriptor, "a+b") as stream:
        if os.name == "posix":
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            end = stream.seek(0, os.SEEK_END)
            separator = b""
            if end:
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b"\n":
                    separator = b"\n"
            encoded = line.encode("utf-8")
            stream.write(separator + encoded + (b"" if encoded.endswith(b"\n") else b"\n"))
            stream.flush()
        finally:
            if os.name == "posix":
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class IncompleteJSONL(ValueError):
    """A source cannot establish complete observation coverage."""


def json_objects(path: Path, *, strict: bool = False) -> Iterator[dict[str, Any]]:
    """Stream objects; opt-in strict readers require complete, valid rows."""
    with path.open("rb") as stream:
        for line in stream:
            if strict and not line.endswith(b"\n"):
                raise IncompleteJSONL("unterminated_jsonl_record")
            try:
                value = json.loads(line.decode("utf-8"))
            except (ValueError, RecursionError):
                if strict:
                    raise IncompleteJSONL("invalid_jsonl_record") from None
                continue
            if isinstance(value, dict):
                yield value
            elif strict:
                raise IncompleteJSONL("non_object_jsonl_record")
