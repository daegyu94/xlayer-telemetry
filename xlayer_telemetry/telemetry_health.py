"""Record workload outcome separately from telemetry completeness."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import time

from .fileio import atomic_write_text, json_objects
from .measurements import finite_number


def _read(root: Path) -> dict:
    try:
        value = json.loads((root / "telemetry-health.json").read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def observe(root: Path, pids: dict[str, int], *, now: float | None = None,
            max_age_seconds: float = 300) -> dict:
    now = time.time() if now is None else now
    previous = _read(root)
    result = {"schema_version": 1, "record_type": "telemetry_health", "observed_at": now,
              "started_at": previous.get("started_at", now),
              "workload": {"status": "running", "exit_code": None},
              "issues": list(previous.get("issues", [])), "sidecars": {}}
    for name, pid in pids.items():
        try:
            os.kill(pid, 0)
            alive = True
        except (ProcessLookupError, PermissionError):
            alive = False
        paths = list((root / "telemetry-metrics").glob("verl-trainer-driver*.json")) if name == "bridge" else [root / "diagnostics/latest.json"]
        mtimes = []
        for path in paths:
            try:
                mtimes.append(path.stat().st_mtime)
            except OSError:
                pass
        last = max(mtimes) if mtimes else None
        age = max(0, now-last) if last is not None else None
        status = "stopped" if not alive else "pending" if last is None else "delayed" if age > max_age_seconds else "observed"
        result["sidecars"][name] = {"pid": pid, "alive": alive, "status": status,
                                    "last_success_at": last, "age_seconds": age}
        if not alive:
            issue = f"{name}:exited_during_workload"
            if issue not in result["issues"]:
                result["issues"].append(issue)
    result["status"] = "partial" if result["issues"] else "pending" if any(
        item["status"] == "pending" for item in result["sidecars"].values()) else "delayed" if any(
        item["status"] == "delayed" for item in result["sidecars"].values()) else "observed"
    atomic_write_text(root / "telemetry-health.json", json.dumps(result, indent=2) + "\n")
    return result


def finish(root: Path, exit_code: int, bridge_export: str, diagnosis_export: str) -> dict:
    result = _read(root)
    result.update(schema_version=1, record_type="telemetry_health", observed_at=time.time(),
                  workload={"status": "finished", "exit_code": exit_code},
                  final_export={"bridge": bridge_export, "diagnostics": diagnosis_export})
    issues = result.setdefault("issues", [])
    if diagnosis_export == "ok":
        try:
            diagnosis = json.loads((root / "diagnostics/latest.json").read_text())
            result["diagnostics"] = {key: diagnosis.get(key) for key in ("generated_at", "analysis_status", "verdict", "missing_sources")}
            history_path = root / "telemetry-events/verl-steps.jsonl"
            reports_path = root / "diagnostics/diagnostics.jsonl"
            if history_path.is_file() and reports_path.is_file():
                expected = {item["record_id"] for item in json_objects(history_path) if isinstance(item.get("record_id"), str)}
                latest = {}
                for item in json_objects(reports_path):
                    key = item.get("trigger_record_id")
                    if isinstance(key, str):
                        latest[key] = item
                missing = sorted(key for key in expected if key not in latest or latest[key].get("analysis_status") == "provisional")
                incomplete = sorted(key for key in expected if latest.get(key, {}).get("verdict") == "insufficient_data")
                result["diagnostics"].update(expected_steps=len(expected), unresolved_steps=len(missing),
                                             unresolved_record_ids=missing[:20], insufficient_data_steps=len(incomplete))
                if missing:
                    issues.append("diagnostics:unresolved_steps")
                if incomplete:
                    issues.append("diagnostics:insufficient_data_steps")
            if diagnosis.get("verdict") == "insufficient_data" or diagnosis.get("analysis_status") == "provisional":
                issues.append("diagnostics:incomplete_evidence")
        except (OSError, ValueError, AttributeError):
            issues.append("diagnostics:unreadable_final_report")
    for name, outcome in result["final_export"].items():
        if outcome == "ok":
            paths = list((root / "telemetry-metrics").glob("verl-trainer-driver*.json")) if name == "bridge" else [root / "diagnostics/latest.json"]
            timestamps = []
            for path in paths:
                try:
                    timestamps.append(path.stat().st_mtime)
                except OSError:
                    pass
            result.setdefault("sidecars", {})[name] = {
                "alive": False, "status": "finalized", "last_success_at": max(timestamps) if timestamps else None,
                "age_seconds": max(0, time.time()-max(timestamps)) if timestamps else None}

        if outcome in {"failed", "missing", "interrupted"}:
            issue = f"{name}:final_export:{outcome}"
            if issue not in issues:
                issues.append(issue)
    result["status"] = "partial" if issues else "complete"
    atomic_write_text(root / "telemetry-health.json", json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--bridge-pid", type=int)
    parser.add_argument("--diagnostics-pid", type=int)
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--max-age-seconds", type=float, default=300)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--finish", type=int, metavar="WORKLOAD_EXIT_CODE")
    parser.add_argument("--bridge-export", choices=("ok", "missing", "failed", "interrupted"), default="interrupted")
    parser.add_argument("--diagnostics-export", choices=("ok", "disabled", "missing", "failed", "interrupted"), default="disabled")
    args = parser.parse_args()
    if args.finish is not None:
        finish(args.run_root, args.finish, args.bridge_export, args.diagnostics_export)
        return
    if any(finite_number(value) is None or value <= 0 for value in (args.interval, args.max_age_seconds)):
        parser.error("interval and max-age-seconds must be finite and positive")
    pids = {name: pid for name, pid in (("bridge", args.bridge_pid), ("diagnostics", args.diagnostics_pid)) if pid is not None}
    if not pids or any(pid <= 0 for pid in pids.values()):
        parser.error("positive sidecar PID required")
    def stop(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        while True:
            observe(args.run_root, pids, max_age_seconds=args.max_age_seconds)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
