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


def test_cli_existing_output_is_actionable_and_preserves_artifacts(tmp_path):
    import subprocess
    import sys
    output = tmp_path / "run"
    output.mkdir()
    sentinel = output / "keep.json"
    sentinel.write_text("existing data")
    result = subprocess.run([sys.executable, "-m", "xlayer_telemetry.demos.diagnosis",
                             "--output", str(output), "--run-id", "demo"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "choose a new --output" in result.stderr
    assert "Traceback" not in result.stderr
    assert sentinel.read_text() == "existing data"
    assert list(output.iterdir()) == [sentinel]


def test_synthetic_lifecycle_points_are_explicit_and_do_not_invent_intervals(tmp_path):
    root = tmp_path / 'lifecycle'
    generate(root, run_id='practice', clock=lambda: 200)
    records = [json.loads(line) for path in (root / 'telemetry-events').glob('demo-*.jsonl')
               for line in path.read_text().splitlines()]
    names = {'policy.update.completed', 'weight.sync.completed', 'checkpoint.completed', 'kv.cache.evicted'}
    lifecycle = [r for r in records if r['name'] in names]
    assert {r['name'] for r in lifecycle} == names
    assert all(r['record_type'] == 'event' and r['attributes']['data_origin'] == 'synthetic' for r in lifecycle)
    assert all('start_time_unix_nano' not in r and 'end_time_unix_nano' not in r for r in lifecycle)
