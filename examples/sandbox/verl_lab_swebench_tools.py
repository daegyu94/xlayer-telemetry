"""Optional XLayer wrapper for verl-lab's SWE-Bench function tools.

Set VERL_LAB_TOOLS_PATH to its existing swebench_agent_tools.py and point both
veRL FUNCTION_TOOL_PATH and CUSTOM_REWARD_FUNCTION_PATH at this file. The
underlying tool and reward implementation remains owned by verl-lab.
"""

from __future__ import annotations

from contextvars import ContextVar
import difflib
from functools import wraps
import importlib.util
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

from verl.tools.function_tool import FUNCTION_TOOL_REGISTRY, function_tool

from xlayer_telemetry.events import EventRecorder
from xlayer_telemetry.sandbox import SandboxRecorder


_ACTIVE_TOOL = ContextVar("xlayer_active_tool", default=None)
_ACTIVE_TOOL_NAME = ContextVar("xlayer_active_tool_name", default=None)


class _InvalidToolArguments(ValueError):
    pass


def _observed_tool(name, fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        def invoke():
            try:
                inspect.signature(fn).bind(*args, **kwargs)
            except TypeError as exc:
                raise _InvalidToolArguments(str(exc)) from exc
            return fn(*args, **kwargs)

        events = EventRecorder.from_env(
            producer="agent", role="rollout", worker_id=str(os.getpid()))
        try:
            if events is None:
                return invoke()
            with events.span("tool.call", phase="tool_interaction",
                             attributes={"tool": name}) as identity:
                token = _ACTIVE_TOOL.set(identity)
                name_token = _ACTIVE_TOOL_NAME.set(name)
                try:
                    return invoke()
                finally:
                    _ACTIVE_TOOL_NAME.reset(name_token)
                    _ACTIVE_TOOL.reset(token)
        except _InvalidToolArguments as exc:
            return json.dumps({"error": "invalid_tool_arguments", "tool": name,
                               "detail": str(exc),
                               "allowed_arguments": list(inspect.signature(fn).parameters)},
                              sort_keys=True)
    return wrapped


class _ObservedSubprocess:
    """Intercept only the grader's Docker call inside the imported module."""

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def run(self, command, *args, **kwargs):
        if (isinstance(command, (list, tuple))
                and tuple(command[:2]) == ("docker", "run")):
            parent_cgroup = os.environ.get("XLAYER_SANDBOX_CGROUP_PARENT", "").strip()
            if parent_cgroup:
                if any(part == "--cgroup-parent" or part.startswith("--cgroup-parent=")
                       for part in command[2:]):
                    raise ValueError("Docker grader already sets --cgroup-parent")
                command = [*command[:2], "--cgroup-parent", parent_cgroup, *command[2:]]
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
                    attributes={"tool": _ACTIVE_TOOL_NAME.get() or "reward_grader",
                                **({"cgroup_parent": parent_cgroup} if parent_cgroup else {})},
                ) as identity:
                    try:
                        result = subprocess.run(command, *args, **kwargs)
                    except subprocess.TimeoutExpired:
                        sandbox.execution_result(identity, outcome="timeout")
                        raise
                    except subprocess.CalledProcessError as error:
                        sandbox.execution_result(identity, outcome="nonzero_exit", exit_code=error.returncode)
                        raise
                    except OSError as error:
                        sandbox.execution_result(identity, outcome="launch_error", error_type=type(error).__name__)
                        raise
                    sandbox.execution_result(identity, outcome="completed" if result.returncode == 0 else "nonzero_exit",
                                             exit_code=result.returncode)
                    return result
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

@function_tool("edit_and_test")
def edit_and_test(path: str, old: str, new: str) -> str:
    """Replace one exact source fragment and test the generated patch in the sandbox.

    Args:
        path: Repository-relative source path.
        old: Exact existing source fragment, without line numbers.
        new: Replacement source fragment, without line numbers.
    """
    if (path != module.TARGET or not isinstance(old, str) or not isinstance(new, str)
            or not old or old == new or len(old) > 2000 or len(new) > 2000):
        return json.dumps({"patch_applies": False, "regression_passes": False,
                           "reason": "invalid edit arguments"}, sort_keys=True)
    source = (module.BASE / path).read_text(encoding="utf-8")
    if source.count(old) != 1:
        return json.dumps({"patch_applies": False, "regression_passes": False,
                           "reason": "old fragment must occur exactly once"}, sort_keys=True)
    revised = source.replace(old, new, 1)
    lines = list(difflib.unified_diff(
        source.splitlines(keepends=True), revised.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    ))
    patch = f"diff --git a/{path} b/{path}\n" + "".join(lines)
    result = module.evaluate_patch(patch)
    module._record(module.TRACE, {"tool": "edit_and_test", "path": path,
                                  "patch_applies": result["patch_applies"],
                                  "regression_passes": result["regression_passes"],
                                  "reason": result["reason"]})
    return json.dumps({**result, "patch": patch}, sort_keys=True)


for tool_name in ("read_source", "test_patch", "edit_and_test"):
    entry = FUNCTION_TOOL_REGISTRY[tool_name]
    entry.fn = _observed_tool(tool_name, entry.fn)
if os.environ.get("XLAYER_SWE_EDIT_ONLY") == "1":
    FUNCTION_TOOL_REGISTRY.pop("test_patch")
module.subprocess = _ObservedSubprocess()
compute_score = module.compute_score
