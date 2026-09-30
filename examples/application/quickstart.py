"""Record metrics and a tool -> sandbox span chain using only the CPU SDK.

The local workspace is ordinary file work, not a container or a sandbox runtime.
No GPU/VERL dependency, backend, resource attribution, or bottleneck is assumed.
Run from an installed checkout: python -m examples.application.quickstart ...
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import socket
import time

from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.metrics import Metric, MetricEmitter
from xlayer_telemetry.sandbox import SandboxRecorder


def record(output: Path, *, run_id: str, node: str, steps: int) -> None:
    if steps < 1:
        raise ValueError("steps must be positive")
    context = CorrelationContext(run_id, "sdk-example", "rollout", "0", node)
    output.mkdir(parents=True, exist_ok=False)
    workspace = output / "workspace"
    workspace.mkdir()
    events = EventRecorder(output / "telemetry-events", context)
    sandbox_events = EventRecorder(
        output / "telemetry-events", replace(context, producer="sandbox-example", role="sandbox"),
    )
    sandbox = SandboxRecorder(
        sandbox_events, runtime="local-example", filesystem="plain-workspace",
        deployment="colocated", sandbox_node=node,
    )
    emitter = MetricEmitter(
        output / "telemetry-metrics", run_id=run_id, producer=context.producer,
        role=context.role, worker_id=context.worker_id, node=node,
    )
    for step in range(1, steps + 1):
        started = time.monotonic()
        with events.span("iteration", phase="rollout", step=step) as iteration:
            tool_started = time.monotonic()
            with events.span(
                "tool.call", phase="environment", step=step,
                trace_id=iteration.trace_id, parent_span_id=iteration.span_id,
                attributes={"tool": "file_roundtrip", "trajectory_id": f"example-{step}"},
            ) as tool:
                with sandbox.span(
                    "exec", step=step, trace_id=tool.trace_id, parent_span_id=tool.span_id,
                    trajectory_id=f"example-{step}", sandbox_id="local-workspace",
                ):
                    path = workspace / "sample.bin"
                    payload = b"xlayer SDK example\n" * 128
                    path.write_bytes(payload)
                    if path.read_bytes() != payload:
                        raise RuntimeError("workspace roundtrip failed")
                    path.unlink()
            tool_seconds = time.monotonic() - tool_started
        emitter.emit(step=step, samples=[
            Metric("training_step_time_seconds", time.monotonic() - started),
            Metric("agent_tool_call_duration_seconds", tool_seconds, labels={"tool": "file_roundtrip"}),
        ])
    print(f"Recorded {steps} iterations: {output}")
    print("Metrics are latest snapshots; JSONL spans preserve every iteration.")
    print("No cgroup or device metrics collected; no filesystem performance claim.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New run directory")
    parser.add_argument("--run-id", default="sdk-example")
    parser.add_argument("--node", default=socket.gethostname())
    parser.add_argument("--steps", type=int, default=2)
    args = parser.parse_args()
    if args.steps < 1:
        parser.error("--steps must be positive")
    record(args.output, run_id=args.run_id, node=args.node, steps=args.steps)


if __name__ == "__main__":
    main()
