"""Optional sandbox lifecycle spans over the existing EventRecorder."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import time
from typing import Any, Iterator, Mapping

from .events import EventRecorder, SpanIdentity
from .sandbox_sampler import pressure_ratio, read_cgroup


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
             cgroup: Path | None = None) -> Iterator[SpanIdentity]:
        if operation not in LIFECYCLE_OPERATIONS:
            raise ValueError(f"unsupported sandbox operation: {operation!r}")
        details = {**dict(attributes or {}), **self.attributes}
        if trajectory_id is not None:
            details["trajectory_id"] = trajectory_id
        if sandbox_id is not None:
            details["sandbox_id"] = sandbox_id
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
                    deltas = {}
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
