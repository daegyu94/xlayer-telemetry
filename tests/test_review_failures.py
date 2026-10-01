"""Regression cases for telemetry failure isolation at external boundaries."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from xlayer_telemetry.adapters import verl
from xlayer_telemetry.analysis import diagnostics


ROOT = Path(__file__).parents[1]


def test_follower_waits_for_split_utf8_and_skips_invalid_line(tmp_path, monkeypatch):
    path = tmp_path / "metrics.jsonl"
    raw = (json.dumps({"step": 1, "data": {}, "note": "학습"}, ensure_ascii=False) + "\n").encode()
    split = raw.index("학".encode()) + 1
    path.write_bytes(raw[:split])
    def finish_line(_interval):
        with path.open("ab") as stream:
            stream.write(raw[split:])
    monkeypatch.setattr(verl.time, "sleep", finish_line)
    follower = verl.iter_file_records(path, follow=True, poll_interval=.01)
    assert next(follower)["step"] == 1
    follower.close()
    with path.open("ab") as stream:
        stream.write(b'\xff\n{"step":2,"data":{}}\n')
    assert [row["step"] for row in verl.iter_file_records(path, follow=False, poll_interval=.01)] == [1, 2]


def test_sandbox_invalid_output_name_never_deletes_outside_file(tmp_path):
    cgroup = tmp_path / "empty-cgroup"
    cgroup.mkdir()
    output = tmp_path / "textfile"
    output.mkdir()
    sentinel = tmp_path / "sentinel.prom"
    sentinel.write_text("preserve me")
    result = subprocess.run([sys.executable, "-m", "xlayer_telemetry.collectors.sandbox_sampler",
        "--cgroup", str(cgroup), "--textfile-dir", str(output),
        "--textfile-name", "../sentinel.prom", "--node", "n", "--runtime", "docker",
        "--filesystem", "overlayfs", "--deployment", "colocated", "--once"],
        capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
    assert sentinel.read_text() == "preserve me"
    assert "basename" in result.stderr


def test_wrapper_preserves_exit_when_diagnostics_config_disappears(tmp_path):
    config = tmp_path / "diagnostics.json"
    config.write_text(json.dumps({"schema_version": 1, "prometheus": {"url": "http://127.0.0.1:1"}}))
    result = subprocess.run(["bash", str(ROOT / "scripts/run_verl_with_telemetry.sh"),
        "--output", str(tmp_path / "run"), "--diagnostics-config", str(config),
        "--", sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).unlink(); sys.exit(7)", str(config)],
        env=os.environ | {"TELEMETRY_PYTHON": sys.executable}, capture_output=True,
        text=True, timeout=15)
    assert result.returncode == 7, result.stderr
    assert (tmp_path / "run/telemetry-health.json").exists()


def test_partial_query_results_retry_until_missing_source_arrives(tmp_path):
    class PartialPrometheus:
        ready = False
        def query_range(self, query, *args):
            if "gpu_utilization" in query or self.ready:
                return {"mean": 40, "max": 40}
            return None
    prom = PartialPrometheus()
    clock = [101.0]
    engine = diagnostics.DiagnosticEngine({"schema_version": 1,
        "prometheus": {"url": "http://unused"}, "retry_interval_seconds": 5},
        prometheus=prom, clock=lambda: clock[0])
    history = tmp_path / "steps.jsonl"
    history.write_text(json.dumps({"schema_version": 1, "record_type": "verl_step_observation", "record_id": "one", "run_id": "r", "node": "n",
        "observed_at": 100, "analysis_window": {"start": 90, "end": 100}}) + "\n")
    output = tmp_path / "diagnostics"
    assert diagnostics.run_once(engine, history, output, periodic_when_idle=False) == 1
    report = json.loads((output / "latest.json").read_text())
    assert report["evidence"]
    assert report["analysis_status"] == "provisional"
    prom.ready = True
    clock[0] = 106
    assert diagnostics.run_once(engine, history, output, periodic_when_idle=False) == 1
    report = json.loads((output / "latest.json").read_text())
    assert report["revision"] == 2
    assert report["analysis_status"] == "final"


def test_clickhouse_quoted_numbers_reach_diagnosis(monkeypatch):
    row = {"metricName": "read_latency", "sample_count": "200", "weighted_mean": "2.5",
           "max_value": "5", "max_observed_p99": "4"}
    monkeypatch.setattr(diagnostics, "urlopen", lambda *a, **k: io.BytesIO((json.dumps(row) + "\n").encode()))
    rows = diagnostics.ThreeFSClient("http://unused").query_window(1, 2)
    assert rows[0]["count"] == 200
    assert diagnostics.DiagnosticEngine._signals(None, {}, rows)["threefs_p99_latency"] == 4


@pytest.mark.parametrize("raw", [b'[]\n', b'42\n', b'{"metricName":"read_latency","sample_count":"invalid"}\n'])
def test_clickhouse_malformed_rows_raise_handled_error(monkeypatch, raw):
    monkeypatch.setattr(diagnostics, "urlopen", lambda *a, **k: io.BytesIO(raw))
    with pytest.raises(ValueError):
        diagnostics.ThreeFSClient("http://unused").query_window(1, 2)


def test_missing_threefs_latency_remains_missing_evidence():
    assert "threefs_p99_latency" not in diagnostics.DiagnosticEngine._signals(None, {}, [
        {"metricName": "read_latency", "count": 2, "max_observed_p99": None}])


def test_concurrent_wrappers_cannot_claim_same_run(tmp_path):
    import time
    helper = tmp_path / "python-wrapper"
    entered, release = tmp_path / "entered", tmp_path / "release"
    helper.write_text(f'''#!{sys.executable}
import os, pathlib, sys, time
if "xlayer_telemetry.manifest" in sys.argv:
    pathlib.Path({str(entered)!r}).touch()
    deadline = time.monotonic() + 10
    while not pathlib.Path({str(release)!r}).exists() and time.monotonic() < deadline:
        time.sleep(.01)
os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])
''')
    helper.chmod(0o755)
    command = ["bash", str(ROOT / "scripts/run_verl_with_telemetry.sh"),
               "--output", str(tmp_path / "run"), "--", "true"]
    first = subprocess.Popen(command, env=os.environ | {"TELEMETRY_PYTHON": str(helper)},
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while not entered.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert entered.exists()
        second = subprocess.run(command, env=os.environ | {"TELEMETRY_PYTHON": sys.executable},
                                capture_output=True, text=True, timeout=5)
        assert second.returncode == 2, second.stderr
        assert "already" in second.stderr
    finally:
        release.touch()
        _, error = first.communicate(timeout=10)
    assert first.returncode == 0, error
