"""Write an explicitly synthetic bottleneck investigation run for Grafana practice."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import time
import hashlib
import json

from .scenario import validate_scenario

from ..analysis.diagnosis_analysis import compare_signals, evaluate_rules
from ..analysis.diagnostics import write_report
from ..events import CorrelationContext, EventRecorder
from ..adapters.verl import measured_phase
from ..manifest import make_agent_rl_manifest, write_manifest
from ..metrics import Metric, MetricEmitter
from ..step_history import StepHistoryWriter


def generate(output: Path, *, run_id: str, node: str = "synthetic-node", clock=time.time, step: int = 127, scenario: dict | None = None) -> dict:
    if scenario is not None:
        validate_scenario(scenario)
        if scenario["run_id"] != run_id or scenario["node"] != node:
            raise ValueError("scenario identity does not match requested run/node")
        previous, frame = scenario["frames"]
        if frame["end"] > clock():
            raise ValueError("scenario has not completed")
        step = frame["step"]
        start, end = frame["start"], frame["end"]
        baseline_start, baseline_end = previous["start"], previous["end"]
    else:
        end = clock() - 5
        start = end - 18.4
        baseline_end = start - 5
        baseline_start = baseline_end - 11.2
    duration = end - start
    baseline_duration = baseline_end - baseline_start
    output.mkdir(parents=True, exist_ok=False)
    history = StepHistoryWriter(
        output / "telemetry-events/verl-steps.jsonl", run_id=run_id,
        node=node, worker_id="driver", clock=iter((baseline_end, end)).__next__,
    )
    def data_for(seconds, rollout, scenario_frame=None):
        data = {"perf/time_per_step": seconds, "timing_s/gen": rollout}
        if scenario_frame is not None:
            data.update({"data/train_batch_size": scenario["workload"]["batch_size"],
                         "prompt_length/mean": scenario["workload"]["prompt_tokens"],
                         "response_length/mean": scenario["workload"]["response_tokens"],
                         "policy_version": scenario_frame["policy_version"]})
            data.update({f"timing_s/{p['operation'] if p['phase'] != 'rollout' else 'gen'}": p["end"] - p["start"]
                         for p in scenario_frame["phases"]})
        return data
    rollout_duration = frame["phases"][0]["end"] - frame["phases"][0]["start"] if scenario is not None else 8
    baseline_rollout = previous["phases"][0]["end"] - previous["phases"][0]["start"] if scenario is not None else 5
    prior = history.append({"step": step - 1, "data": data_for(baseline_duration, baseline_rollout, previous if scenario is not None else None)})
    observed = history.append({"step": step, "data": data_for(duration, rollout_duration, frame if scenario is not None else None)})
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
    if scenario is not None:
        current, baseline = dict(frame["signals"]), dict(previous["signals"])
    window = observed["analysis_window"]
    sources = {
        "threefs_p99_latency": "synthetic:3fs_clickhouse",
        "storage_device_busy_ratio": "synthetic:storage_ssd",
        "threefs_throughput_bytes_per_second": "synthetic:3fs_clickhouse",
        "network_utilization_ratio": "synthetic:storage_nic",
        "gpu_utilization_percent": "synthetic:gpu_sampler",
    }
    if scenario is not None:
        sources.update(threefs_p99_latency="synthetic:scenario_storage_p99",
                       threefs_throughput_bytes_per_second="synthetic:scenario_storage_throughput",
                       gpu_utilization_percent="synthetic:scenario_time_weighted_gpu_mean",
                       storage_device_busy_ratio="synthetic:scenario_device_busy_max",
                       vllm_requests_waiting="synthetic:scenario_queue_max",
                       rollout_duration_seconds="synthetic:scenario_sdk")
    candidates = evaluate_rules(
        current, baseline, thresholds={},
        context={"window": window, "boundary_accuracy": "approximate",
                 "sources": sources, "node": node},
    )
    # Scenario values are explicit producer inputs, not backend statistics.
    # Declare their units using the existing additive investigation contract.
    scenario_units = {"step_duration_seconds": "s", "threefs_p99_latency": "ms",
                      "storage_device_busy_ratio": "percentunit", "gpu_utilization_percent": "percent",
                      "threefs_throughput_bytes_per_second": "Bps", "network_utilization_ratio": "percentunit",
                      "vllm_requests_waiting": "short", "rollout_duration_seconds": "s"}
    for candidate in candidates:
        for key in ("evidence", "counter_evidence"):
            for item in candidate.get(key, []):
                item.update(unit=scenario_units.get(item["signal"]),
                            window_statistic=frame["signal_statistics"].get(item["signal"], "synthetic_scenario_value") if scenario is not None else "synthetic_scenario_value")
    comparison_signals = compare_signals(current, baseline)
    for row in comparison_signals:
        row.update(unit=scenario_units.get(row["signal"]),
                   window_statistic=frame["signal_statistics"].get(row["signal"], "synthetic_scenario_value") if scenario is not None else "synthetic_scenario_value")
    if scenario is not None:
        _record_scenario_spans(output, scenario)
    else:
        event_clock = iter((int((start + 2) * 1e9), int((start + 10) * 1e9), int((start + 5) * 1e9)))
        recorder = EventRecorder(
            output / "telemetry-events",
            CorrelationContext(run_id=run_id, producer="demo", role="rollout", worker_id="worker-0", node=node),
            clock_ns=event_clock.__next__,
        )
        with recorder.span("rollout.generate", phase="rollout", step=step, attributes={"data_origin": "synthetic"}) as span:
            pass
        recorder.event("queue.backlog", phase="rollout", step=step, trace_id=span.trace_id,
                       attributes={"waiting": 3, "data_origin": "synthetic"})
        # These boundaries belong to an explicitly synthetic scenario, not a real
        # runtime or measured filesystem. Keep IDs in events rather than labels.
        offsets = iter((3, 3.2, 3.4, 3.6, 7.4, 7.8, 8.0, 8.4))
        tools = EventRecorder(output / "telemetry-events", recorder.context,
            clock_ns=lambda: int((start + next(offsets)) * 1e9))
        with tools.span("tool.call", phase="environment", step=step,
                        attributes={"tool": "pytest", "data_origin": "synthetic"}) as tool:
            with tools.span("sandbox.acquire", phase="environment", step=step,
                            trace_id=tool.trace_id, parent_span_id=tool.span_id,
                            attributes={"deployment": "colocated", "data_origin": "synthetic"}):
                pass
            with tools.span("sandbox.exec", phase="environment", step=step,
                            trace_id=tool.trace_id, parent_span_id=tool.span_id,
                            attributes={"tool": "pytest", "runtime": "containerd", "filesystem": "overlayfs",
                                        "sandbox_id": "demo-sandbox", "data_origin": "synthetic"}):
                pass
            tools.event("sandbox.resource_sample", phase="environment", step=step,
                        trace_id=tool.trace_id, span_id=tool.span_id,
                        attributes={"observation_scope": "cgroup", "io_pressure_ratio": .43,
                                    "data_origin": "synthetic"})
        # An explicit synthetic execution schedule uses actual SDK entry/exit hooks.
        # These intervals are not inferred from a reported metric or real GPU work.
        for native_stage, begin, finish in (("reward",10.1,11.1),("update_actor",11.2,15.0),("update_weights",15.1,17.0)):
            times=iter((int((start+begin)*1e9),int((start+finish)*1e9)))
            stage_recorder=EventRecorder(output/"telemetry-events",
                CorrelationContext(run_id=run_id,producer="demo_native",role="trainer",worker_id="driver",node=node),
                clock_ns=times.__next__)
            with measured_phase(stage_recorder,native_stage,step=step,attributes={"data_origin":"synthetic"}):
                pass
        # Recorded point events demonstrate overlays, not inferred phase durations.
        lifecycle_times = iter((11, 12, 13))
        lifecycle = EventRecorder(output / "telemetry-events",
            CorrelationContext(run_id=run_id, producer="demo", role="trainer", worker_id="driver", node=node),
            clock_ns=lambda: int((start + next(lifecycle_times)) * 1e9))
        for name, phase in (("policy.update.completed", "policy_update"),
                            ("weight.sync.completed", "weight_sync"),
                            ("checkpoint.completed", "checkpoint")):
            lifecycle.event(name, phase=phase, step=step,
                            attributes={"data_origin": "synthetic", "observation_scope": "application"})
        kv = EventRecorder(output / "telemetry-events", recorder.context,
                          clock_ns=lambda: int((start + 14) * 1e9))
        kv.event("kv.cache.evicted", phase="rollout", step=step,
                 attributes={"data_origin": "synthetic", "observation_scope": "synthetic_engine"})
    logs = output / "logs"
    logs.mkdir()
    (logs / "agent.log").write_text(
        f"[synthetic] run={run_id} step={step} tool=pytest sandbox=demo-sandbox status=ok\n"
        "[synthetic] illustration only: local NVMe pressure is correlated evidence, not proven causality\n",
        encoding="utf-8")
    report = {
        "schema_version": 1, "record_type": "bottleneck_diagnosis",
        "generated_at": datetime.fromtimestamp(end, timezone.utc).isoformat(),
        "run_id": run_id, "node": node, "execution_mode": "sync",
        "trigger": "step_observed", "trigger_record_id": observed["record_id"],
        "step": step, "boundary_scope": "rl_step", "analysis_window": window,
        "verdict": "bottleneck_suspected" if candidates else "no_anomaly_observed", "findings": [], "evidence": {},
        "data_origin": "synthetic",
        "missing_sources": [], "limitations": ["Synthetic values are illustrative, not host measurements.",
            *(["Scenario summaries are explicit producer inputs, not Prometheus query statistics; shared storage p99 is illustrative, not an actual ClickHouse backend."] if scenario is not None else [])],
        "diagnosis_schema_version": 1,
        "symptom": {"step": step, "step_duration_seconds": duration, "slow_stages": [], "boundary_scope": "rl_step"},
        "comparison": {
            "current_interval": window, "baseline_interval": prior["analysis_window"],
            "baseline_record_id": prior["record_id"],
            "selection": "same_run_same_worker_nearest_prior_median",
            "signals": comparison_signals,
            **({"workload": scenario["workload"], "workload_comparability": "matched_configured_fields"} if scenario is not None else {}),
        },
        "candidates": candidates,
    }
    write_report(output / "diagnostics", report)
    # Use the same discovery and inspect paths as a wrapped workload. These are
    # synthetic application values, not measurements of the host or its storage.
    MetricEmitter(output / "telemetry-metrics", run_id=run_id, node=node,
                  producer="synthetic", role="trainer", worker_id="driver", clock=lambda: end).emit(
        step=step, samples=[Metric("training_step_time_seconds", duration, labels={"phase": "rl_step"}),
                           Metric("rl_stage_duration_seconds", rollout_duration, labels={"phase": "rollout"})])
    manifest = make_agent_rl_manifest(run_id=run_id, roles={"trainer": node},
        configuration={"execution_mode": "sync", "data_origin": "synthetic"},
        artifacts={"diagnostics": str(output / "diagnostics"),
                   "events": str(output / "telemetry-events")},
        created_at=datetime.fromtimestamp(end, timezone.utc).isoformat())
    manifest["data_origin"] = "synthetic"
    write_manifest(output / "telemetry-manifest.json", manifest)
    return report


def _record_scenario_spans(output: Path, scenario: dict) -> None:
    fingerprint = hashlib.sha256(json.dumps(scenario["workload"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    attrs = {"data_origin": "synthetic", "workload_fingerprint": fingerprint,
             "boundary_scope": "rl_step", "measurement_source": "synthetic_scenario_sdk"}
    for frame in scenario["frames"]:
        for phase in frame["phases"]:
            clock = iter((int(phase["start"] * 1e9), int(phase["end"] * 1e9)))
            context = CorrelationContext(run_id=scenario["run_id"], node=scenario["node"], producer="demo_phase",
                                         role=phase["role"], worker_id=phase["worker_id"], policy_version=frame["policy_version"])
            recorder = EventRecorder(output / "telemetry-events", context, clock_ns=clock.__next__)
            attributes = {**attrs, "scenario_phase": True, "scenario": frame["scenario"]}
            if phase["phase"] == "rollout":
                with recorder.span("rollout.generate", phase="rollout", step=frame["step"], attributes=attributes) as parent:
                    # Child cgroup observations retain the application parent chain.
                    offsets = iter((phase["start"] + 1, phase["start"] + 2, phase["end"] - 2, phase["end"] - 1))
                    child_context = CorrelationContext(run_id=scenario["run_id"], node=scenario["node"], producer="demo_phase",
                                                       role="sandbox", worker_id="pool-0", policy_version=frame["policy_version"])
                    child = EventRecorder(output / "telemetry-events", child_context,
                                          clock_ns=lambda: int(next(offsets) * 1e9))
                    with child.span("tool.call", phase="environment", step=frame["step"], trace_id=parent.trace_id,
                                    parent_span_id=parent.span_id, attributes={**attrs, "tool": "pytest"}) as tool:
                        with child.span("sandbox.exec", phase="environment", step=frame["step"], trace_id=tool.trace_id,
                                        parent_span_id=tool.span_id, attributes={**attrs, "runtime": "containerd",
                                         "filesystem": "overlayfs", "observation_scope": "cgroup", "sandbox_id": "demo-sandbox"}):
                            pass
            else:
                with measured_phase(recorder, phase["operation"], step=frame["step"], attributes=attributes):
                    pass
        # Only explicit workload events; resource anomalies stay diagnosis evidence.
        for name, label, stamp in (("policy.update.completed", "policy_update", frame["phases"][3]["end"]),
                                   ("checkpoint.completed", "checkpoint", frame["end"])):
            event = EventRecorder(output / "telemetry-events",
                CorrelationContext(run_id=scenario["run_id"], node=scenario["node"], producer="demo_phase",
                                   role="trainer", worker_id="driver", policy_version=frame["policy_version"]),
                clock_ns=lambda stamp=stamp: int(stamp * 1e9))
            event.event(name, phase=label, step=frame["step"], attributes=attrs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="new run root under a configured TELEMETRY_LOG_ROOTS parent")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--node", default="synthetic-node")
    args = parser.parse_args()
    try:
        report = generate(args.output, run_id=args.run_id, node=args.node)
    except FileExistsError:
        parser.error("Output already exists; choose a new --output run directory. Existing artifacts were preserved.")
    print(f"Synthetic step {report['step']} with {len(report['candidates'])} candidates: {args.output}")


if __name__ == "__main__":
    main()
