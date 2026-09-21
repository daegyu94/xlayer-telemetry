"""Regression coverage for streaming records and run handoff failures."""

import json
import os
from pathlib import Path
import subprocess
import sys

from post_training_telemetry.adapters import verl
from post_training_telemetry.show_run import _recent_events

ROOT = Path(__file__).parents[1]


def test_follow_preserves_partial_json_record(tmp_path, monkeypatch):
    path = tmp_path / "metrics.jsonl"
    path.write_text('{"step":1,"data":')
    polls = []

    def finish_write(interval):
        polls.append(interval)
        assert len(polls) == 1
        with path.open("a") as stream:
            stream.write('{"timing_s/gen":0.5}}' + chr(10))

    monkeypatch.setattr(verl.time, "sleep", finish_write)
    records = verl.iter_file_records(path, follow=True, poll_interval=0.1)
    try:
        assert next(records) == {"step": 1, "data": {"timing_s/gen": 0.5}}
        assert polls == [0.1]
    finally:
        records.close()


def test_recent_events_selects_latest_across_workers(tmp_path):
    paths = []
    for worker, times in (("a", [100, 300]), ("z", [10, 20])):
        path = tmp_path / (worker + ".jsonl")
        path.write_text("".join(
            json.dumps({"schema_version": 1, "timestamp_unix_nano": stamp})
            + chr(10) for stamp in times
        ))
        paths.append(path)
    assert [r["timestamp_unix_nano"] for r in _recent_events(paths, 2)] == [100, 300]


def test_wrapper_preserves_workload_status_when_summary_fails(tmp_path):
    interpreter = tmp_path / "telemetry-python"
    interpreter.write_text(
        "#!" + sys.executable + chr(10)
        + "import os, sys" + chr(10)
        + "if 'post_training_telemetry.show_run' in sys.argv: sys.exit(99)" + chr(10)
        + "os.execv(" + repr(sys.executable) + ", [" + repr(sys.executable)
        + "] + sys.argv[1:])" + chr(10)
    )
    interpreter.chmod(0o755)
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/run_verl_with_telemetry.sh"),
         "--output", str(tmp_path / "run"), "--", "bash", "-c", "exit 7"],
        env=os.environ | {"TELEMETRY_PYTHON": str(interpreter)},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 7, result.stderr
    assert "run summary failed" in result.stderr


def test_wrapper_preserves_existing_run(tmp_path):
    output = tmp_path / "run"
    output.mkdir()
    manifest = output / "telemetry-manifest.json"
    manifest.write_text('{"run_id":"original"}')
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/run_verl_with_telemetry.sh"),
         "--output", str(output), "--", "true"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert manifest.read_text() == '{"run_id":"original"}'


def test_wrapper_forwards_termination_to_workload(tmp_path):
    import time

    ready = tmp_path / "ready"
    stopped = tmp_path / "stopped"
    code = (
        "import signal, time; from pathlib import Path; "
        + "signal.signal(signal.SIGTERM, lambda *_: "
        + "(Path(" + repr(str(stopped)) + ").touch(), exit(0))); "
        + "Path(" + repr(str(ready)) + ").touch(); time.sleep(30)"
    )
    process = subprocess.Popen(
        ["bash", str(ROOT / "scripts/run_verl_with_telemetry.sh"),
         "--output", str(tmp_path / "run"), "--", sys.executable, "-c", code],
        env=os.environ | {"TELEMETRY_PYTHON": sys.executable},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 143, (stdout, stderr)
        assert stopped.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
