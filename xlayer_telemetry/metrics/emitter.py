"""Portable, best-effort application metric snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
from typing import Callable, Iterable, Mapping

from xlayer_telemetry.identity import producer_filename_stem
from xlayer_telemetry.fileio import atomic_write_text
from xlayer_telemetry.measurements import finite_number
from xlayer_telemetry.io_writer import BoundedWriter, settings_from_env
from xlayer_telemetry.time_alignment import CalibrationCache


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_METRIC_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_CONTEXT_LABELS = {
    "run_id",
    "producer",
    "role",
    "worker_id",
    "node",
    "rank",
    "local_rank",
    "gpu",
}


@dataclass(frozen=True)
class Metric:
    name: str
    value: float
    kind: str = "gauge"
    labels: Mapping[str, str] = field(default_factory=dict)


class MetricEmitter:
    """Best-effort latest snapshots, optionally with background filesystem I/O."""

    def __init__(
        self,
        directory: Path,
        *,
        run_id: str,
        producer: str,
        role: str,
        worker_id: str,
        node: str | None = None,
        rank: int | None = None,
        local_rank: int | None = None,
        gpu: str | None = None,
        cuda_visible_devices: str | None = None,
        clock: Callable[[], float] = time.time,
        async_io: bool = False,
        queue_capacity: int = 256,
        max_queue_bytes: int = 4 * 1024 * 1024,
        flush_timeout: float = 1,
        time_calibration: CalibrationCache | None = None,
    ) -> None:
        identifiers = {"run_id": run_id, "producer": producer, "role": role, "worker_id": worker_id}
        for name, value in identifiers.items():
            if not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"invalid {name}: {value!r}")
        for name, value in (("rank", rank), ("local_rank", local_rank)):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be nonnegative")
        if gpu is not None and not _IDENTIFIER.fullmatch(gpu):
            raise ValueError(f"invalid gpu: {gpu!r}")
        self.directory = directory
        self.run_id = run_id
        self.producer = producer
        self.role = role
        self.worker_id = worker_id
        self.node = node or socket.gethostname()
        if not _IDENTIFIER.fullmatch(self.node):
            raise ValueError(f"invalid node: {self.node!r}")
        self.rank = rank
        self.local_rank = local_rank
        self.gpu = gpu
        self.cuda_visible_devices = cuda_visible_devices
        self.clock = clock
        self.time_calibration = time_calibration or CalibrationCache.from_env(self.node)
        self.disabled = False
        if type(async_io) is not bool:
            raise ValueError("async_io must be boolean")
        self._writer = BoundedWriter(self._persist, self._disable, capacity=queue_capacity,
                                     max_bytes=max_queue_bytes, flush_timeout=flush_timeout, latest_only=True) if async_io else None

    @classmethod
    def from_env(
        cls,
        *,
        producer: str,
        role: str,
        worker_id: str | None = None,
    ) -> MetricEmitter | None:
        directory = os.environ.get("TELEMETRY_METRICS_DIR")
        run_id = os.environ.get("TELEMETRY_RUN_ID")
        if not directory and not run_id:
            return None
        if not directory or not run_id:
            cls._warn(
                "[metrics] TELEMETRY_METRICS_DIR and TELEMETRY_RUN_ID must be set together"
            )
            return None
        try:
            rank = int(os.environ["RANK"]) if "RANK" in os.environ else None
            local_rank = int(os.environ["LOCAL_RANK"]) if "LOCAL_RANK" in os.environ else None
            visible_devices = os.environ.get("CUDA_VISIBLE_DEVICES")
            visible = [device.strip() for device in (visible_devices or "").split(",")]
            gpu = None
            if local_rank is not None and 0 <= local_rank < len(visible):
                candidate = visible[local_rank]
                if candidate and candidate != "-1":
                    gpu = candidate
            return cls(
                Path(directory),
                run_id=run_id,
                producer=producer,
                role=role,
                worker_id=worker_id or str(rank if rank is not None else 0),
                node=os.environ.get("TELEMETRY_NODE"),
                rank=rank,
                local_rank=local_rank,
                gpu=gpu,
                cuda_visible_devices=visible_devices,
                **settings_from_env(),
            )
        except ValueError as exc:
            cls._warn(f"[metrics] export disabled: {exc}")
            return None

    def emit(self, *, step: int | None, samples: Iterable[Metric]) -> Path | None:
        if self.disabled:
            return None
        try:
            if step is not None and (type(step) is not int or step < 0):
                raise ValueError("step must be a nonnegative integer or None")
            encoded = [self._encode(sample) for sample in samples]
            if not encoded:
                raise ValueError("at least one metric is required")
            snapshot = {
                "schema_version": 2,
                "run_id": self.run_id,
                "producer": self.producer,
                "role": self.role,
                "worker_id": self.worker_id,
                "node": self.node,
                "rank": self.rank,
                "local_rank": self.local_rank,
                "gpu": self.gpu,
                "cuda_visible_devices": self.cuda_visible_devices,
                "step": step,
                "observed_at": self.clock(),
                "samples": encoded,
            }
            if self.time_calibration is not None:
                mapping = self.time_calibration.project(snapshot["observed_at"], snapshot["observed_at"])
                snapshot["time_alignment"] = mapping["time_alignment"]
                snapshot["correlation_observed_at"] = mapping["end"] if mapping["time_alignment"]["status"] == "aligned" else None
            filename = producer_filename_stem(self.producer, self.role, self.worker_id,
                                              node=self.node, run_id=self.run_id) + ".json"
            destination = self.directory / filename
            encoded = json.dumps(snapshot, separators=(",", ":"), allow_nan=False) + "\n"
            if self._writer is not None:
                if not self._writer.submit((destination, encoded)):
                    return None
            else:
                self._persist((destination, encoded))
            return destination
        except (OSError, ValueError, TypeError, AttributeError, OverflowError) as exc:
            self._disable(exc)
            return None

    @staticmethod
    def _persist(item) -> None:
        destination, encoded = item
        atomic_write_text(destination, encoded)

    def _disable(self, error) -> None:
        self.disabled = True
        self._warn(f"[metrics] export disabled: {error}")

    @staticmethod
    def _warn(message: str) -> None:
        try:
            print(message, file=sys.stderr)
        except (OSError, ValueError):
            pass  # Export failure reporting is best-effort as well.

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

    @staticmethod
    def _encode(metric: Metric) -> dict[str, object]:
        if not _METRIC_NAME.fullmatch(metric.name):
            raise ValueError(f"invalid metric name: {metric.name!r}")
        if metric.kind not in {"gauge", "counter"}:
            raise ValueError(f"invalid metric kind: {metric.kind!r}")
        if finite_number(metric.value) is None:
            raise ValueError(f"metric {metric.name!r} must be finite")
        if metric.kind == "counter" and metric.value < 0:
            raise ValueError(f"counter {metric.name!r} must be nonnegative")
        if any(not _LABEL_NAME.fullmatch(name) for name in metric.labels):
            raise ValueError(f"metric {metric.name!r} has an invalid label name")
        if set(metric.labels) & _CONTEXT_LABELS:
            raise ValueError(f"metric {metric.name!r} overrides a context label")
        return {
            "name": metric.name,
            "kind": metric.kind,
            "value": metric.value,
            "labels": {name: str(value) for name, value in metric.labels.items()},
        }
