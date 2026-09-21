"""Low-overhead JSONL events for cross-layer Agent RL correlation."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
from typing import Any, Callable, Iterator, Mapping
import uuid


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class CorrelationContext:
    """Stable dimensions shared by metrics, events, logs, and artifacts."""

    run_id: str
    producer: str
    role: str
    worker_id: str
    node: str
    rank: int | None = None
    local_rank: int | None = None
    gpu: str | None = None

    def __post_init__(self) -> None:
        for name in ("run_id", "producer", "role", "worker_id", "node"):
            if not _IDENTIFIER.fullmatch(getattr(self, name)):
                raise ValueError(f"invalid {name}: {getattr(self, name)!r}")
        for name in ("rank", "local_rank"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be nonnegative")
        if self.gpu is not None and not _IDENTIFIER.fullmatch(self.gpu):
            raise ValueError(f"invalid gpu: {self.gpu!r}")

    @classmethod
    def from_env(
        cls,
        *,
        producer: str,
        role: str,
        worker_id: str | None = None,
    ) -> CorrelationContext:
        run_id = os.environ["TELEMETRY_RUN_ID"]
        rank = int(os.environ["RANK"]) if "RANK" in os.environ else None
        local_rank = int(os.environ["LOCAL_RANK"]) if "LOCAL_RANK" in os.environ else None
        visible = [
            device.strip()
            for device in os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",")
        ]
        gpu = None
        if local_rank is not None and local_rank < len(visible):
            candidate = visible[local_rank]
            if candidate and candidate != "-1":
                gpu = candidate
        return cls(
            run_id=run_id,
            producer=producer,
            role=role,
            worker_id=worker_id or str(rank if rank is not None else 0),
            node=os.environ.get("TELEMETRY_NODE", socket.gethostname()),
            rank=rank,
            local_rank=local_rank,
            gpu=gpu,
        )

    def as_dict(self) -> dict[str, str | int]:
        values = {
            "run_id": self.run_id,
            "producer": self.producer,
            "role": self.role,
            "worker_id": self.worker_id,
            "node": self.node,
            "rank": self.rank,
            "local_rank": self.local_rank,
            "gpu": self.gpu,
        }
        return {name: value for name, value in values.items() if value is not None}


@dataclass(frozen=True)
class SpanIdentity:
    trace_id: str
    span_id: str


class EventRecorder:
    """Append producer-owned phase spans and high-cardinality evidence to JSONL."""

    def __init__(
        self,
        directory: Path,
        context: CorrelationContext,
        *,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.directory = directory
        self.context = context
        self.clock_ns = clock_ns
        self.disabled = False
        self.path = directory / (
            f"{context.producer}-{context.role}-{context.worker_id}.jsonl"
        )

    @classmethod
    def from_env(
        cls,
        *,
        producer: str,
        role: str,
        worker_id: str | None = None,
    ) -> EventRecorder | None:
        directory = os.environ.get("TELEMETRY_EVENTS_DIR")
        run_id = os.environ.get("TELEMETRY_RUN_ID")
        if not directory and not run_id:
            return None
        if not directory or not run_id:
            print(
                "[events] TELEMETRY_EVENTS_DIR and TELEMETRY_RUN_ID must be set together",
                file=sys.stderr,
            )
            return None
        try:
            return cls(
                Path(directory),
                CorrelationContext.from_env(
                    producer=producer,
                    role=role,
                    worker_id=worker_id,
                ),
            )
        except ValueError as exc:
            print(f"[events] export disabled: {exc}", file=sys.stderr)
            return None

    def event(
        self,
        name: str,
        *,
        phase: str,
        step: int | None = None,
        attributes: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> None:
        self._validate(name=name, phase=phase, step=step)
        timestamp_ns = self.clock_ns()
        self._write(
            {
                "schema_version": 1,
                "record_type": "event",
                **self.context.as_dict(),
                "name": name,
                "phase": phase,
                "step": step,
                "timestamp_unix_nano": timestamp_ns,
                "trace_id": trace_id,
                "attributes": dict(attributes or {}),
            }
        )

    @contextmanager
    def span(
        self,
        name: str,
        *,
        phase: str,
        step: int | None = None,
        attributes: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> Iterator[SpanIdentity]:
        self._validate(name=name, phase=phase, step=step)
        identity = SpanIdentity(
            trace_id=trace_id or uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
        )
        started_ns = self.clock_ns()
        status = "ok"
        final_attributes = dict(attributes or {})
        try:
            yield identity
        except BaseException as exc:
            status = "error"
            final_attributes.setdefault("error_type", type(exc).__name__)
            raise
        finally:
            ended_ns = self.clock_ns()
            self._write(
                {
                    "schema_version": 1,
                    "record_type": "span",
                    **self.context.as_dict(),
                    "name": name,
                    "phase": phase,
                    "step": step,
                    "start_time_unix_nano": started_ns,
                    "end_time_unix_nano": ended_ns,
                    "duration_seconds": max(0, ended_ns - started_ns) / 1_000_000_000,
                    "status": status,
                    "trace_id": identity.trace_id,
                    "span_id": identity.span_id,
                    "attributes": final_attributes,
                }
            )

    @staticmethod
    def _validate(*, name: str, phase: str, step: int | None) -> None:
        if not _EVENT_NAME.fullmatch(name):
            raise ValueError(f"invalid event name: {name!r}")
        if not _EVENT_NAME.fullmatch(phase):
            raise ValueError(f"invalid phase: {phase!r}")
        if step is not None and (type(step) is not int or step < 0):
            raise ValueError("step must be a nonnegative integer or None")

    def _write(self, record: Mapping[str, Any]) -> None:
        if self.disabled:
            return
        try:
            line = json.dumps(record, separators=(",", ":"), sort_keys=True)
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except (OSError, TypeError, ValueError) as exc:
            print(f"[events] export disabled: {exc}", file=sys.stderr)
            self.disabled = True
