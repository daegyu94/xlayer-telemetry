"""Collect supported nvidia-smi fields without substituting zero for N/A."""

import argparse
import csv
import json
import math
import socket
import subprocess
import time
from pathlib import Path

from xlayer_telemetry.metrics.prometheus import GaugeSample, write_gauges


def optional_number(value: str) -> float | None:
    try:
        result = float(value.strip())
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def query(kind: str, fields: list[str]) -> list[list[str]]:
    result = subprocess.run(
        ["nvidia-smi", f"--query-{kind}={','.join(fields)}", "--format=csv,noheader,nounits"],
        check=True, text=True, capture_output=True, timeout=10,
    )
    return list(csv.reader(result.stdout.splitlines(), skipinitialspace=True))


def snapshot(*, include_processes: bool = False, max_processes: int = 256) -> dict:
    """Sample devices; per-PID diagnostics are opt-in to avoid series churn."""
    if not 1 <= max_processes <= 4096:
        raise ValueError("max_processes must be between 1 and 4096")
    fields = ["index", "utilization.gpu", "power.draw", "temperature.gpu", "clocks.sm", "memory.used", "memory.total"]
    gpus = []
    for row in query("gpu", fields + ["uuid"]):
        gpu = {key: optional_number(row[index]) if index < len(row) else None
               for index, key in enumerate(fields)}
        index = gpu["index"]
        if index is None or index < 0 or not index.is_integer():
            # Do not invent an identity or crash all device collection on a bad row.
            continue
        gpu["unavailable_fields"] = [key for key in fields if gpu[key] is None]
        gpu["uuid"] = row[len(fields)] if len(row) > len(fields) else None
        gpus.append(gpu)
    processes = []
    process_error = None
    process_truncated = 0
    if include_processes:
        try:
            rows = query("compute-apps", ["gpu_uuid", "pid", "process_name", "used_gpu_memory"])
            process_truncated = max(0, len(rows) - max_processes)
            seen_processes = set()
            for row in rows[:max_processes]:
                try:
                    pid = int(row[1])
                    identity = (row[0], pid)
                    if pid <= 0 or identity in seen_processes:
                        raise ValueError("invalid or duplicate compute process identity")
                    seen_processes.add(identity)
                    processes.append({"gpu_uuid": row[0], "pid": pid, "process_name": row[2],
                                      "used_gpu_memory_mib": optional_number(row[3])})
                except (IndexError, ValueError):
                    process_error = "invalid_compute_process_row"
        except (OSError, subprocess.SubprocessError) as exc:
            process_error = type(exc).__name__
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        if key in {"MemTotal", "MemAvailable", "MemFree", "Buffers", "Cached", "SwapTotal", "SwapFree"}:
            memory[key + "_bytes"] = int(value.split()[0]) * 1024
    return {"timestamp": time.time(), "hostname": socket.gethostname(), "host_memory": memory,
            "gpus": gpus, "compute_processes": processes,
            "compute_processes_enabled": include_processes,
            "compute_processes_truncated": process_truncated,
            **({"compute_processes_error": process_error} if process_error else {}),
            "null_reason": "nvidia-smi field unavailable; not zero"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--textfile-dir", type=Path)
    parser.add_argument(
        "--duration",
        type=float,
        help="stop after this many seconds; omit to run until interrupted",
    )
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--process-metrics", action="store_true",
                        help="opt in to high-churn PID memory samples for short diagnostics")
    parser.add_argument("--max-processes", type=int, default=256,
                        help="maximum PID rows retained per sample (1-4096)")
    args = parser.parse_args()
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        parser.error("duration must be finite and positive")
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("interval must be finite and positive")
    if not 1 <= args.max_processes <= 4096:
        parser.error("max-processes must be between 1 and 4096")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.duration if args.duration is not None else None
    with args.output.open("x") as output:
        while deadline is None or time.monotonic() < deadline:
            value = snapshot(include_processes=args.process_metrics, max_processes=args.max_processes)
            output.write(json.dumps(value) + "\n")
            output.flush()
            if args.textfile_dir:
                samples = [GaugeSample("telemetry_gpu_sample_timestamp_seconds", "Last successful GPU sample.", value["timestamp"])]
                samples.append(GaugeSample("telemetry_gpu_process_collection_enabled",
                                           "Whether per-PID GPU memory diagnostics are enabled.",
                                           int(args.process_metrics)))
                if args.process_metrics and not value.get("compute_processes_error"):
                    samples.append(GaugeSample("telemetry_gpu_process_samples_truncated",
                                               "Process rows omitted by the per-sample diagnostic cap.",
                                               value.get("compute_processes_truncated", 0)))
                gpu_indices = {}
                for gpu in value["gpus"]:
                    labels = {"gpu": str(int(gpu["index"]))}
                    if gpu.get("uuid"):
                        labels["gpu_uuid"] = gpu["uuid"]
                        gpu_indices[gpu["uuid"]] = labels["gpu"]
                    for field, name in [("utilization.gpu", "utilization_percent"), ("power.draw", "power_watts"), ("temperature.gpu", "temperature_celsius"), ("clocks.sm", "sm_clock_mhz")]:
                        if gpu[field] is not None:
                            samples.append(GaugeSample(f"telemetry_gpu_{name}", f"nvidia-smi {field}.", gpu[field], labels))
                    for field, name in (("memory.used", "memory_used_bytes"), ("memory.total", "memory_total_bytes")):
                        if gpu[field] is not None:
                            samples.append(GaugeSample(
                                f"telemetry_gpu_{name}", f"nvidia-smi device {field}, converted from MiB to bytes.",
                                gpu[field] * 1024**2, labels,
                            ))
                for process in value["compute_processes"]:
                    if process["used_gpu_memory_mib"] is not None:
                        labels = {"pid": str(process["pid"]), "gpu_uuid": process["gpu_uuid"]}
                        if process["gpu_uuid"] in gpu_indices:
                            labels["gpu"] = gpu_indices[process["gpu_uuid"]]
                        samples.append(GaugeSample(
                            "telemetry_gpu_process_memory_bytes",
                            "nvidia-smi compute-process GPU memory; not total unified memory.",
                            process["used_gpu_memory_mib"] * 1024**2,
                            labels,
                        ))
                write_gauges(args.textfile_dir, "gpu.prom", samples)
            sleep_seconds = args.interval
            if deadline is not None:
                sleep_seconds = min(args.interval, max(0, deadline - time.monotonic()))
            time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()
