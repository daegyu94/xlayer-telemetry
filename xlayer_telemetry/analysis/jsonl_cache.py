"""Bounded process-local cache for producer-owned append-only JSONL files."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Iterator


@dataclass
class _Entry:
    identity: tuple[int, int]
    size: int
    stamp: tuple[int, int]
    offset: int
    prefix: bytes
    tail: bytes
    records: list[dict]


class JSONLCache:
    """Cache decoded records, never an authoritative or persistent index.

    Files must append complete newline-terminated records or be atomically
    replaced. Overflow falls back to a complete scan rather than losing evidence.
    """

    def __init__(self, *, max_records: int = 100_000, max_bytes: int = 64 * 1024 * 1024,
                 max_files: int = 128):
        for value in (max_records, max_bytes, max_files):
            if type(value) is not int or value <= 0:
                raise ValueError("JSONL cache limits must be positive integers")
        self.max_records, self.max_bytes, self.max_files = max_records, max_bytes, max_files
        self._entries: OrderedDict[Path, _Entry] = OrderedDict()
        self.parsed_bytes = self.cache_hits = self.reloads = self.uncached_scans = 0

    def _objects(self, stream) -> Iterator[dict]:
        while True:
            offset = stream.tell()
            line = stream.readline()
            if not line:
                return
            if not line.endswith(b"\n"):
                stream.seek(offset)
                return  # A split record is read again after its newline arrives.
            self.parsed_bytes += len(line)
            try:
                record = json.loads(line.decode("utf-8"))
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record

    def read(self, path: Path) -> Iterator[dict]:
        path = Path(path).absolute()
        try:
            stream = path.open("rb")
        except FileNotFoundError:
            self._entries.pop(path, None)
            raise
        with stream:
            stat = os.fstat(stream.fileno())
            entry = self._entries.pop(path, None)
            identity, stamp = (stat.st_dev, stat.st_ino), (stat.st_mtime_ns, stat.st_ctime_ns)
            if stat.st_size > self.max_bytes:
                self.uncached_scans += 1
                yield from self._objects(stream)
                return
            valid = entry is not None and entry.identity == identity and stat.st_size >= entry.size
            if valid and stat.st_size == entry.size and stamp != entry.stamp:
                valid = False  # Same-size rewrites (including touch) safely reload.
            if valid:
                prefix = stream.read(len(entry.prefix))
                stream.seek(max(0, entry.offset - len(entry.tail)))
                valid = prefix == entry.prefix and stream.read(len(entry.tail)) == entry.tail
            if valid:
                stream.seek(entry.offset)
                records = list(entry.records)
                self.cache_hits += 1
            else:
                stream.seek(0)
                records = []
                self.reloads += 1
            for record in self._objects(stream):
                records.append(record)
                if len(records) > self.max_records:
                    self.uncached_scans += 1
                    yield from records
                    yield from self._objects(stream)
                    return
            offset = stream.tell()
            stream.seek(0)
            prefix = stream.read(min(256, offset))
            stream.seek(max(0, offset - 256))
            tail = stream.read(min(256, offset))
        size = max(stat.st_size, offset)  # Include appends observed during parsing.
        if size > self.max_bytes:
            self.uncached_scans += 1
            yield from records
            return
        entry = _Entry(identity, size, stamp, offset, prefix, tail, records)
        # Evict older files, not records within a file: every query remains complete.
        while self._entries and (len(self._entries) >= self.max_files
                or sum(len(e.records) for e in self._entries.values()) + len(records) > self.max_records
                or sum(e.size for e in self._entries.values()) + entry.size > self.max_bytes):
            self._entries.popitem(last=False)
        self._entries[path] = entry
        yield from records

    def stats(self) -> dict:
        return {"cached_files": len(self._entries),
                "cached_records": sum(len(e.records) for e in self._entries.values()),
                "cached_source_bytes": sum(e.size for e in self._entries.values()),
                "parsed_bytes": self.parsed_bytes, "cache_hits": self.cache_hits,
                "reloads": self.reloads, "uncached_scans": self.uncached_scans}


def from_config(config: dict) -> JSONLCache | None:
    settings = config.get("jsonl_cache", {})
    if not isinstance(settings, dict) or type(settings.get("enabled", True)) is not bool:
        raise ValueError("jsonl_cache must be an object with boolean enabled")
    cache = JSONLCache(**{key: settings[key] for key in ("max_records", "max_bytes", "max_files") if key in settings})
    return cache if settings.get("enabled", True) else None
