"""Check tool-to-sandbox trace links in an existing veRL sandbox smoke run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate(events_dir: Path, *, require_sandbox: bool = False) -> dict[str, int]:
    spans = []
    for path in sorted(events_dir.glob("*.jsonl")):
        if not path.name.startswith(("agent-", "sandbox-")):
            continue
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("record_type") == "span":
                    spans.append(record)
    for span in spans:
        if span.get("name") in {"tool.call", "sandbox.exec"} and any(
                not isinstance(span.get(key), str) or not span[key].strip()
                for key in ("run_id", "trace_id", "span_id")):
            raise ValueError("tool/sandbox span has an invalid run/trace/span identity")
    tools = {(span.get("run_id"), span.get("trace_id"), span.get("span_id"))
             for span in spans if span.get("name") == "tool.call"}
    sandbox = [span for span in spans if span.get("name") == "sandbox.exec"]
    if not tools:
        raise ValueError("no tool.call spans found")
    if require_sandbox and not sandbox:
        raise ValueError("no sandbox.exec spans found; this workload may not have called the grader")
    unmatched = [span for span in sandbox
                 if (span.get("run_id"), span.get("trace_id"), span.get("parent_span_id"))
                 not in tools]
    if unmatched:
        raise ValueError(f"{len(unmatched)} sandbox.exec spans have no matching tool.call parent")
    return {"tool_calls": len(tools), "sandbox_execs": len(sandbox),
            "linked_sandbox_execs": len(sandbox)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path, help="Root containing telemetry/telemetry-events (legacy run/ also supported)")
    parser.add_argument("--require-sandbox", action="store_true")
    args = parser.parse_args()
    events_dir = args.run_root / "telemetry" / "telemetry-events"
    if not events_dir.is_dir():
        events_dir = args.run_root / "run" / "telemetry-events"
    result = validate(events_dir,
                      require_sandbox=args.require_sandbox)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
