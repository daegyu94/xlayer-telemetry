"""Check tool-to-sandbox trace links in an existing veRL sandbox smoke run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate(events_dir: Path, *, require_sandbox: bool = False,
             require_results: bool = False) -> dict[str, int]:
    spans = []
    results = []
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
                elif isinstance(record, dict) and record.get("name") == "sandbox.exec_result":
                    results.append(record)
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
    summary = {"tool_calls": len(tools), "sandbox_execs": len(sandbox),
               "linked_sandbox_execs": len(sandbox)}
    if require_results:
        if not sandbox:
            raise ValueError("no sandbox.exec spans found for outcome validation")
        identities = [(s["run_id"], s["trace_id"], s["span_id"]) for s in sandbox]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate sandbox.exec identity")
        unmatched = set(identities)
        for result in results:
            identity = tuple(result.get(key) for key in ("run_id", "trace_id", "span_id"))
            if any(not isinstance(value, str) or not value.strip() for value in identity):
                raise ValueError("sandbox outcome has an invalid identity")
            if identity not in unmatched:
                raise ValueError("sandbox outcome has no unique matching sandbox.exec")
            attributes = result.get("attributes")
            allowed = {"completed", "nonzero_exit", "timeout", "launch_error",
                       "oom", "infra_failure", "test_failure", "model_failure"}
            if (not isinstance(attributes, dict)
                    or not isinstance(attributes.get("outcome"), str)
                    or attributes["outcome"] not in allowed):
                raise ValueError("sandbox outcome is invalid")
            code = attributes.get("exit_code")
            if code is not None and (type(code) is not int
                    or (attributes["outcome"] == "completed" and code != 0)
                    or (attributes["outcome"] == "nonzero_exit" and code == 0)):
                raise ValueError("sandbox outcome contradicts exit_code")
            unmatched.remove(identity)
        if unmatched:
            raise ValueError(f"{len(unmatched)} sandbox.exec spans have no outcome")
        summary["sandbox_execution_results"] = len(results)
    return summary


def validate_updates(events_dir: Path, execution_mode: str, minimum: int = 1) -> dict:
    """Check update semantics without requiring rollout spans to fit an update."""
    if execution_mode not in {"sync", "async"} or minimum < 1:
        raise ValueError("execution mode must be sync/async and minimum updates positive")
    expected_scope = "trainer_update" if execution_mode == "async" else "rl_step"
    records = [json.loads(line) for line in
               (events_dir / "verl-steps.jsonl").read_text().splitlines() if line.strip()]
    updates = [r for r in records if isinstance(r, dict)
               and r.get("record_type") == "verl_step_observation"]
    if len(updates) < minimum:
        raise ValueError(f"expected at least {minimum} updates; found {len(updates)}")
    ids = set()
    for update in updates:
        if update.get("execution_mode") != execution_mode or update.get("boundary_scope") != expected_scope:
            raise ValueError("update execution_mode or boundary_scope is incorrect")
        identity = update.get("record_id")
        if not isinstance(identity, str) or not identity or identity in ids:
            raise ValueError("update record_id is missing or duplicated")
        ids.add(identity)
    return {"updates": len(updates), "boundary_scope": expected_scope}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path, help="Root containing telemetry/telemetry-events (legacy run/ also supported)")
    parser.add_argument("--require-sandbox", action="store_true")
    parser.add_argument("--require-results", action="store_true",
                        help="Require exactly one execution outcome per sandbox.exec")
    parser.add_argument("--execution-mode", choices=("sync", "async"),
                        help="Also verify trainer update history semantics")
    parser.add_argument("--min-updates", type=int, default=1)
    args = parser.parse_args()
    if args.min_updates < 1 or (args.min_updates != 1 and not args.execution_mode):
        parser.error("--min-updates must be positive and requires --execution-mode")
    events_dir = args.run_root / "telemetry" / "telemetry-events"
    if not events_dir.is_dir():
        events_dir = args.run_root / "run" / "telemetry-events"
    result = validate(events_dir,
                      require_sandbox=args.require_sandbox, require_results=args.require_results)
    if args.execution_mode:
        result.update(validate_updates(events_dir, args.execution_mode, args.min_updates))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
