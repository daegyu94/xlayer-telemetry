"""Synthetic observations, independent model diagnoses, and explicit structural checks.

Case names and expectations are never sent to the model. These checks measure the
output contract and evidence coverage; semantic accuracy requires manual review.
Run from the checkout with PYTHONPATH=. python examples/local-llm/evaluate.py ...
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from xlayer_telemetry.analysis.llm_diagnosis import diagnose, failure_record, write_result


# signal, baseline, unit, scope, labels
BASE = [
    ("step_duration_seconds", 10, "seconds", "application", {"node": "gpu-0"}),
    ("rollout_duration_seconds", 5, "seconds", "application", {"node": "gpu-0"}),
    ("gpu_utilization_percent", 90, "percent", "device", {"node": "gpu-0", "gpu": "0"}),
    ("storage_p99_latency_seconds", .002, "seconds", "shared-service", {"service": "3fs"}),
    ("storage_device_busy_ratio", .2, "ratio", "device", {"node": "storage-0", "device": "nvme0n1"}),
    ("storage_throughput_bytes_per_second", 100_000_000, "bytes/second", "shared-service", {"service": "3fs"}),
    ("storage_network_utilization_ratio", .2, "ratio", "network-interface", {"node": "storage-0", "device": "eth0"}),
    ("vllm_waiting_requests", 0, "requests", "service", {"node": "gpu-0", "engine": "A"}),
    ("vllm_kv_cache_usage_ratio", .4, "ratio", "service", {"node": "gpu-0", "engine": "A"}),
    ("vllm_preemptions_increase", 0, "events in interval", "service", {"node": "gpu-0", "engine": "A"}),
    ("host_memory_available_ratio", .5, "ratio", "node", {"node": "gpu-0"}),
    ("host_swap_bytes_per_second", 0, "bytes/second", "node", {"node": "gpu-0"}),
    ("tool_duration_seconds", 2, "seconds", "application", {"tool": "pytest", "node": "sandbox-0"}),
    ("sandbox_io_pressure_ratio", .01, "ratio", "cgroup", {"node": "sandbox-0", "worker": "sandbox-worker"}),
    ("sandbox_device_busy_ratio", .2, "ratio", "device", {"node": "sandbox-0", "device": "nvme1n1"}),
    ("weight_sync_duration_seconds", 1, "seconds", "application", {"node": "gpu-0"}),
    ("rdma_bytes_per_second", 1_000_000_000, "bytes/second", "network-interface", {"node": "gpu-0", "device": "mlx5_0"}),
]


def scenarios():
    definitions = [
        ("normal", {}, [], "no_issue_observed"),
        ("storage_device", {1: 20, 3: 45, 4: .015, 5: .98, 6: 103_000_000}, [4, 5], "bottleneck_suspected"),
        ("storage_network", {1: 20, 3: 45, 4: .015, 5: .25, 7: .97}, [4, 7], "bottleneck_suspected"),
        ("rollout_queue", {1: 20, 2: 15, 8: 30}, [2, 8], "bottleneck_suspected"),
        ("kv_pressure", {1: 20, 2: 15, 8: 30, 9: .99, 10: 50}, [9, 10], "bottleneck_suspected"),
        ("host_memory", {1: 22, 3: 40, 11: .03, 12: 200_000_000}, [11, 12], "bottleneck_suspected"),
        ("communication", {1: 20, 3: 40, 16: 8, 17: 3_000_000_000}, [16, 17], "bottleneck_suspected"),
        ("sandbox_io", {1: 25, 3: 40, 13: 20, 14: .6, 15: .99}, [13, 14, 15], "bottleneck_suspected"),
        ("insufficient", {1: 20, 3: 40}, [], "insufficient_evidence"),
        ("mixed_engines", {1: 20, 9: .99}, [], None),
        ("unknown_time", {1: 20, 4: .015, 5: .98}, [], "insufficient_evidence"),
        ("straggler", {1: 24}, [18, 19, 20], "bottleneck_suspected"),
        ("larger_workload", {1: 20, 2: 10}, [], "no_issue_observed"),
    ]
    result = []
    for index, (name, changes, required, assessment) in enumerate(definitions):
        observations = [{
            "id": f"m{i}", "signal": signal, "current": changes.get(i, value), "baseline": value,
            "unit": unit, "observation_scope": scope, "labels": labels, "source": "synthetic_fixture",
        } for i, (signal, value, unit, scope, labels) in enumerate(BASE, 1)]
        packet = {
            "schema_version": 1, "record_type": "llm_observation_packet", "data_origin": "synthetic",
            "context": {"run_id": f"fixture-{index:02d}", "step": 7, "workload": "Agent RL",
                        "question": "What may explain the workload's performance in this interval?"},
            "current_interval": {"start": 1700000100, "end": 1700000130, "accuracy": "exact"},
            "baseline_interval": {"start": 1700000040, "end": 1700000070, "accuracy": "exact"},
            "observations": observations, "missing_sources": [],
            "topology": [{"from": "gpu-0", "to": "storage-0", "purpose": "training data"},
                         {"from": "gpu-0", "to": "sandbox-0", "purpose": "tool RPC"}],
            "limitations": ["Synthetic scalar interval summaries; shared metrics have no per-run byte attribution."],
        }
        if name == "insufficient":
            packet["observations"] = [observations[0], observations[2]]
            packet["missing_sources"] = ["storage", "network", "vllm", "host_memory", "tool_events"]
        if name == "mixed_engines":
            for source_id, value in ((8, 5), (9, .1), (10, 2)):
                row = deepcopy(observations[source_id-1])
                row.update(id=f"m{len(observations)+1}", current=value)
                row["labels"]["engine"] = "B"
                observations.append(row)
        if name == "unknown_time":
            packet["current_interval"] = {"start": None, "end": None, "accuracy": "unknown"}
            packet["missing_sources"] = ["step_event_time"]
        if name == "straggler":
            for rank, duration in enumerate((10, 10.5, 24)):
                observations.append({"id": f"m{18+rank}", "signal": "participant_duration_seconds",
                                     "current": duration, "baseline": 10, "unit": "seconds",
                                     "observation_scope": "worker", "labels": {"rank": str(rank)},
                                     "source": "synthetic_fixture"})
        if name == "larger_workload":
            observations.append({"id": "m18", "signal": "generated_output_tokens", "current": 2000,
                                 "baseline": 1000, "unit": "tokens", "observation_scope": "application",
                                 "labels": {}, "source": "synthetic_fixture"})
            packet["context"]["workload_change"] = "Same model, batch and token generation rate; twice as many output tokens requested."
        result.append((name, packet, {"assessment": assessment, "primary_evidence_ids": [f"m{i}" for i in required]}))
    return result


def checks(result, expected):
    answer = result["diagnosis"]
    primary = answer["candidates"][0] if answer["candidates"] else {}
    cited = set(primary.get("evidence_ids", []) + primary.get("counter_evidence_ids", []))
    return {
        "expected_assessment": expected["assessment"], "actual_assessment": answer["assessment"],
        "assessment_match": (answer["assessment"] == expected["assessment"]
                             if expected["assessment"] is not None else None),
        "primary_evidence_coverage": (set(expected["primary_evidence_ids"]) <= cited
                                      if expected["primary_evidence_ids"] else None),
        "primary_title": primary.get("title"),
        "latency_seconds": result["latency_seconds"],
        "input_sha256": result["input_sha256"], "seed": result["seed"],
        "semantic_review_decision": result.get("semantic_review", {}).get("decision"),
        "semantic_review_issues": result.get("semantic_review", {}).get("issues", []),
        "semantic_review_required": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.5:27b")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--case", action="append", help="run only named cases")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--generate-only", action="store_true")
    args = parser.parse_args()
    cases = scenarios()
    if args.repeats < 1:
        parser.error("repeats must be at least 1")
    if args.case and set(args.case) - {name for name, _, _ in cases}:
        parser.error("unknown case; choose from: " + ", ".join(name for name, _, _ in cases))
    args.output.mkdir(parents=True, exist_ok=True)
    summary = []
    write_result(args.output / "summary.json", summary)
    for name, packet, expected in cases:
        if args.case and name not in args.case:
            continue
        for repeat in range(args.repeats):
            directory = args.output / f"{name}-{repeat+1}"
            directory.mkdir(exist_ok=True)
            for filename in ("diagnosis.json", "rejected-response.json"):
                (directory / filename).unlink(missing_ok=True)
            write_result(directory / "observations.json", packet)
            if args.generate_only:
                continue
            print(f"Diagnosing {name} repetition {repeat+1}", flush=True)
            try:
                result = diagnose(packet, model=args.model, endpoint=args.endpoint, seed=args.seed+repeat)
                write_result(directory / "diagnosis.json", result)
                row = {"case": name, "repeat": repeat+1, **checks(result, expected)}
            except Exception as error:
                row = {"case": name, "repeat": repeat+1, "error": str(error)}
                write_result(directory / "diagnosis.json", failure_record(error, model=args.model, packet=packet))
                if hasattr(error, "response"):
                    write_result(directory / "rejected-response.json", error.response)
            summary.append(row)
            write_result(args.output / "summary.json", summary)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    if any(row.get("error") or row.get("assessment_match") is False
           or row.get("primary_evidence_coverage") is False for row in summary):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
