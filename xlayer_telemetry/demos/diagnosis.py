"""Write an explicitly synthetic bottleneck investigation run for Grafana practice."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import time

from ..analysis.diagnosis_analysis import compare_signals, evaluate_rules
from ..analysis.diagnostics import write_report
from ..events import CorrelationContext, EventRecorder
from ..manifest import make_agent_rl_manifest, write_manifest
from ..metrics import Metric, MetricEmitter
from ..step_history import StepHistoryWriter


def generate(output: Path, *, run_id: str, node: str = "synthetic-node", clock=time.time) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    end = clock() - 5
    start = end - 18.4
    baseline_end = start - 5
    baseline_start = baseline_end - 11.2
    history = StepHistoryWriter(
        output / "telemetry-events/verl-steps.jsonl", run_id=run_id,
        node=node, worker_id="driver", clock=iter((baseline_end, end)).__next__,
    )
    prior = history.append({"step": 126, "data": {"perf/time_per_step": 11.2, "timing_s/gen": 5}})
    observed = history.append({"step": 127, "data": {"perf/time_per_step": 18.4, "timing_s/gen": 8}})
    current = {
        "step_duration_seconds": 18.4, "threefs_p99_latency": 14,
        "storage_device_busy_ratio": 0.96,
        "threefs_throughput_bytes_per_second": 110,
        "gpu_utilization_percent": 47, "network_utilization_ratio": 0.2,
        "vllm_requests_waiting": 0,
    }
    baseline = {
        "step_duration_seconds": 11.2, "threefs_p99_latency": 2,
        "storage_device_busy_ratio": 0.31,
        "threefs_throughput_bytes_per_second": 100,
        "gpu_utilization_percent": 91, "network_utilization_ratio": 0.2,
        "vllm_requests_waiting": 0,
    }
    window = observed["analysis_window"]
    sources = {
        "threefs_p99_latency": "synthetic:3fs_clickhouse",
        "storage_device_busy_ratio": "synthetic:storage_ssd",
        "threefs_throughput_bytes_per_second": "synthetic:3fs_clickhouse",
        "network_utilization_ratio": "synthetic:storage_nic",
        "gpu_utilization_percent": "synthetic:gpu_sampler",
    }
    candidates = evaluate_rules(
        current, baseline, thresholds={},
        context={"window": window, "boundary_accuracy": "approximate",
                 "sources": sources, "node": node},
    )
    event_clock = iter((int((start + 2) * 1e9), int((start + 10) * 1e9), int((start + 5) * 1e9)))
    recorder = EventRecorder(
        output / "telemetry-events",
        CorrelationContext(run_id=run_id, producer="demo", role="rollout", worker_id="worker-0", node=node),
        clock_ns=event_clock.__next__,
    )
    with recorder.span("rollout.generate", phase="rollout", step=127, attributes={"data_origin": "synthetic"}) as span:
        pass
    recorder.event("queue.backlog", phase="rollout", step=127, trace_id=span.trace_id,
                   attributes={"waiting": 3, "data_origin": "synthetic"})
    # These boundaries belong to an explicitly synthetic scenario, not a real
    # runtime or measured filesystem. Keep IDs in events rather than labels.
    offsets = iter((3, 3.2, 3.4, 3.6, 7.4, 7.8, 8.0, 8.4))
    tools = EventRecorder(output / "telemetry-events", recorder.context,
        clock_ns=lambda: int((start + next(offsets)) * 1e9))
    with tools.span("tool.call", phase="environment", step=127,
                    attributes={"tool": "pytest", "data_origin": "synthetic"}) as tool:
        with tools.span("sandbox.acquire", phase="environment", step=127,
                        trace_id=tool.trace_id, parent_span_id=tool.span_id,
                        attributes={"deployment": "colocated", "data_origin": "synthetic"}):
            pass
        with tools.span("sandbox.exec", phase="environment", step=127,
                        trace_id=tool.trace_id, parent_span_id=tool.span_id,
                        attributes={"tool": "pytest", "runtime": "containerd", "filesystem": "overlayfs",
                                    "sandbox_id": "demo-sandbox", "data_origin": "synthetic"}):
            pass
        tools.event("sandbox.resource_sample", phase="environment", step=127,
                    trace_id=tool.trace_id, span_id=tool.span_id,
                    attributes={"observation_scope": "cgroup", "io_pressure_ratio": .43,
                                "data_origin": "synthetic"})
    logs = output / "logs"
    logs.mkdir()
    (logs / "agent.log").write_text(
        f"[synthetic] run={run_id} step=127 tool=pytest sandbox=demo-sandbox status=ok\n"
        "[synthetic] illustration only: local NVMe pressure is correlated evidence, not proven causality\n",
        encoding="utf-8")
    report = {
        "schema_version": 1, "record_type": "bottleneck_diagnosis",
        "generated_at": datetime.fromtimestamp(end, timezone.utc).isoformat(),
        "run_id": run_id, "node": node, "execution_mode": "sync",
        "trigger": "step_observed", "trigger_record_id": observed["record_id"],
        "step": 127, "boundary_scope": "rl_step", "analysis_window": window,
        "verdict": "bottleneck_suspected", "findings": [], "evidence": {},
        "data_origin": "synthetic",
        "missing_sources": [], "limitations": ["Synthetic values are illustrative, not host measurements."],
        "diagnosis_schema_version": 1,
        "symptom": {"step": 127, "step_duration_seconds": 18.4, "slow_stages": [], "boundary_scope": "rl_step"},
        "comparison": {
            "current_interval": window, "baseline_interval": prior["analysis_window"],
            "baseline_record_id": prior["record_id"],
            "selection": "same_run_same_worker_nearest_prior_median",
            "signals": compare_signals(current, baseline),
        },
        "candidates": candidates,
    }
    write_report(output / "diagnostics", report)
    # Use the same discovery and inspect paths as a wrapped workload. These are
    # synthetic application values, not measurements of the host or its storage.
    MetricEmitter(output / "telemetry-metrics", run_id=run_id, node=node,
                  producer="synthetic", role="trainer", worker_id="driver", clock=lambda: end).emit(
        step=127, samples=[Metric("training_step_time_seconds", 18.4, labels={"phase": "rl_step"}),
                           Metric("rl_stage_duration_seconds", 8, labels={"phase": "rollout"})])
    manifest = make_agent_rl_manifest(run_id=run_id, roles={"trainer": node},
        configuration={"execution_mode": "sync", "data_origin": "synthetic"},
        artifacts={"diagnostics": str(output / "diagnostics"),
                   "events": str(output / "telemetry-events")},
        created_at=datetime.fromtimestamp(end, timezone.utc).isoformat())
    manifest["data_origin"] = "synthetic"
    write_manifest(output / "telemetry-manifest.json", manifest)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="new run root under a configured TELEMETRY_LOG_ROOTS parent")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--node", default="synthetic-node")
    args = parser.parse_args()
    report = generate(args.output, run_id=args.run_id, node=args.node)
    print(f"Synthetic step {report['step']} with {len(report['candidates'])} candidates: {args.output}")


if __name__ == "__main__":
    main()
