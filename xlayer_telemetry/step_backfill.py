"""Prepare legacy VERL step records for the Grafana Loki dashboard."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from xlayer_telemetry.step_history import dashboard_fields


def backfill(run_root: Path) -> int:
    source = run_root / "telemetry-events" / "verl-steps.jsonl"
    output = source.with_name("verl-steps-backfill.jsonl")
    temporary = output.with_suffix(".jsonl.tmp")
    count = 0
    with source.open(encoding="utf-8") as records, temporary.open("w", encoding="utf-8") as target:
        for line in records:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            window = record.get("analysis_window") or {}
            start, end = window.get("start"), window.get("end")
            if (record.get("record_type") != "verl_step_observation"
                    or not isinstance(record.get("record_id"), str)
                    or type(start) not in (int, float) or type(end) not in (int, float)
                    or not math.isfinite(start) or not math.isfinite(end)
                    or start >= end or "window_start_ms" in record):
                continue
            stages = record.get("stage_durations_seconds") or {}
            stages = {name: float(value) for name, value in stages.items()
                      if isinstance(name, str) and type(value) in (int, float)
                      and math.isfinite(value) and value >= 0}
            record.update(dashboard_fields(start, end, stages, str(window.get("accuracy", "unknown"))))
            target.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
            count += 1
    temporary.replace(output)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path, help="Directory containing telemetry-events/verl-steps.jsonl")
    args = parser.parse_args()
    try:
        count = backfill(args.run_root)
    except OSError as error:
        parser.error(str(error))
    print(f"Prepared {count} legacy step records for Grafana")


if __name__ == "__main__":
    main()
