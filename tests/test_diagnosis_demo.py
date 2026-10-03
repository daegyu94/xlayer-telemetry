import json

from xlayer_telemetry.demos.diagnosis import generate


def test_synthetic_investigation_has_inspectable_candidate_and_exact_span(tmp_path):
    root = tmp_path / "run" / "telemetry"
    report = generate(root, run_id="synthetic-run", clock=lambda: 200)

    assert report["comparison"]["baseline_record_id"]
    queue = next(item for item in report["candidates"] if item["id"] == "storage_queue_saturation")
    assert queue["state"] == "strong_signal"
    assert "per_run_3fs_client_bytes" in queue["missing_evidence"]
    assert not any(item["id"] == "network_limited_storage" for item in report["candidates"])
    assert len((root / "telemetry-events/verl-steps.jsonl").read_text().splitlines()) == 2
    events = [json.loads(line) for line in next((root / "telemetry-events").glob("demo-rollout-worker%2D0*.jsonl")).read_text().splitlines()]
    assert {event["record_type"] for event in events} == {"span", "event"}
    assert events[0]["trace_id"] == events[1]["trace_id"]
    tool = next(e for e in events if e["name"] == "tool.call")
    sandbox = next(e for e in events if e["name"] == "sandbox.exec")
    assert sandbox["trace_id"] == tool["trace_id"]
    assert sandbox["parent_span_id"] == tool["span_id"]
    assert all(e["attributes"]["data_origin"] == "synthetic" for e in events)
    assert "[synthetic]" in (root / "logs/agent.log").read_text()
    rows = [json.loads(line) for line in next((root / "diagnostics/investigation").glob("*.jsonl")).read_text().splitlines()]
    assert next(row for row in rows if row["row_kind"] == "summary")["primary_candidate"] == "storage_queue_saturation"
    assert all(row["data_origin"] == "synthetic" for row in rows)


def test_synthetic_run_is_discoverable_and_inspectable(tmp_path):
    from xlayer_telemetry.metrics.textfile import collect_snapshots, build_metrics
    from xlayer_telemetry.show_run import summarize
    root = tmp_path / "runs" / "practice"
    generate(root, run_id="practice", node="cpu-demo", clock=lambda: 200)
    snapshots = collect_snapshots([], [root.parent], now=201, max_age_seconds=300)
    metrics = build_metrics(snapshots)
    assert any(metric.name == "training_step" and metric.value == 127 for metric in metrics)
    manifest = json.loads((root / "telemetry-manifest.json").read_text())
    assert manifest["data_origin"] == "synthetic"
    assert "storage_queue_saturation" in summarize(root)
