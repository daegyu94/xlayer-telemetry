"""Prepare legacy VERL step records for the Grafana Loki dashboard."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from xlayer_telemetry.step_history import dashboard_fields
from xlayer_telemetry.fileio import atomic_text_writer, json_objects
from xlayer_telemetry.measurements import finite_number


def backfill(run_root: Path) -> int:
    source = run_root / "telemetry-events" / "verl-steps.jsonl"
    output = source.with_name("verl-steps-backfill.jsonl")
    count = 0
    with atomic_text_writer(output) as target:
        for record in json_objects(source):
            window = record.get("analysis_window")
            if not isinstance(window, dict):
                continue
            start, end = window.get("start"), window.get("end")
            if (record.get("record_type") != "verl_step_observation"
                    or not isinstance(record.get("record_id"), str)
                    or finite_number(start) is None or finite_number(end) is None
                    or start >= end or "window_start_ms" in record):
                continue
            stages = record.get("stage_durations_seconds") or {}
            if not isinstance(stages, dict):
                stages = {}
            stages = {name: float(value) for name, value in stages.items()
                      if isinstance(name, str) and finite_number(value) is not None and value >= 0}
            record.update(dashboard_fields(start, end, stages, str(window.get("accuracy", "unknown"))))
            target.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
            count += 1
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
