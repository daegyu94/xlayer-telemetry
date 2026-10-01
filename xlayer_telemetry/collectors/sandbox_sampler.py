"""Read a stable Linux cgroup v2 sandbox-worker subtree.

One Prometheus series represents one stable worker cgroup, never a transient
sandbox ID. The kernel's hierarchical counters include its child cgroups.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import signal
import re
import time
from typing import Mapping

from ..measurements import finite_number
from ..metrics.prometheus import GaugeSample, write_gauges


PROM_LABELS = frozenset({"node", "role", "runtime", "filesystem", "deployment"})
COUNTERS = {"rbytes": "sandbox_io_read_bytes_total", "wbytes": "sandbox_io_write_bytes_total",
            "rios": "sandbox_io_read_ops_total", "wios": "sandbox_io_write_ops_total"}


def parse_io_devices(raw: str) -> dict[str, dict[str, int]]:
    devices = {}
    for line in raw.splitlines():
        parts = line.split()
        if not parts or not re.fullmatch(r"[0-9]+:[0-9]+", parts[0]):
            continue
        values = {}
        for part in parts[1:]:
            key, separator, value = part.partition("=")
            if separator and key in COUNTERS:
                number = int(value)
                if number < 0:
                    raise ValueError("negative io.stat counter")
                values[key] = number
        devices[parts[0]] = values
    return devices


def read_io_devices(directory: Path) -> dict[str, dict[str, int]]:
    try:
        return parse_io_devices((directory / "io.stat").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def parse_io_stat(raw: str) -> dict[str, int]:
    totals = {key: 0 for key in COUNTERS}
    for values in parse_io_devices(raw).values():
        for key, value in values.items():
            totals[key] += value
    return totals


def parse_pressure(raw: str) -> dict[str, int]:
    for line in raw.splitlines():
        parts = line.split()
        if parts and parts[0] == "some":
            fields = dict(item.split("=", 1) for item in parts[1:] if "=" in item)
            return {"some_total_usec": int(fields["total"])} if "total" in fields else {}
    return {}


def parse_key_values(raw: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) == 2:
            result[parts[0]] = int(parts[1])
    return result


def read_cgroup(directory: Path) -> dict[str, int]:
    """Missing controllers are omitted; absent values are never read as zero."""
    values: dict[str, int] = {}
    sources = {
        "io.stat": (parse_io_stat, ""),
        "io.pressure": (parse_pressure, "io_"),
        "cpu.stat": (parse_key_values, "cpu_"),
        "cpu.pressure": (parse_pressure, "cpu_pressure_"),
        "memory.current": (lambda raw: {"current": int(raw.strip())}, "memory_"),
        "memory.peak": (lambda raw: {"peak": int(raw.strip())}, "memory_"),
        "memory.events": (parse_key_values, "memory_event_"),
    }
    for filename, (parser, prefix) in sources.items():
        try:
            parsed = parser((directory / filename).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        values.update({prefix + key: value for key, value in parsed.items()})
    return values


def pressure_ratio(current: Mapping[str, int], previous: Mapping[str, int] | None,
                   elapsed_seconds: float, name: str) -> float | None:
    if previous is None or elapsed_seconds <= 0 or name not in current or name not in previous:
        return None
    delta = current[name] - previous[name]
    if delta < 0:
        return None
    return min(1.0, delta / (elapsed_seconds * 1_000_000))


def samples(values: Mapping[str, int], *, previous: Mapping[str, int] | None,
            elapsed_seconds: float, labels: Mapping[str, str]) -> list[GaugeSample]:
    if set(labels) != PROM_LABELS:
        raise ValueError("sandbox Prometheus labels must be node, role, runtime, filesystem, deployment")
    if labels["deployment"] not in {"colocated", "dedicated"}:
        raise ValueError("invalid deployment")
    output: list[GaugeSample] = []
    def add(name: str, value: float | None, kind: str = "gauge") -> None:
        if value is not None:
            output.append(GaugeSample(name, "Sandbox worker cgroup v2 observation.", value, labels, kind))
    for key, name in COUNTERS.items():
        add(name, values.get(key), "counter")
    for key, name in (("cpu_usage_usec", "sandbox_cpu_usage_seconds_total"),
                      ("memory_current", "sandbox_memory_bytes"),
                      ("memory_peak", "sandbox_memory_peak_bytes"),
                      ("memory_event_oom", "sandbox_oom_total"),
                      ("memory_event_oom_kill", "sandbox_oom_kill_total")):
        value = values.get(key)
        add(name, value / 1_000_000 if key == "cpu_usage_usec" and value is not None else value,
            "counter" if name.endswith("_total") else "gauge")
    for key, name in (("io_some_total_usec", "sandbox_io_pressure_ratio"),
                      ("cpu_pressure_some_total_usec", "sandbox_cpu_pressure_ratio")):
        add(name, pressure_ratio(values, previous, elapsed_seconds, key))
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cgroup", type=Path, required=True, help="Stable sandbox-worker cgroup v2 directory")
    parser.add_argument("--textfile-dir", type=Path, required=True)
    parser.add_argument("--textfile-name", default="sandbox.prom", help="Unique producer-owned .prom basename")
    parser.add_argument("--node", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--filesystem", required=True)
    parser.add_argument("--deployment", choices=("colocated", "dedicated"), required=True)
    parser.add_argument("--role", default="sandbox")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if Path(args.textfile_name).name != args.textfile_name or not args.textfile_name.endswith(".prom"):
        parser.error("textfile-name must be a .prom basename")
    if finite_number(args.interval) is None or args.interval <= 0 or not args.cgroup.is_dir():
        parser.error("interval must be positive and cgroup must be a directory")
    labels = {"node": args.node, "role": args.role, "runtime": args.runtime,
              "filesystem": args.filesystem, "deployment": args.deployment}
    def stop(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    previous: dict[str, int] | None = None
    previous_time = time.monotonic()
    destination = args.textfile_dir / args.textfile_name
    try:
        while True:
            now = time.monotonic()
            current = read_cgroup(args.cgroup)
            if not current:
                destination.unlink(missing_ok=True)
                parser.error(f"no readable cgroup v2 sources in {args.cgroup}")
            current_samples = samples(current, previous=previous,
                                      elapsed_seconds=now - previous_time, labels=labels)
            current_samples.append(GaugeSample(
                "sandbox_sample_timestamp_seconds", "Time of the latest sandbox cgroup sample.",
                time.time(), labels))
            write_gauges(args.textfile_dir, args.textfile_name, current_samples)
            if args.once:
                break
            previous, previous_time = current, now
            time.sleep(args.interval)
    except KeyboardInterrupt:
        destination.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
