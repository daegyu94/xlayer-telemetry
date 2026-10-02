"""Bridge VERL's built-in file logger to canonical application metrics."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import time
from typing import Any, Iterable, Iterator, Mapping

from xlayer_telemetry.metrics import Metric, MetricEmitter
from xlayer_telemetry.measurements import finite_number
from xlayer_telemetry.step_history import StepHistoryWriter


STAGE_PHASES = {
    "step": "rl_step",
    "gen": "rollout",
    "reward": "reward",
    "old_log_prob": "reference_log_prob",
    "ref": "reference_log_prob",
    "values": "critic",
    "adv": "advantage_estimation",
    "update_critic": "critic_update",
    "update_actor": "actor_update",
    "update_weights": "weight_sync",
    "save_checkpoint": "checkpoint_save",
    "testing": "evaluation",
    "dump_rollout_generations": "artifact_write",
    "start_profile": "profiling",
    "stop_profile": "profiling",
}


class FileRecord(dict):
    """A logger record with ingestion provenance kept out of its content hash."""

    def __init__(self, value: dict[str, Any], *, live: bool) -> None:
        super().__init__(value)
        self.live = live

DIRECT_METRICS = {
    "perf/throughput": ("training_tokens_per_second_per_gpu", {}),
    "perf/total_num_tokens": ("training_tokens_processed", {}),
    "perf/time_per_step": ("training_step_time_seconds", {"phase": "rl_step"}),
    "critic/score/mean": ("reward_score_mean", {}),
    "critic/rewards/mean": ("reward_mean", {}),
    "response_length/mean": ("rollout_output_tokens_mean", {}),
    "num_turns/mean": ("agent_turns_mean", {}),
    "tool_call_counts/mean": ("agent_tool_calls_mean", {}),
}


def describe_metrics() -> str:
    """Describe the actual translation tables without requiring a VERL run."""
    lines = ["VERL file logger -> application snapshot (only keys present in the log)",
             "step -> training_step (published by the textfile collector)"]
    for key, (name, labels) in DIRECT_METRICS.items():
        suffix = " " + ", ".join(f"{key}={value}" for key, value in labels.items()) if labels else ""
        lines.append(f"{key} -> {name}{suffix}")
    lines.append("timing_s/step -> training_step_time_seconds phase=rl_step (fallback when perf/time_per_step is absent or non-finite)")
    lines.extend(["", "Supported stages (completed durations, not live phase boundaries):"])
    for stage, phase in STAGE_PHASES.items():
        lines.append(f"timing_s/{stage} -> rl_stage_duration_seconds phase={phase}")
    lines.extend([
        "timing_per_token_ms/<supported stage> -> rl_stage_time_per_token_seconds (ms / 1000)",
        "", "Other logger keys remain in logs/verl-metrics.jsonl; they are not automatically exported.",
        "vLLM/Ray endpoints are registered separately on the monitoring server.",
        "GPU/host metrics require the node collector; tool/sandbox spans require instrumentation.",
    ])
    return "\n".join(lines)


class VerlMetricsAdapter:
    """Translate stable VERL logger keys while leaving native telemetry untouched."""

    def __init__(self, emitter: MetricEmitter) -> None:
        self.emitter = emitter

    @staticmethod
    def translate(data: Mapping[str, Any]) -> list[Metric]:
        samples: list[Metric] = []
        for key, value in data.items():
            value = finite_number(value)
            if value is None:
                continue
            if key.startswith("timing_s/"):
                stage = key.removeprefix("timing_s/")
                phase = STAGE_PHASES.get(stage)
                if phase is not None:
                    samples.append(
                        Metric(
                            "rl_stage_duration_seconds",
                            float(value),
                            labels={"phase": phase, "verl_stage": stage},
                        )
                    )
                continue
            if key.startswith("timing_per_token_ms/"):
                stage = key.removeprefix("timing_per_token_ms/")
                phase = STAGE_PHASES.get(stage)
                if phase is not None:
                    samples.append(
                        Metric(
                            "rl_stage_time_per_token_seconds",
                            float(value) / 1000,
                            labels={"phase": phase, "verl_stage": stage},
                        )
                    )
                continue
            mapping = DIRECT_METRICS.get(key)
            if mapping is not None:
                name, labels = mapping
                samples.append(Metric(name, float(value), labels=labels))
        duration = finite_number(data.get("perf/time_per_step"))
        fallback = finite_number(data.get("timing_s/step"))
        if duration is None and fallback is not None and fallback >= 0:
            samples.append(Metric("training_step_time_seconds", float(fallback), labels={"phase": "rl_step"}))
        return samples

    def emit(self, data: Mapping[str, Any], *, step: int) -> Path | None:
        samples = self.translate(data)
        if not samples:
            return None
        return self.emitter.emit(step=step, samples=samples)


def iter_file_records(
    path: Path,
    *,
    follow: bool,
    poll_interval: float,
) -> Iterator[dict[str, Any]]:
    existed_at_start = path.is_file()
    while not path.is_file():
        if not follow:
            raise FileNotFoundError(path)
        time.sleep(poll_interval)
    backlog = existed_at_start or not follow
    while True:
        try:
            stream = path.open("rb")
        except FileNotFoundError:
            if not follow:
                raise
            time.sleep(poll_interval)
            continue
        with stream:
            opened = os.fstat(stream.fileno())
            backlog_end = opened.st_size if backlog else 0
            while True:
                position = stream.tell()
                line = stream.readline()
                if line and (line.endswith(b"\n") or not follow):
                    try:
                        record = json.loads(line.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if isinstance(record, dict):
                        yield FileRecord(record, live=follow and position >= backlog_end)
                    continue
                if not follow:
                    return
                # Keep incomplete UTF-8/JSON for the next poll, but do not keep
                # following an orphaned inode forever after logger rotation.
                stream.seek(position)
                try:
                    current = path.stat()
                except FileNotFoundError:
                    current = None
                if current is not None and (
                        (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                        or current.st_size < position + len(line)):
                    backlog = True  # Replacement contents have unknown event time.
                    break
                time.sleep(poll_interval)


def bridge_records(
    records: Iterable[Mapping[str, Any]],
    adapter: VerlMetricsAdapter,
    history: StepHistoryWriter | None = None,
) -> int:
    emitted = 0
    for record in records:
        step = record.get("step")
        data = record.get("data")
        if type(step) is not int or step < 0 or not isinstance(data, dict):
            continue
        if history is not None:
            history.append(record, live=getattr(record, "live", True))
        if adapter.emit(data, step=step) is not None:
            emitted += 1
    return emitted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--describe-metrics", action="store_true", help="List supported logger keys and metric names without starting a bridge")
    parser.add_argument(
        "--metrics-dir",
        type=Path,
        default=os.environ.get("TELEMETRY_METRICS_DIR"),
    )
    parser.add_argument("--run-id", default=os.environ.get("TELEMETRY_RUN_ID"))
    parser.add_argument("--worker-id", default="driver")
    parser.add_argument("--node", default=socket.gethostname())
    parser.add_argument("--history", type=Path)
    parser.add_argument("--execution-mode", choices=("sync", "async"), default="sync")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    args = parser.parse_args()
    if args.describe_metrics:
        print(describe_metrics())
        return
    if args.input is None:
        parser.error("--input is required unless --describe-metrics is used")
    if not args.run_id:
        parser.error("--run-id or TELEMETRY_RUN_ID is required")
    if args.metrics_dir is None:
        parser.error("--metrics-dir or TELEMETRY_METRICS_DIR is required")
    if finite_number(args.poll_interval) is None or args.poll_interval <= 0:
        parser.error("--poll-interval must be finite and positive")
    if not args.follow and not args.input.is_file():
        parser.error(f"not a file: {args.input}")

    emitter = MetricEmitter(
        args.metrics_dir,
        run_id=args.run_id,
        producer="verl",
        role="trainer",
        worker_id=args.worker_id,
        node=args.node,
    )
    history = (
        StepHistoryWriter(
            args.history,
            run_id=args.run_id,
            node=args.node,
            worker_id=args.worker_id,
            execution_mode=args.execution_mode,
        )
        if args.history is not None
        else None
    )
    bridge_records(
        iter_file_records(
            args.input,
            follow=args.follow,
            poll_interval=args.poll_interval,
        ),
        VerlMetricsAdapter(emitter),
        history,
    )


if __name__ == "__main__":
    main()
