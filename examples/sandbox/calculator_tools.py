"""GSM8K validation tool using an external, network-isolated Docker process.

This is a small telemetry integration fixture, not a sandbox runtime or a
benchmark of agent quality. Requires veRL's existing function_tool API.
"""
from __future__ import annotations

from contextlib import nullcontext
import json
import os
import subprocess

from verl.tools.function_tool import function_tool
from xlayer_telemetry.events import EventRecorder
from xlayer_telemetry.sandbox import SandboxRecorder

# Parse arithmetic inside the external environment; never evaluate Python code.
PROGRAM = '''import ast,json,operator,sys
ops={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv}
def calc(n):
 if isinstance(n,ast.Constant) and type(n.value) in (int,float): return n.value
 if isinstance(n,ast.BinOp) and type(n.op) in ops: return ops[type(n.op)](calc(n.left),calc(n.right))
 if isinstance(n,ast.UnaryOp) and isinstance(n.op,ast.USub): return -calc(n.operand)
 raise ValueError("Only arithmetic literals and + - * / are accepted")
print(json.dumps({"result":calc(ast.parse(sys.argv[1],mode="eval").body)}))
'''


@function_tool("calculate")
def calculate(expression: str) -> str:
    """Calculate an arithmetic expression in an isolated external environment.

    Args:
        expression: Arithmetic numbers, parentheses and + - * / operators.
    """
    events = EventRecorder.from_env(
        producer="agent", role="rollout", worker_id=str(os.getpid()))
    sandbox_events = EventRecorder.from_env(
        producer="sandbox", role="sandbox", worker_id=str(os.getpid()))
    parent = (events.span("tool.call", phase="tool_interaction",
                          attributes={"tool": "calculate"})
              if events else nullcontext())
    try:
        with parent as identity:
            if not isinstance(expression, str) or not expression or len(expression) > 200:
                raise ValueError("invalid expression")
            child = (SandboxRecorder(
                sandbox_events, runtime="docker", filesystem="overlayfs",
                deployment="colocated", sandbox_node=sandbox_events.context.node)
                if sandbox_events else None)
            span = child.span("exec", trace_id=identity.trace_id if identity else None,
                              parent_span_id=identity.span_id if identity else None,
                              attributes={"tool": "calculate"}) if child else nullcontext()
            command = ["docker", "run", "--rm", "--network", "none", "--read-only",
                       "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                       "--pids-limit", "32", "--memory", "64m", "--cpus", "1"]
            if os.environ.get("XLAYER_SANDBOX_CGROUP_PARENT"):
                command.extend(["--cgroup-parent", os.environ["XLAYER_SANDBOX_CGROUP_PARENT"]])
            command.extend([os.environ.get("SWE_AGENT_GRADER_IMAGE", "python:3.12-alpine"),
                            "python3", "-c", PROGRAM, expression])
            with span:
                result = subprocess.run(
                    command, check=True, capture_output=True, text=True, timeout=20)
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return json.dumps({"error": type(exc).__name__})
