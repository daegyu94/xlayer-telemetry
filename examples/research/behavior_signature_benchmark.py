"""Reproducible synthetic Agent RL replay and real CPU overhead experiment.

GPU/network/storage evidence and event clocks are simulated, never real faults.
Only Python processing time, byte sizes, and optional cProfile capture are real.
Run: python examples/research/behavior_signature_benchmark.py --output-dir ...
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import platform
import pstats
import random
import statistics
import sys
import time

from xlayer_telemetry.analysis.behavior_signature import Boundary, candidates, compare, encode, summarize
from xlayer_telemetry.analysis.triggered_profiling import CapturePolicy, TriggeredProfiler
from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.fileio import atomic_write_text


PHASES = ("cpu", "compute", "communication", "storage")
EXPECTED = ("cpu_contention", "gpu_pressure", "network_pressure", "storage_pressure")
SIGNALS = (("host_cpu_pressure_ratio", "node"), ("gpu_utilization_percent", "device"),
           ("network_utilization_ratio", "network-interface"), ("disk_busy_ratio", "device"))


class VirtualClock:
    def __init__(self):
        self.ns = 1700000000000000000

    def __call__(self):
        return self.ns

    def advance(self, seconds):
        self.ns += round(seconds * 1e9)


class MemoryRecorder(EventRecorder):
    def __init__(self, context, clock):
        self.lines = []
        super().__init__(Path("unused-memory-recorder"), context, clock_ns=clock)

    def _persist(self, line):
        self.lines.append(line)


def fixture(seed=0, *, cause=None, step=1, worker="0", missing=False, mixed=False, samples=64):
    rng, clock = random.Random(seed), VirtualClock()
    context = CorrelationContext("signature-research", "synthetic", "actor", worker, "synthetic-node", gpu=worker)
    recorder = MemoryRecorder(context, clock)
    started = clock()
    with recorder.span("rollout", phase="rollout", step=step, attributes={"rollout_id": "rollout-1"}) as parent:
        for index, phase in enumerate(PHASES):
            factor = 5 if cause == index or mixed and index == 3 else 1
            resource_identity = ({}, {"engine": "actor"}, {"interface": "synthetic-nic"}, {"device": "synthetic-ssd"})[index]
            for _ in range(samples):
                with recorder.span("agent." + phase, phase=phase, step=step, trace_id=parent.trace_id,
                                   parent_span_id=parent.span_id, attributes={"rollout_id": "rollout-1", "tokens": 4, **resource_identity}):
                    clock.advance(.002 * factor * rng.uniform(.95, 1.05))
    duration = (clock()-started) / 1e9
    boundary = Boundary(context, step=step, sequence=step, duration_seconds=duration, accuracy="exact",
                        workload={"tokens": samples * 4, "tool_calls": samples, "policy_version": 1})
    observations = []
    for index, (signal, scope) in enumerate(SIGNALS):
        high = cause == index or mixed and index == 3
        value = (.4, 98., .95, .99)[index] if high else (.03, 50., .1, .1)[index]
        entity = {"node": context.node}
        if index == 1:
            entity.update(gpu=worker, engine="actor")
        if index == 2:
            entity["interface"] = "synthetic-nic"
        if index == 3:
            entity["device"] = "synthetic-ssd"
        observations.append({"signal": signal, "scope": scope, "entity": entity,
                             "unit": "percent" if index == 1 else "ratio",
                             "source": "synthetic-fixture", "status": "no_data" if missing and high else "observed",
                             "value": value * rng.uniform(.98, 1.02), "accuracy": "sampled"})
    # Hot different GPU and foreign node are deliberately irrelevant confounders.
    observations.extend([{**observations[1], "entity": {"node": context.node, "gpu": "other", "engine": "unrelated"}, "value": 99},
                         {**observations[0], "entity": {"node": "foreign-node"}, "value": .9}])
    return boundary, [json.loads(line) for line in recorder.lines], observations, sum(len(line.encode()) for line in recorder.lines)


def cpu_work(work_iterations=40000):
    return sum((value * value) % 997 for value in range(work_iterations))


def overhead(boundary, events, observations, baseline, *, repeats, iterations, work_iterations):
    modes = {"workload_only": [], "signature": [], "signature_compare_candidates": [], "raw_json_serialization": []}
    instrumentation = {mode: [] for mode in modes if mode != "workload_only"}
    paired_cpu_work = {mode: [] for mode in instrumentation}
    paired_cost_percent = {mode: [] for mode in instrumentation}
    sink = 0
    for repeat in range(repeats + 1):
        # Alternate order to reduce systematic warmup/order bias.
        order = list(modes) if repeat % 2 else list(reversed(modes))
        for mode in order:
            started = time.perf_counter_ns()
            for _ in range(iterations):
                work_started = time.perf_counter_ns()
                sink += cpu_work(work_iterations)
                instrument_started = time.perf_counter_ns()
                work_ms = (instrument_started-work_started) / 1e6
                if mode in {"signature", "signature_compare_candidates"}:
                    signature = summarize(boundary, events, observations)
                    sink += len(encode(signature))
                    if mode == "signature_compare_candidates":
                        sink += len(candidates(signature, compare(signature, [baseline])))
                elif mode == "raw_json_serialization":
                    sink += sum(len(json.dumps(event, separators=(",", ":"))) for event in events)
                if repeat and mode in instrumentation:
                    instrumentation_ms = (time.perf_counter_ns()-instrument_started) / 1e6
                    instrumentation[mode].append(instrumentation_ms)
                    paired_cpu_work[mode].append(work_ms)
                    paired_cost_percent[mode].append(100 * instrumentation_ms / work_ms)
            seconds = (time.perf_counter_ns()-started) / 1e9 / iterations
            if repeat:
                modes[mode].append(seconds)
    result = {mode: {"median_ms_per_boundary": statistics.median(values) * 1000,
                     "min_ms": min(values)*1000, "max_ms": max(values)*1000} for mode, values in modes.items()}
    base = result["workload_only"]["median_ms_per_boundary"]
    for values in result.values():
        values["overhead_percent_vs_workload_only"] = (values["median_ms_per_boundary"] / base - 1) * 100
    for mode, values in instrumentation.items():
        result[mode]["instrumentation_only_median_ms"] = statistics.median(values)
        result[mode]["paired_cpu_work_median_ms"] = statistics.median(paired_cpu_work[mode])
        result[mode]["paired_added_cost_percent"] = statistics.median(paired_cost_percent[mode])
    result["method"] = {"warmup_batches": 1, "repeated_batches": repeats, "boundaries_per_batch": iterations,
                        "events_per_boundary": len(events), "fixture_generation_in_timed_region": False,
                        "disk_io_in_timed_region": False, "sink_nonzero": sink != 0,
                        "cpu_work_iterations": work_iterations}
    return result


def run(output_dir: Path, *, seed=20261007, cases=32, repeats=7, iterations=50, work_iterations=40000, real_cpu_capture=False):
    output_dir.mkdir(parents=True, exist_ok=False)
    old_boundary, old_events, old_observations, _ = fixture(seed, step=0)
    baseline = summarize(old_boundary, old_events, old_observations)
    counts = {name: {"cases": 0, "correct_unique_candidate": 0, "slow_detected": 0} for name in (*EXPECTED, "normal")}
    missing_cases = ambiguous_cases = rollout_separated = workload_rejected = 0
    raw_bytes = log_bytes = signature_bytes = 0
    example = None
    for case in range(cases):
        for cause in (None, 0, 1, 2, 3):
            boundary, events, observations, raw_size = fixture(seed + case, cause=cause)
            signature = summarize(boundary, events, observations)
            comparison = compare(signature, [baseline])
            supported = [row["candidate"] for row in candidates(signature, comparison) if row["state"] == "supported_candidate"]
            expected = EXPECTED[cause] if cause is not None else "normal"
            row = counts[expected]
            row["cases"] += 1
            row["correct_unique_candidate"] += supported == ([expected] if cause is not None else [])
            row["slow_detected"] += comparison["slow"] is True
            raw_bytes += raw_size
            # A second, synthetic human-readable log stream; no payload padding.
            log_bytes += sum(len((f"worker={event['worker_id']} step={event['step']} phase={event['phase']} "
                                  f"completed={event['name']} duration_seconds={event['duration_seconds']}\n").encode())
                             for event in events)
            signature_bytes += len(encode(signature).encode())
            if cause == 0 and case == 0:
                example = {"signature": signature, "history": comparison, "candidates": candidates(signature, comparison)}
        boundary, events, observations, _ = fixture(seed + case, cause=0, missing=True)
        signature = summarize(boundary, events, observations)
        rows = candidates(signature, compare(signature, [baseline]))
        missing_cases += rows[0]["state"] == "supporting_signal" and bool(rows[0]["missing"])
        boundary, events, observations, _ = fixture(seed + case, cause=0, mixed=True)
        signature = summarize(boundary, events, observations)
        ambiguous_cases += sum(row["state"] == "supported_candidate" for row in candidates(signature, compare(signature, [baseline]))) == 2
        changed = summarize(replace(boundary, workload={"tokens": 999}), events, observations)
        workload_rejected += compare(changed, [baseline])["slow"] is None
        # Async trainer_update and rollout boundaries remain independent.
        rollout = replace(boundary, scope="rollout", phase="rollout", rollout_id="rollout-1")
        old_rollout = replace(old_boundary, scope="rollout", phase="rollout", rollout_id="rollout-1")
        update = replace(boundary, scope="trainer_update", duration_seconds=.2)
        old_update = replace(old_boundary, scope="trainer_update", duration_seconds=.2)
        rollout_separated += (compare(summarize(rollout, events), [summarize(old_rollout, old_events)])["slow"] is True
                              and compare(summarize(update, events), [summarize(old_update, old_events)])["slow"] is False)
    boundary, events, observations, _ = fixture(seed, cause=0)
    peer_boundary, peer_events, peer_observations, _ = fixture(seed, worker="1")
    peer = compare(summarize(boundary, events, observations), [summarize(peer_boundary, peer_events, peer_observations)], peer=True)
    relation_cost = next(row for row in compare(summarize(boundary, events), [baseline])["relations"] if row["child_phase"] == "cpu")
    fan_boundary, fan_events, fan_observations, _ = fixture(seed)
    extra = [{**event, "span_id": event["span_id"] + "-extra"} for event in fan_events if event["phase"] == "cpu"]
    fan_signature = summarize(fan_boundary, [*fan_events, *extra], fan_observations)
    relation_fanout = next(row for row in compare(fan_signature, [baseline])["relations"] if row["child_phase"] == "cpu")
    timings = overhead(boundary, events, observations, baseline, repeats=repeats, iterations=iterations, work_iterations=work_iterations)
    capture = {"state": "disabled", "real_cpu_capture": False}
    if real_cpu_capture:
        # Standard library profiler on actual synthetic CPU work, not GPU/VERL.
        code = "import cProfile,sys; cProfile.run('sum((i*i)%997 for i in range(500000))',sys.argv[1])"
        capture_path = output_dir / "synthetic-cpu.prof"
        profiler = TriggeredProfiler([sys.executable, "-c", code, str(capture_path)],
                                     policy=CapturePolicy(window_seconds=.05, timeout_seconds=2))
        capture = profiler.request(example["signature"], example["history"])
        valid_profile = capture_path.is_file() and pstats.Stats(str(capture_path)).total_calls > 0
        capture.update(real_cpu_capture=capture_path.is_file(), artifact_verified=valid_profile,
                       capture_bytes=capture_path.stat().st_size if capture_path.is_file() else 0,
                       target="separate synthetic CPU computation; not attached running workload")
    report = {"schema_version": 1, "experiment": "synthetic_agent_rl_behavior_signature", "seed": seed,
              "python": platform.python_version(), "platform": platform.system(), "machine": platform.machine(),
              "classification": counts, "missing_source_preserved": {"correct": missing_cases, "total": cases},
              "mixed_candidates_preserved": {"correct": ambiguous_cases, "total": cases},
              "changed_workload_rejected": {"correct": workload_rejected, "total": cases},
              "slow_rollout_normal_async_update": {"correct": rollout_separated, "total": cases},
              "peer_comparison": {"reference_count": peer["reference_count"], "slow": peer["slow"], "duration_ratio": peer["duration_ratio"]},
              "relation_delta": {"higher_child_cost": relation_cost, "higher_fanout": relation_fanout},
              "volume": {"boundaries": cases * 5, "raw_sdk_event_bytes": raw_bytes, "signature_bytes": signature_bytes,
                         "raw_synthetic_log_bytes": log_bytes, "raw_events_plus_logs_bytes": raw_bytes + log_bytes,
                         "reduction_ratio": raw_bytes / signature_bytes, "reduction_percent": 100 * (1 - signature_bytes / raw_bytes),
                         "events_plus_logs_reduction_ratio": (raw_bytes + log_bytes) / signature_bytes},
              "cpu_overhead": timings, "optional_capture": capture,
              "limits": ["Event durations, resource evidence and fault labels are simulated with fixed seed; thresholds match fixture ranges.",
                         "Accuracy measures this controlled fixture, not causal root-cause accuracy on production or real GPU/VERL.",
                         "Overhead is actual Python CPU processing over prebuilt traces; no SDK collection, backend, disk I/O or native profiler overhead.",
                         "Raw SDK events and secondary synthetic human-readable log sizes are counted separately. Signatures lose original order, IDs and per-event detail.",
                         "Summary artifact size reduction does not change existing SDK raw event collection or backend ingestion.",
                         "Optional cProfile artifact is real CPU profiling of a separate synthetic process; no GPU capture or existing workload attachment."]}
    atomic_write_text(output_dir / "report.json", json.dumps(report, indent=2, allow_nan=False) + "\n")
    atomic_write_text(output_dir / "example.json", json.dumps(example, indent=2, allow_nan=False) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261007)
    parser.add_argument("--cases", type=int, default=32)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--work-iterations", type=int, default=40000)
    parser.add_argument("--real-cpu-capture", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.cases <= 256 or not 1 <= args.repeats <= 20 or not 1 <= args.iterations <= 1000 or not 1 <= args.work_iterations <= 10000000:
        parser.error("bounded experiment: cases <=256, repeats <=20, iterations <=1000, work-iterations <=10000000")
    report = run(args.output_dir, seed=args.seed, cases=args.cases, repeats=args.repeats, iterations=args.iterations,
                 work_iterations=args.work_iterations, real_cpu_capture=args.real_cpu_capture)
    print(json.dumps({"report": str(args.output_dir / "report.json"), "volume": report["volume"], "cpu_overhead": report["cpu_overhead"]}, indent=2))


if __name__ == "__main__":
    main()
