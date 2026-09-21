"""Bridge VERL's built-in file logger to canonical application metrics."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import socket
import time
from typing import Any, Iterable, Iterator, Mapping

from post_training_telemetry.metrics import Metric, MetricEmitter


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


class VerlMetricsAdapter:
    """Translate stable VERL logger keys while leaving native telemetry untouched."""

    def __init__(self, emitter: MetricEmitter) -> None:
        self.emitter = emitter

    @staticmethod
    def translate(data: Mapping[str, Any]) -> list[Metric]:
        samples: list[Metric] = []
        for key, value in data.items():
            if type(value) not in (int, float) or not math.isfinite(value):
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
    while not path.is_file():
        if not follow:
            raise FileNotFoundError(path)
        time.sleep(poll_interval)
    with path.open(encoding="utf-8") as stream:
        while True:
            position = stream.tell()
            line = stream.readline()
            if line and not line.endswith(chr(10)) and follow:
                stream.seek(position)
                time.sleep(poll_interval)
                continue
            if line:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    yield record
                continue
            if not follow:
                return
            time.sleep(poll_interval)


def bridge_records(
    records: Iterable[Mapping[str, Any]],
    adapter: VerlMetricsAdapter,
) -> int:
    emitted = 0
    for record in records:
        step = record.get("step")
        data = record.get("data")
        if type(step) is not int or step < 0 or not isinstance(data, dict):
            continue
        if adapter.emit(data, step=step) is not None:
            emitted += 1
    return emitted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument(
        "--metrics-dir",
        type=Path,
        default=os.environ.get("TELEMETRY_METRICS_DIR"),
    )
    parser.add_argument("--run-id", default=os.environ.get("TELEMETRY_RUN_ID"))
    parser.add_argument("--worker-id", default="driver")
    parser.add_argument("--node", default=socket.gethostname())
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    args = parser.parse_args()
    if not args.run_id:
        parser.error("--run-id or TELEMETRY_RUN_ID is required")
    if args.metrics_dir is None:
        parser.error("--metrics-dir or TELEMETRY_METRICS_DIR is required")
    if args.poll_interval <= 0:
        parser.error("--poll-interval must be positive")
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
    bridge_records(
        iter_file_records(
            args.input,
            follow=args.follow,
            poll_interval=args.poll_interval,
        ),
        VerlMetricsAdapter(emitter),
    )


if __name__ == "__main__":
    main()
