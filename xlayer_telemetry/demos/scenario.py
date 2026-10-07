"""A bounded synthetic phase schedule shared by the live exporter and artifacts.

This is demo input, not a production telemetry schema. Explicit phase clocks let
real Prometheus scrapes overlap SDK spans without manufacturing historical samples.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..measurements import finite_number

_PHASES = (("rollout", "rollout.generate", "rollout", "pool-0", 12),
           ("reward", "reward", "trainer", "driver", 6),
           ("actor_update", "update_actor", "trainer", "driver", 8),
           ("weight_sync", "update_weights", "trainer", "driver", 6),
           ("checkpoint_save", "save_checkpoint", "trainer", "driver", 8))


def make_scenario(*, start: float, run_id: str, node: str, step: int = 127,
                  current: str = "storage-regression") -> dict[str, Any]:
    if current not in {"normal", "storage-regression"}:
        raise ValueError("invalid synthetic scenario")
    frames = []
    cursor = start
    for offset, scenario in enumerate(("normal", current)):
        phases = []
        begin = cursor
        for phase, operation, role, worker, duration in _PHASES:
            seconds = duration + (8 if scenario == "storage-regression" and phase == "rollout" else 0)
            phases.append({"phase": phase, "operation": operation, "role": role,
                           "worker_id": worker, "start": cursor, "end": cursor + seconds})
            cursor += seconds
        frames.append({"step": step + offset, "scenario": scenario, "start": begin, "end": cursor,
                       "phases": phases, "policy_version": 128,
                       "signals": {"step_duration_seconds": cursor - begin,
                                   "threefs_p99_latency": 14 if scenario == "storage-regression" else 2,
                                   "storage_device_busy_ratio": .96 if scenario == "storage-regression" else .31,
                                   "threefs_throughput_bytes_per_second": 110 if scenario == "storage-regression" else 100,
                                   "gpu_utilization_percent": 47 if scenario == "storage-regression" else 91,
                                   "network_utilization_ratio": .2,
                                   "vllm_requests_waiting": 14 if scenario == "storage-regression" else 1}})
    # Producer-declared Step summaries use the same phase inputs. They remain
    # scenario summaries, not queried Prometheus/ClickHouse observations.
    for frame in frames:
        observations = [(p["end"] - p["start"], phase_values(frame, p)) for p in frame["phases"]]
        frame["signals"]["gpu_utilization_percent"] = sum(seconds * values["gpu"] for seconds, values in observations) / (frame["end"] - frame["start"])
        frame["signals"]["rollout_duration_seconds"] = frame["phases"][0]["end"] - frame["phases"][0]["start"]
        frame["signal_statistics"] = {name: "synthetic_scenario_value" for name in frame["signals"]}
        frame["signal_statistics"].update(gpu_utilization_percent="synthetic_scenario_time_weighted_mean",
                                         storage_device_busy_ratio="synthetic_scenario_max",
                                         vllm_requests_waiting="synthetic_scenario_max",
                                         rollout_duration_seconds="synthetic_sdk_call_duration")
    schedule = {"schema_version": 1, "data_origin": "synthetic", "run_id": run_id, "node": node,
                "workload": {"batch_size": 64, "prompt_tokens": 256, "response_tokens": 512, "tool_calls": 1},
                "frames": frames}
    validate_scenario(schedule)
    return schedule


def validate_scenario(schedule: Any) -> dict[str, Any]:
    """Validate bounded local demo input before changing a server response."""
    if not isinstance(schedule, dict) or schedule.get("schema_version") != 1 or schedule.get("data_origin") != "synthetic":
        raise ValueError("invalid scenario header")
    from ..events import CorrelationContext
    CorrelationContext(run_id=schedule.get("run_id", ""), node=schedule.get("node", ""),
                       producer="scenario", role="trainer", worker_id="driver")
    frames = schedule.get("frames")
    if not isinstance(frames, list) or len(frames) != 2 or not isinstance(schedule.get("workload"), dict):
        raise ValueError("invalid scenario frames/workload")
    dimensions = schedule["workload"]
    if set(dimensions) != {"batch_size", "prompt_tokens", "response_tokens", "tool_calls"} or any(type(value) is not int or not 0 <= value <= 1000000 for value in dimensions.values()):
        raise ValueError("invalid scenario workload dimensions")
    previous = None
    for frame in frames:
        if not isinstance(frame, dict) or type(frame.get("step")) is not int or frame["step"] < 0 or frame.get("scenario") not in {"normal", "storage-regression"}:
            raise ValueError("invalid scenario frame")
        if type(frame.get("policy_version")) is not int or not 0 <= frame["policy_version"] <= 2**53:
            raise ValueError("invalid scenario policy version")
        start, end = frame.get("start"), frame.get("end")
        if finite_number(start) is None or finite_number(end) is None or end <= start or end - start > 120:
            raise ValueError("invalid scenario interval")
        if previous is not None and start != previous:
            raise ValueError("noncontiguous scenario intervals")
        phases = frame.get("phases")
        if not isinstance(phases, list) or len(phases) != len(_PHASES):
            raise ValueError("invalid scenario phases")
        cursor = start
        for phase, expected in zip(phases, _PHASES):
            if not isinstance(phase, dict) or (phase.get("phase"), phase.get("operation"), phase.get("role"), phase.get("worker_id")) != expected[:4]:
                raise ValueError("invalid scenario phase identity")
            if finite_number(phase.get("start")) is None or finite_number(phase.get("end")) is None or phase["start"] != cursor or not 6 <= phase["end"] - phase["start"] <= 30:
                raise ValueError("invalid scenario phase interval")
            cursor = phase["end"]
        if cursor != end or not isinstance(frame.get("signals"), dict) or set(frame["signals"]) != {"step_duration_seconds", "threefs_p99_latency", "storage_device_busy_ratio", "threefs_throughput_bytes_per_second", "gpu_utilization_percent", "network_utilization_ratio", "vllm_requests_waiting", "rollout_duration_seconds"} or any(finite_number(value) is None for value in frame["signals"].values()):
            raise ValueError("invalid scenario signals/boundary")
        previous = end
    if frames[1]["step"] != frames[0]["step"] + 1:
        raise ValueError("invalid scenario step sequence")
    return schedule


def load_scenario(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("scenario state exceeds size limit")
    try:
        return validate_scenario(json.loads(raw))
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("invalid scenario JSON") from error


def frame_at(schedule: dict[str, Any], when: float) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for frame in schedule["frames"]:
        if frame["start"] <= when < frame["end"]:
            return frame, next(p for p in frame["phases"] if p["start"] <= when < p["end"])
    return None


def phase_values(frame: dict[str, Any], phase: dict[str, Any]) -> dict[str, float]:
    """Phase-varying native inputs; shared metrics keep their actual scope."""
    name = phase["phase"]
    slow = frame["scenario"] == "storage-regression" and name in {"rollout", "checkpoint_save"}
    gpu = (61 if name == "rollout" else 10) if slow else {"rollout": 91, "reward": 32, "actor_update": 94, "weight_sync": 28, "checkpoint_save": 18}[name]
    return {"gpu": gpu, "tokens": 4700 if slow else 7600, "step": frame["end"] - frame["start"],
            "read": 6 if slow and name == "rollout" else .5, "write": 5 if name == "checkpoint_save" else .2,
            "rx": 420 if slow else 180, "tx": 330 if name == "weight_sync" else 150,
            "busy": .96 if slow else .31, "waiting": 14 if slow else 1,
            "kv_hit": .54 if slow else .78, "sandbox_pressure": .43 if slow else .02,
            "kv_slow": float(slow)}
