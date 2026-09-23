import json
from pathlib import Path

from xlayer_telemetry.step_backfill import backfill


def test_backfill_prepares_only_legacy_steps_with_valid_windows(tmp_path: Path) -> None:
    events = tmp_path / "telemetry-events"
    events.mkdir()
    source = events / "verl-steps.jsonl"
    records = [
        {
            "record_type": "verl_step_observation", "record_id": "old", "step": 8,
            "analysis_window": {"start": 10.1234, "end": 20.5678},
            "stage_durations_seconds": {"gen": 7.0, "update_actor": 2.5},
        },
        {
            "record_type": "verl_step_observation", "record_id": "new", "step": 9,
            "analysis_window": {"start": 20, "end": 30}, "window_start_ms": 20000,
        },
        {
            "record_type": "verl_step_observation", "record_id": "no-window", "step": 10,
            "analysis_window": {"start": None, "end": 40},
        },
    ]
    original = "\n".join(json.dumps(record) for record in records) + "\n"
    source.write_text(original)

    assert backfill(tmp_path) == 1
    prepared = json.loads((events / "verl-steps-backfill.jsonl").read_text())
    assert prepared["record_id"] == "old"
    assert prepared["window_start_ms"] == 10123
    assert prepared["window_end_ms"] == 20568
    assert prepared["stage_summary"] == "gen: 7.00s · update_actor: 2.50s"
    assert prepared["boundary_accuracy"] == "unknown"
    assert source.read_text() == original
