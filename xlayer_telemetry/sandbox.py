"""Optional sandbox lifecycle spans over the existing EventRecorder."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import time
import re
from typing import Any, Iterator, Mapping

from .events import EventRecorder, SpanIdentity
from .sandbox_sampler import pressure_ratio, read_cgroup, read_io_devices


LIFECYCLE_OPERATIONS = frozenset({"queue", "acquire", "prepare", "exec", "reset", "release"})
DEPLOYMENTS = frozenset({"colocated", "dedicated"})


class SandboxRecorder:
    """Add sandbox context to exact spans without managing the sandbox itself."""

    def __init__(self, events: EventRecorder, *, runtime: str, filesystem: str,
                 deployment: str, sandbox_node: str) -> None:
        if deployment not in DEPLOYMENTS:
            raise ValueError(f"invalid sandbox deployment: {deployment!r}")
        if not all((runtime, filesystem, sandbox_node)):
            raise ValueError("runtime, filesystem and sandbox_node are required")
        self.events = events
        self.attributes = {"runtime": runtime, "filesystem": filesystem,
                           "deployment": deployment, "sandbox_node": sandbox_node}

    @contextmanager
    def span(self, operation: str, *, step: int | None = None,
             trajectory_id: str | None = None, sandbox_id: str | None = None,
             trace_id: str | None = None, parent_span_id: str | None = None,
             attributes: Mapping[str, Any] | None = None,
             cgroup: Path | None = None, device_major_minor: str | None = None) -> Iterator[SpanIdentity]:
        if operation not in LIFECYCLE_OPERATIONS:
            raise ValueError(f"unsupported sandbox operation: {operation!r}")
        details = {**dict(attributes or {}), **self.attributes}
        if trajectory_id is not None:
            details["trajectory_id"] = trajectory_id
        if sandbox_id is not None:
            details["sandbox_id"] = sandbox_id
        if device_major_minor is not None and not re.fullmatch(r"[0-9]+:[0-9]+", device_major_minor):
            raise ValueError("device_major_minor must be major:minor")
        devices_before = read_io_devices(cgroup) if cgroup is not None else {}
        before = read_cgroup(cgroup) if cgroup is not None else None
        started = time.monotonic()
        with self.events.span(
            f"sandbox.{operation}", phase="environment", step=step,
            attributes=details, trace_id=trace_id,
            parent_span_id=parent_span_id,
        ) as identity:
            try:
                yield identity
            finally:
                if before is not None:
                    after = read_cgroup(cgroup)
                    devices_after = read_io_devices(cgroup)
                    device_deltas = {
                        device: {key: value-devices_before[device][key]
                                 for key, value in values.items()
                                 if key in devices_before.get(device, {}) and value >= devices_before[device][key]}
                        for device, values in devices_after.items() if device in devices_before
                    }
                    deltas = {
                        "io_devices": device_deltas,
                        "device_mapping": {
                            "configured_major_minor": device_major_minor,
                            "status": "unconfigured" if device_major_minor is None else "observed" if device_major_minor in device_deltas else "unmatched",
                            "observed_major_minors": sorted(device_deltas),
                            "attribution": "cgroup_block_io_not_tool_or_device_ownership",
                        },
                    }
                    for key, name in (("rbytes", "io_read_bytes_delta"),
                                      ("wbytes", "io_write_bytes_delta"),
                                      ("rios", "io_read_ops_delta"),
                                      ("wios", "io_write_ops_delta"),
                                      ("cpu_usage_usec", "cpu_usage_usec_delta")):
                        if key in before and key in after and after[key] >= before[key]:
                            deltas[name] = after[key] - before[key]
                    pressure = pressure_ratio(after, before, time.monotonic() - started,
                                              "io_some_total_usec")
                    if pressure is not None:
                        deltas["io_pressure_ratio"] = pressure
                    for key in ("memory_current", "memory_peak", "memory_event_oom",
                                "memory_event_oom_kill"):
                        if key in after:
                            deltas[key] = after[key]
                    if deltas:
                        self.events.event(
                            "sandbox.resource_sample", phase="environment", step=step,
                            trace_id=identity.trace_id, span_id=identity.span_id,
                            attributes={**details, "observation_scope": "cgroup", **deltas},
                        )


def device_window(directory: Path, run_id: str, node: str, start: float, end: float,
                  configured_major_minor: str | None = None) -> dict:
    """Inspect bounded event evidence, not a sum of overlapping cgroup deltas."""
    from .fileio import json_objects
    from .measurements import finite_number
    observations = []
    observed_devices = set()
    observation_count = 0
    for path in directory.glob("sandbox*.jsonl"):
        try:
            for item in json_objects(path):
                stamp = finite_number(item.get("timestamp_unix_nano"))
                attributes = item.get("attributes", {})
                if (item.get("name") != "sandbox.resource_sample" or item.get("run_id") != run_id
                        or not isinstance(attributes, dict) or attributes.get("sandbox_node") != node
                        or stamp is None or not start <= stamp/1e9 <= end):
                    continue
                devices = attributes.get("io_devices", {})
                if not isinstance(devices, dict):
                    continue
                observation_count += 1
                observed_devices.update(devices)
                if len(observations) < 20:
                    observations.append({"trace_id": item.get("trace_id"), "span_id": item.get("span_id"),
                                         "io_devices": devices})
        except OSError:
            continue
    return {"configured_major_minor": configured_major_minor,
            "status": "unconfigured" if not configured_major_minor else "observed" if configured_major_minor in observed_devices else "unmatched" if observed_devices else "unknown",
            "observed_major_minors": sorted(observed_devices), "observations": observations,
            "observation_count": observation_count, "truncated": observation_count > len(observations),
            "scope": "cgroup_event_window", "limitation": "Configured major:minor is supplied by the operator; simultaneous device busy does not prove tool ownership."}
