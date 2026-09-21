"""Run a tiny REINFORCE loop to validate Agent RL telemetry on one GPU.

This is an instrumentation smoke test, not a VERL performance benchmark.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import time

from post_training_telemetry.events import CorrelationContext, EventRecorder
from post_training_telemetry.manifest import make_agent_rl_manifest, write_manifest
from post_training_telemetry.metrics import Metric, MetricEmitter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    if args.steps <= 0 or args.batch_size <= 0:
        parser.error("--steps and --batch-size must be positive")

    import torch

    if args.device == "cuda" and not torch.cuda.is_available():
        parser.error("CUDA is not available")
    device = torch.device(args.device)
    run_id = f"agent-rl-smoke-{int(time.time())}"
    metrics_dir = args.output / "telemetry-metrics"
    events_dir = args.output / "telemetry-events"
    visible_devices = os.environ.get("CUDA_VISIBLE_DEVICES")
    gpu = None
    if device.type == "cuda":
        logical_gpu = torch.cuda.current_device()
        visible = [item.strip() for item in (visible_devices or "").split(",")]
        if logical_gpu < len(visible) and visible[logical_gpu]:
            gpu = visible[logical_gpu]
        else:
            gpu = str(logical_gpu)
    context = CorrelationContext(
        run_id=run_id,
        producer="tiny-agent-rl",
        role="trainer",
        worker_id="0",
        node="localhost",
        rank=0,
        local_rank=0 if gpu is not None else None,
        gpu=gpu,
    )
    recorder = EventRecorder(events_dir, context)
    emitter = MetricEmitter(
        metrics_dir,
        run_id=run_id,
        producer=context.producer,
        role=context.role,
        worker_id=context.worker_id,
        node=context.node,
        rank=context.rank,
        local_rank=context.local_rank,
        gpu=context.gpu,
        cuda_visible_devices=visible_devices,
    )

    policy = torch.nn.Sequential(
        torch.nn.Linear(32, 64),
        torch.nn.Tanh(),
        torch.nn.Linear(64, 8),
    ).to(device)
    optimizer = torch.optim.AdamW(policy.parameters(), lr=1e-3)

    for step in range(1, args.steps + 1):
        durations = {}
        started = time.perf_counter()
        with recorder.span("input.prepare", phase="input_prepare", step=step):
            prompts = torch.randn(args.batch_size, 32, device=device)
        durations["input_prepare"] = time.perf_counter() - started

        started = time.perf_counter()
        with recorder.span("rollout.generate", phase="rollout", step=step):
            distribution = torch.distributions.Categorical(logits=policy(prompts))
            actions = distribution.sample()
            log_prob = distribution.log_prob(actions)
        durations["rollout"] = time.perf_counter() - started

        started = time.perf_counter()
        with recorder.span(
            "tool.evaluate",
            phase="tool_interaction",
            step=step,
            attributes={"tool": "synthetic_environment"},
        ):
            targets = (prompts.sum(dim=1) > 0).long()
            rewards = (actions.remainder(2) == targets).float()
        durations["tool_interaction"] = time.perf_counter() - started

        started = time.perf_counter()
        with recorder.span("reward.compute", phase="reward", step=step):
            advantage = rewards - rewards.mean()
        durations["reward"] = time.perf_counter() - started

        started = time.perf_counter()
        with recorder.span("actor.update", phase="actor_update", step=step):
            loss = -(log_prob * advantage).mean()
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            if device.type == "cuda":
                torch.cuda.synchronize(device)
        durations["actor_update"] = time.perf_counter() - started

        started = time.perf_counter()
        with recorder.span("weights.sync", phase="weight_sync", step=step):
            policy_version = step
            synced_parameters = sum(parameter.numel() for parameter in policy.parameters())
        durations["weight_sync"] = time.perf_counter() - started

        rollout_seconds = max(durations["rollout"], 1e-12)
        samples = [
            Metric(
                "rl_stage_duration_seconds",
                duration,
                labels={"phase": phase, "verl_stage": phase},
            )
            for phase, duration in durations.items()
        ]
        samples.extend(
            [
                Metric("reward_mean", float(rewards.mean().item())),
                Metric("agent_turns_mean", 1.0),
                Metric("agent_tool_calls_mean", 1.0),
                Metric("rollout_output_tokens_mean", 1.0),
                Metric(
                    "training_tokens_per_second_per_gpu",
                    args.batch_size / rollout_seconds,
                ),
                Metric("policy_version", policy_version),
                Metric("weight_sync_bytes_total", synced_parameters * 4),
            ]
        )
        emitter.emit(step=step, samples=samples)

    write_manifest(
        args.output / "telemetry-manifest.json",
        make_agent_rl_manifest(
            run_id=run_id,
            roles={"trainer": "localhost", "rollout": "localhost"},
            artifacts={
                "metrics": str(metrics_dir),
                "events": str(events_dir),
            },
            configuration={
                "backend": "pytorch-smoke",
                "device": str(device),
                "steps": args.steps,
                "batch_size": args.batch_size,
            },
        ),
    )
    print(f"run_id={run_id}")
    print(f"device={device}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()
