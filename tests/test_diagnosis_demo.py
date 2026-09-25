import json

from xlayer_telemetry.diagnosis_demo import generate


def test_synthetic_investigation_has_inspectable_candidate_and_exact_span(tmp_path):
    root = tmp_path / "run" / "telemetry"
    report = generate(root, run_id="synthetic-run", clock=lambda: 200)

    assert report["comparison"]["baseline_record_id"]
    queue = next(item for item in report["candidates"] if item["id"] == "storage_queue_saturation")
    assert queue["state"] == "strong_signal"
    assert "per_run_3fs_client_bytes" in queue["missing_evidence"]
    assert not any(item["id"] == "network_limited_storage" for item in report["candidates"])
    assert len((root / "telemetry-events/verl-steps.jsonl").read_text().splitlines()) == 2
    events = [json.loads(line) for line in (root / "telemetry-events/demo-rollout-worker-0.jsonl").read_text().splitlines()]
    assert {event["record_type"] for event in events} == {"span", "event"}
    assert events[0]["trace_id"] == events[1]["trace_id"]
    rows = [json.loads(line) for line in next((root / "diagnostics/investigation").glob("*.jsonl")).read_text().splitlines()]
    assert next(row for row in rows if row["row_kind"] == "summary")["primary_candidate"] == "storage_queue_saturation"
    assert all(row["data_origin"] == "synthetic" for row in rows)
