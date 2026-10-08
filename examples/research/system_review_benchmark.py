"""Compare repository revisions on synthetic diagnosis, CPU and allocation cost.

Backend responses and workload records are fixtures. Only Python wall/CPU time
and tracemalloc allocations are measured; this is not a VERL or storage test.
"""

from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
import types

from xlayer_telemetry.analysis import behavior_signature, diagnostics
from xlayer_telemetry.events import CorrelationContext


ROOT = Path(__file__).resolve().parents[2]


def before_modules(revision):
    """Execute only code in the explicitly selected trusted repository revision."""
    commit = subprocess.run(["git", "rev-parse", "--verify", "--end-of-options", revision + "^{commit}"],
                            cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    modules = {}
    for name in ("diagnosis_analysis", "diagnostics", "behavior_signature"):
        source = subprocess.run(["git", "show", commit + ":xlayer_telemetry/analysis/" + name + ".py"],
                                cwd=ROOT, check=True, capture_output=True, text=True).stdout
        if name == "diagnostics":
            source = source.replace("from .diagnosis_analysis import", "from ._review_before_diagnosis_analysis import")
        key = "xlayer_telemetry.analysis._review_before_" + name
        module = types.ModuleType(key)
        sys.modules[key] = module
        exec(compile(source, name + "@" + commit, "exec"), module.__dict__)
        modules[name] = module
    return commit, modules


def step(stamp, *, duration=10, **changes):
    return {"record_id": str(stamp), "run_id": "fixture", "node": "node", "worker_id": "worker",
            "boundary_scope": "rl_step", "execution_mode": "sync", "observed_at": stamp,
            "step_duration_seconds": duration, "stage_durations_seconds": {"gen": duration},
            "analysis_window": {"start": stamp-duration, "end": stamp, "accuracy": "approximate"},
            "workload": {"perf/total_num_tokens": 1000, "policy_version": 128}, **changes}


class NoMetrics:
    def __init__(self):
        self.calls = 0

    def query_range(self, *args):
        self.calls += 1
        return None


def analyze(module, current, history, config=None):
    source = NoMetrics()
    report = module.DiagnosticEngine({"prometheus": {"url": "http://unused"}, **(config or {})},
                                     prometheus=source, clock=lambda: 1000).analyze(current, history)
    return report, source.calls


def paired_measures(calls, repeats):
    samples = {label: {"wall": [], "cpu": []} for label in calls}
    for call in calls.values():
        call()  # Warm imports and allocations; exclude input construction.
    for repeat in range(repeats):
        for label in list(calls)[::1 if repeat % 2 == 0 else -1]:
            gc.collect()
            started, cpu_started = time.perf_counter(), time.process_time()
            calls[label]()
            samples[label]["wall"].append(time.perf_counter()-started)
            samples[label]["cpu"].append(time.process_time()-cpu_started)
    results = {}
    for label, call in calls.items():
        gc.collect()
        tracemalloc.start()
        try:
            call()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        results[label] = {"wall_seconds": {"median": statistics.median(samples[label]["wall"]), "samples": samples[label]["wall"]},
                          "cpu_seconds": {"median": statistics.median(samples[label]["cpu"]), "samples": samples[label]["cpu"]},
                          "peak_incremental_python_bytes": peak}
    return results


def quality_cases(old):
    cases = {}
    for name, change in (("different_execution_mode", {"execution_mode": "async"}),
                         ("unknown_replay_window", {"analysis_window": {"start": None, "end": None, "accuracy": "unknown"}})):
        cases[name] = {}
        for label, module in (("before", old["diagnostics"]), ("after", diagnostics)):
            report, calls = analyze(module, step(100), [step(80, duration=1, **change)])
            cases[name][label] = {"baseline": report["comparison"]["baseline_record_id"],
                                  "slow_stages": len(report["symptom"]["slow_stages"]), "backend_requests": calls}
    cases["different_policy_with_tolerance"] = {}
    for label, module in (("before", old["diagnostics"]), ("after", diagnostics)):
        report, calls = analyze(module, step(100), [step(80, workload={"policy_version": 127})],
                                {"baseline": {"match_fields": ["policy_version"], "relative_tolerance": .1}})
        cases["different_policy_with_tolerance"][label] = {"baseline": report["comparison"]["baseline_record_id"], "backend_requests": calls}

    context = CorrelationContext("fixture", "sdk", "actor", "worker", "node")
    def span(boundary, name, duration, accuracy="exact"):
        return {**context.as_dict(), "step": boundary.step, "record_type": "span", "phase": "cpu", "name": name,
                "duration_seconds": duration, "boundary_accuracy": accuracy, "trace_id": "trace", "span_id": name,
                "attributes": {}}
    cases["unknown_slow_span_with_unrelated_exact_span"] = {}
    cases["incomplete_reference"] = {}
    for label, module in (("before", old["behavior_signature"]), ("after", behavior_signature)):
        before = module.Boundary(context, 0, 0, duration_seconds=1, accuracy="exact", workload={"tokens": 100})
        now = module.Boundary(context, 1, 1, duration_seconds=2, accuracy="exact", workload={"tokens": 100})
        observation = {"signal": "host_cpu_pressure_ratio", "scope": "node", "source": "fixture", "unit": "ratio",
                       "status": "observed", "accuracy": "sampled", "value": .4, "entity": {"node": "node"}}
        reference = module.summarize(before, [span(before, "slow", .1), span(before, "normal", .1)])
        current = module.summarize(now, [span(now, "slow", .4, "unknown"), span(now, "normal", .1)], [observation])
        row = module.candidates(current, module.compare(current, [reference]))[0]
        cases["unknown_slow_span_with_unrelated_exact_span"][label] = {"state": row["state"], "missing": row["missing"]}
        reference["quality"]["events_truncated"] = True
        current = module.summarize(now, [span(now, "slow", .4)], [observation])
        comparison = module.compare(current, [reference])
        row = module.candidates(current, comparison)[0]
        cases["incomplete_reference"][label] = {"reference_count": comparison["reference_count"], "state": row["state"]}
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--baseline-revision", default="c386d2a")
    parser.add_argument("--history-size", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if not 5 <= args.history_size <= 1_000_000 or not 1 <= args.repeats <= 31:
        parser.error("history-size must be 5..1000000; repeats must be 1..31")
    if args.output_dir.exists():
        parser.error("output-dir already exists; choose a new directory")
    commit, old = before_modules(args.baseline_revision)
    history = [step((index+1)*20) for index in range(args.history_size)]
    current = step((args.history_size+1)*20)
    old_report, old_calls = analyze(old["diagnostics"], current, history)
    new_report, new_calls = analyze(diagnostics, current, history)
    assert old_report["comparison"]["baseline_record_id"] == new_report["comparison"]["baseline_record_id"]
    assert old_report["symptom"]["slow_stages"] == new_report["symptom"]["slow_stages"]
    report = {"baseline_revision": commit, "python": platform.python_version(), "platform": platform.platform(),
              "measurement": "synthetic records and no-data backend fixture; Python costs only; input construction excluded",
              "history_size": args.history_size, "repeats": args.repeats, "quality_cases": quality_cases(old),
              "normal_equivalence": {"baseline_record_id": new_report["comparison"]["baseline_record_id"],
                                     "backend_requests_before": old_calls, "backend_requests_after": new_calls},
              **paired_measures({"before": lambda: analyze(old["diagnostics"], current, history),
                                  "after": lambda: analyze(diagnostics, current, history)}, args.repeats)}
    args.output_dir.mkdir(parents=True, exist_ok=False)
    with (args.output_dir / "report.json").open("x") as output:
        json.dump(report, output, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps({"report": str(args.output_dir / "report.json"), "history_size": args.history_size,
                      "normal_equivalence": report["normal_equivalence"],
                      **{label: {"wall_seconds": report[label]["wall_seconds"]["median"],
                                  "cpu_seconds": report[label]["cpu_seconds"]["median"],
                                  "peak_incremental_python_bytes": report[label]["peak_incremental_python_bytes"]}
                         for label in ("before", "after")}}, indent=2))


if __name__ == "__main__":
    main()
