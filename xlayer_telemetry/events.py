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

from .identity import producer_filename_stem
from .io_writer import BoundedWriter, settings_from_env
from .time_alignment import CalibrationCache


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
        if local_rank is not None and 0 <= local_rank < len(visible):
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
        monotonic_ns: Callable[[], int] | None = None,
        async_io: bool = False,
        queue_capacity: int = 256,
        max_queue_bytes: int = 4 * 1024 * 1024,
        flush_timeout: float = 1,
        time_calibration: CalibrationCache | None = None,
    ) -> None:
        self.directory = directory
        self.context = context
        self.clock_ns = clock_ns
        self.time_calibration = time_calibration or CalibrationCache.from_env(context.node)
        self.monotonic_ns = monotonic_ns or (time.monotonic_ns if clock_ns is time.time_ns else clock_ns)
        self.disabled = False
        self.path = directory / (producer_filename_stem(
            context.producer, context.role, context.worker_id,
            node=context.node, run_id=context.run_id) + ".jsonl")
        if type(async_io) is not bool:
            raise ValueError("async_io must be boolean")
        self._writer = BoundedWriter(self._persist, self._disable, capacity=queue_capacity,
                                     max_bytes=max_queue_bytes, flush_timeout=flush_timeout) if async_io else None

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
            cls._warn(
                "[events] TELEMETRY_EVENTS_DIR and TELEMETRY_RUN_ID must be set together"
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
                **settings_from_env(),
            )
        except ValueError as exc:
            cls._warn(f"[events] export disabled: {exc}")
            return None

    def event(
        self,
        name: str,
        *,
        phase: str,
        step: int | None = None,
        attributes: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
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
                "event_time_unix_nano": timestamp_ns,
                "trace_id": trace_id,
                **({"span_id": span_id} if span_id is not None else {}),
                "attributes": dict(attributes or {}),
                **self._aligned_fields(timestamp_ns, timestamp_ns),
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
        parent_span_id: str | None = None,
    ) -> Iterator[SpanIdentity]:
        self._validate(name=name, phase=phase, step=step)
        identity = SpanIdentity(
            trace_id=trace_id or uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
        )
        started_ns = self.clock_ns()
        started_mono = self.monotonic_ns() if self.monotonic_ns is not self.clock_ns else started_ns
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
            ended_mono = self.monotonic_ns() if self.monotonic_ns is not self.clock_ns else ended_ns
            elapsed_ns = max(0, ended_mono - started_mono)
            discontinuity = abs((ended_ns - started_ns) - elapsed_ns) > 1_000_000_000
            self._write(
                {
                    "schema_version": 1,
                    "record_type": "span",
                    **self.context.as_dict(),
                    "name": name,
                    "phase": phase,
                    "span_boundary_label": phase + (" [clock discontinuity]" if discontinuity else " [exact, node clock]"),
                    "step": step,
                    "start_time_unix_nano": started_ns,
                    "event_time_unix_nano": started_ns,
                    "end_time_unix_nano": ended_ns,
                    "start_time_ms": started_ns // 1_000_000,
                    "end_time_ms": (ended_ns + 999_999) // 1_000_000,
                    "boundary_accuracy": "clock_discontinuity" if discontinuity else "exact",
                    "clock_scope": "node",
                    "duration_source": "monotonic" if self.monotonic_ns is not self.clock_ns else "injected_clock",
                    "duration_seconds": elapsed_ns / 1_000_000_000,
                    "status": status,
                    "trace_id": identity.trace_id,
                    "span_id": identity.span_id,
                    **({"parent_span_id": parent_span_id} if parent_span_id is not None else {}),
                    "attributes": final_attributes,
                    **self._aligned_fields(started_ns, ended_ns, discontinuity=discontinuity, phase=phase),
                }
            )

    def _aligned_fields(self, start: int, end: int, *, discontinuity: bool = False, phase: str = "") -> dict:
        if self.time_calibration is None:
            return {}
        mapped = self.time_calibration.project(start/1e9, end/1e9)
        alignment = mapped["time_alignment"]
        if discontinuity:
            alignment = {"status": "unknown", "issue": "local_clock_discontinuity"}
        fields = {"time_alignment": alignment}
        if alignment["status"] == "aligned":
            began, finished = round(mapped["start"]*1e9), round(mapped["end"]*1e9)
            fields.update(event_time_unix_nano=began, correlation_start_time_unix_nano=began,
                          correlation_end_time_unix_nano=finished,
                          start_time_ms=began//1_000_000, end_time_ms=(finished+999_999)//1_000_000,
                          boundary_accuracy="calibrated", clock_scope="monitoring_reference")
            fields.update(time_reference=alignment["reference_id"], time_uncertainty_seconds=alignment["uncertainty_seconds"])
            fields["span_boundary_label"] = f"{phase} [calibrated ±{alignment['uncertainty_seconds']:.3g}s]"
        elif not discontinuity:
            fields.update(boundary_accuracy="unknown", start_time_ms=None, end_time_ms=None)
        return fields

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
            if self._writer is not None:
                self._writer.submit(line + "\n")
            else:
                self._persist(line + "\n")
        except (OSError, TypeError, ValueError) as exc:
            self._disable(exc)

    def _persist(self, line: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(line)

    def _disable(self, error) -> None:
        self.disabled = True
        self._warn(f"[events] export disabled: {error}")

    @staticmethod
    def _warn(message: str) -> None:
        try:
            print(message, file=sys.stderr)
        except (OSError, ValueError):
            pass  # A closed log stream must not replace the workload exception.

    def io_status(self) -> dict:
        return self._writer.status() if self._writer is not None else {"mode": "synchronous", "disabled": self.disabled}

    def flush(self, timeout=None) -> bool:
        return self._writer.flush(timeout) if self._writer is not None else True

    def close(self, timeout=None) -> bool:
        return self._writer.close(timeout) if self._writer is not None else True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
