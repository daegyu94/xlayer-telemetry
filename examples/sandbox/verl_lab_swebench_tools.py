"""Optional XLayer wrapper for verl-lab's SWE-Bench function tools.

Set VERL_LAB_TOOLS_PATH to its existing swebench_agent_tools.py and point both
veRL FUNCTION_TOOL_PATH and CUSTOM_REWARD_FUNCTION_PATH at this file. The
underlying tool and reward implementation remains owned by verl-lab.
"""

from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

from verl.tools.function_tool import FUNCTION_TOOL_REGISTRY

from xlayer_telemetry.events import EventRecorder
from xlayer_telemetry.sandbox import SandboxRecorder


_ACTIVE_TOOL = ContextVar("xlayer_active_tool", default=None)


def _observed_tool(name, fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        events = EventRecorder.from_env(
            producer="agent", role="rollout", worker_id=str(os.getpid()))
        if events is None:
            return fn(*args, **kwargs)
        with events.span("tool.call", phase="tool_interaction",
                         attributes={"tool": name}) as identity:
            token = _ACTIVE_TOOL.set(identity)
            try:
                return fn(*args, **kwargs)
            finally:
                _ACTIVE_TOOL.reset(token)
    return wrapped


class _ObservedSubprocess:
    """Intercept only the grader's Docker call inside the imported module."""

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def run(self, command, *args, **kwargs):
        if isinstance(command, (list, tuple)) and command and command[0] == "docker":
            events = EventRecorder.from_env(
                producer="sandbox", role="sandbox", worker_id=str(os.getpid()))
            if events is not None:
                parent = _ACTIVE_TOOL.get()
                sandbox = SandboxRecorder(
                    events, runtime="docker", filesystem="overlayfs",
                    deployment="colocated", sandbox_node=events.context.node)
                with sandbox.span(
                    "exec", trace_id=parent.trace_id if parent else None,
                    parent_span_id=parent.span_id if parent else None,
                    attributes={"tool": "test_patch"},
                ):
                    return subprocess.run(command, *args, **kwargs)
        return subprocess.run(command, *args, **kwargs)


source_path = Path(os.environ["VERL_LAB_TOOLS_PATH"]).resolve()
if source_path == Path(__file__).resolve() or not source_path.is_file():
    raise ValueError("VERL_LAB_TOOLS_PATH must name verl-lab's original tool file")
spec = importlib.util.spec_from_file_location("_xlayer_verl_lab_tools", source_path)
if spec is None or spec.loader is None:
    raise ImportError(f"cannot import {source_path}")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

for tool_name in ("read_source", "test_patch"):
    entry = FUNCTION_TOOL_REGISTRY[tool_name]
    entry.fn = _observed_tool(tool_name, entry.fn)
module.subprocess = _ObservedSubprocess()
compute_score = module.compute_score
