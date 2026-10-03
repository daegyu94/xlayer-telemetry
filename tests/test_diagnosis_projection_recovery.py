"""A completed analysis must survive failure of its derived dashboard files."""
import errno
import json

import pytest

from xlayer_telemetry.analysis import diagnostics
from xlayer_telemetry.analysis.deadline import IsolatedAnalyzer


class AvailablePrometheus:
    def __init__(self):
        self.calls = 0

    def query_range(self, *args):
        self.calls += 1
        return {"mean": 40, "max": 40}


def inputs(tmp_path):
    history = tmp_path / "steps.jsonl"
    history.write_text(json.dumps({
        "schema_version": 1, "record_type": "verl_step_observation",
        "record_id": "step-one", "run_id": "r", "node": "node-a",
        "worker_id": "driver", "step": 1, "observed_at": 100,
        "analysis_window": {"start": 90, "end": 100},
    }) + "\n")
    config = {"prometheus": {"url": "http://127.0.0.1:1"}}
    backend = AvailablePrometheus()
    engine = diagnostics.DiagnosticEngine(config, prometheus=backend, clock=lambda: 101)
    return history, tmp_path / "reports", config, engine, backend


@pytest.mark.parametrize("failed_file", ["latest.json", "step-one.jsonl"])
@pytest.mark.parametrize("restart", [False, True])
def test_final_report_recovers_projection_without_reanalysis(tmp_path, monkeypatch, failed_file, restart):
    history, output, config, engine, backend = inputs(tmp_path)
    atomic_write = diagnostics.atomic_write_text

    def disk_full(path, text):
        if path.name == failed_file:
            raise OSError(errno.ENOSPC, "No space left on device")
        return atomic_write(path, text)

    with monkeypatch.context() as fault:
        fault.setattr(diagnostics, "atomic_write_text", disk_full)
        with pytest.raises(OSError, match="No space"):
            diagnostics.run_once(engine, history, output, periodic_when_idle=False)
    journal = (output / "diagnostics.jsonl").read_bytes()
    final = json.loads(journal)
    assert final["analysis_status"] == "final"
    previous_calls = backend.calls
    if restart:
        engine = diagnostics.DiagnosticEngine(config, prometheus=backend, clock=lambda: 102)

    assert diagnostics.run_once(engine, history, output, periodic_when_idle=False) == 0
    projection = output / "investigation/step-one.jsonl"
    assert projection.is_file()
    assert json.loads((output / "latest.json").read_text()) == final
    assert backend.calls == previous_calls
    assert (output / "diagnostics.jsonl").read_bytes() == journal
    before = projection.stat().st_ino, projection.stat().st_mtime_ns
    assert diagnostics.run_once(engine, history, output, periodic_when_idle=False) == 0
    assert (projection.stat().st_ino, projection.stat().st_mtime_ns) == before


def test_isolated_prepare_recovery_is_persisted_only_by_controller(tmp_path, monkeypatch):
    history, output, config, engine, backend = inputs(tmp_path)
    atomic_write = diagnostics.atomic_write_text

    def disk_full(path, text):
        if path.parent.name == "investigation":
            raise OSError(errno.ENOSPC, "No space left on device")
        return atomic_write(path, text)

    with monkeypatch.context() as fault:
        fault.setattr(diagnostics, "atomic_write_text", disk_full)
        with pytest.raises(OSError):
            diagnostics.run_once(engine, history, output, periodic_when_idle=False)
    journal = (output / "diagnostics.jsonl").read_bytes()
    projection = output / "investigation/step-one.jsonl"
    with IsolatedAnalyzer(config, seconds=3) as analyzer:
        analyzer.prepare(history, output, periodic_when_idle=False)
        assert not projection.exists()  # The isolated worker cannot publish files.
        assert diagnostics.run_once(engine, history, output, periodic_when_idle=False, analyzer=analyzer) == 0
    assert projection.is_file()
    assert (output / "diagnostics.jsonl").read_bytes() == journal


def test_recovery_does_not_publish_provisional_or_skip_retry_interval(tmp_path, monkeypatch):
    history, output, config, engine, backend = inputs(tmp_path)
    engine.prometheus.query_range = lambda *args: None
    atomic_write = diagnostics.atomic_write_text

    def fail_latest(path, text):
        if path.name == "latest.json":
            raise OSError(errno.ENOSPC, "No space left on device")
        return atomic_write(path, text)

    with monkeypatch.context() as fault:
        fault.setattr(diagnostics, "atomic_write_text", fail_latest)
        with pytest.raises(OSError):
            diagnostics.run_once(engine, history, output, periodic_when_idle=False)
    journal = (output / "diagnostics.jsonl").read_bytes()
    assert diagnostics.run_once(engine, history, output, periodic_when_idle=False) == 0
    report = json.loads((output / "latest.json").read_text())
    assert report["analysis_status"] == "provisional"
    assert report["revision"] == 1
    assert not (output / "investigation").exists()
    assert (output / "diagnostics.jsonl").read_bytes() == journal


def test_recovered_latest_uses_journal_order_after_older_step_retry(tmp_path, monkeypatch):
    history, output, config, engine, backend = inputs(tmp_path)
    first = engine.analyze(json.loads(history.read_text()), []) | {
        "analysis_status": "provisional", "revision": 1, "first_attempt_at": 101, "retry_at": 111}
    diagnostics.write_report(output, first)
    second = first | {"trigger_record_id": "step-two", "step": 2,
                      "analysis_status": "final", "generated_at": "2026-01-01T00:00:02Z"}
    diagnostics.write_report(output, second)
    second_projection = output / "investigation/step-two.jsonl"
    original_stat = second_projection.stat()
    final_retry = first | {"analysis_status": "final", "revision": 2, "retry_at": None,
                           "generated_at": "2026-01-01T00:00:03Z"}
    atomic_write = diagnostics.atomic_write_text

    def fail_latest(path, text):
        if path.name == "latest.json":
            raise OSError(errno.ENOSPC, "No space left on device")
        return atomic_write(path, text)

    with monkeypatch.context() as fault:
        fault.setattr(diagnostics, "atomic_write_text", fail_latest)
        with pytest.raises(OSError):
            diagnostics.write_report(output, final_retry)
    journal = (output / "diagnostics.jsonl").read_bytes()
    assert diagnostics.run_once(engine, history, output, periodic_when_idle=False) == 0
    assert json.loads((output / "latest.json").read_text()) == final_retry
    assert (output / "diagnostics.jsonl").read_bytes() == journal
    assert second_projection.stat().st_ino == original_stat.st_ino
    assert second_projection.stat().st_mtime_ns == original_stat.st_mtime_ns
