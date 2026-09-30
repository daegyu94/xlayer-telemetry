"""Append deduplicated VERL step observations for offline diagnosis."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping

from .fileio import json_objects
from .measurements import finite_number


def _duration(data: Mapping[str, Any]) -> float | None:
    for key in ("perf/time_per_step", "timing_s/step"):
        value = data.get(key)
        if finite_number(value) is not None and value >= 0:
            return float(value)
    return None


def dashboard_fields(start: float | None, end: float | None, stages: Mapping[str, float], accuracy: str) -> dict[str, Any]:
    """Flatten a step window for Grafana's Loki table and data links."""
    return {
        "stage_summary": " · ".join(
            f"{name}: {seconds:.2f}s"
            for name, seconds in sorted(stages.items(), key=lambda item: -item[1])
            if name != "step"
        ),
        "window_start_ms": math.floor(start * 1000) if start is not None else None,
        "window_end_ms": math.ceil(end * 1000) if end is not None else None,
        "boundary_accuracy": accuracy,
    }


class StepHistoryWriter:
    """Persist each distinct file-logger record once across bridge restarts."""

    def __init__(
        self,
        path: Path,
        *,
        run_id: str,
        node: str,
        worker_id: str,
        execution_mode: str = "sync",
        clock: Callable[[], float] = time.time,
    ) -> None:
        if execution_mode not in {"sync", "async"}:
            raise ValueError("execution_mode must be sync or async")
        self.path = path
        self.run_id = run_id
        self.node = node
        self.worker_id = worker_id
        self.execution_mode = execution_mode
        self.clock = clock
        self._seen = self._load_seen()

    def _load_seen(self) -> set[str]:
        seen: set[str] = set()
        try:
            for record in json_objects(self.path):
                record_id = record.get("record_id")
                if (isinstance(record_id, str) and record.get("run_id") == self.run_id
                        and record.get("node") == self.node and record.get("worker_id") == self.worker_id):
                    seen.add(record_id)
        except FileNotFoundError:
            pass
        return seen

    def append(self, record: Mapping[str, Any], *, live: bool = True) -> dict[str, Any] | None:
        step = record.get("step")
        data = record.get("data")
        if type(step) is not int or step < 0 or not isinstance(data, dict):
            return None
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
        legacy_id = hashlib.sha256(canonical.encode()).hexdigest()[:24]
        identity = json.dumps([self.run_id, self.node, self.worker_id, record], sort_keys=True, separators=(",", ":"))
        record_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
        if record_id in self._seen or legacy_id in self._seen:
            return None

        observed_at = self.clock()
        duration = _duration(data)
        end = observed_at if live else None
        start = max(0.0, end - duration) if end is not None and duration is not None else None
        stages = {
            key.removeprefix("timing_s/"): float(value)
            for key, value in data.items()
            if key.startswith("timing_s/")
            and finite_number(value) is not None
            and value >= 0
        }
        scope = "trainer_update" if self.execution_mode == "async" else "rl_step"
        history_record = {
            "schema_version": 1,
            "record_type": "verl_step_observation",
            "record_id": record_id,
            "run_id": self.run_id,
            "node": self.node,
            "worker_id": self.worker_id,
            "execution_mode": self.execution_mode,
            "boundary_scope": scope,
            "step": step,
            "observed_at": observed_at,
            "ingested_at": observed_at,
            "source_event_time": None,
            "step_duration_seconds": duration,
            "stage_durations_seconds": stages,
            "workload": {
                **{key: value for key, value in data.items() if key in {
                    "perf/total_num_tokens", "prompt_length/mean", "response_length/mean",
                    "data/train_batch_size", "train_batch_size", "policy_version",
                } and finite_number(value) is not None and value >= 0},
                "has_evaluation": stages.get("testing", 0) > 0,
                "has_checkpoint": stages.get("save_checkpoint", 0) > 0,
            },
            **dashboard_fields(start, end, stages, "approximate" if start is not None else "unknown"),
            "analysis_window": {
                "start": start,
                "end": end,
                "accuracy": "approximate" if start is not None else "unknown",
                "source": "file_logger_observation_minus_reported_duration" if live else "replayed_file_logger_without_event_time",
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(history_record, separators=(",", ":"), sort_keys=True)
        descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            os.write(descriptor, (line + "\n").encode())
        finally:
            os.close(descriptor)
        self._seen.add(record_id)
        return history_record
