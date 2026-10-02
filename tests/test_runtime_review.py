"""Rotation, retry timing, cached collection and SDK failure boundaries."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from xlayer_telemetry.adapters import verl
from xlayer_telemetry.analysis.diagnostics import run_once
from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.fileio import atomic_write_text
from xlayer_telemetry.metrics import Metric, MetricEmitter
from xlayer_telemetry.metrics.textfile import SnapshotCache, build_metrics, collect_snapshots


@pytest.mark.parametrize("interval", ["nan", "inf", "0"])
@pytest.mark.parametrize("module,options", [
    ("analysis.diagnostics", ["--config", "missing.json"]),
    ("collectors.topology_textfile", ["--topology-dir", ".", "--textfile-dir", "."]),
    ("collectors.resource_sampler", ["--target", ".", "--output", "resource.jsonl"]),
])
def test_invalid_poll_interval_fails_before_any_io(module, options, interval):
    result = subprocess.run([sys.executable, "-m", "xlayer_telemetry." + module,
                             *options, "--interval", interval], capture_output=True, text=True, timeout=5)
    assert result.returncode == 2 and "finite and positive" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("mode", ["replace", "truncate", "temporarily_missing", "partial"])
def test_follower_recovers_rotated_logs_without_inventing_event_time(tmp_path, monkeypatch, mode):
    path = tmp_path / "logger.jsonl"
    initial = json.dumps({"step": 1, "data": {"padding": "x" * 100}}) + "\n"
    path.write_text(initial)
    follower = verl.iter_file_records(path, follow=True, poll_interval=.01)
    assert next(follower)["step"] == 1
    if mode == "partial":
        with path.open("a") as stream:
            stream.write('{"step":')
    calls = 0
    def rotate(_interval):
        nonlocal calls
        calls += 1
        assert calls <= 2, "follower kept reading the old inode/offset"
        if mode == "temporarily_missing" and calls == 1:
            path.unlink()
            return
        data = '{"step":2,"data":{}}\n'
        if mode in {"truncate", "partial"}:
            path.write_text(data)
        else:
            atomic_write_text(path, data)
    monkeypatch.setattr(verl.time, "sleep", rotate)
    try:
        replacement = next(follower)
        assert replacement["step"] == 2
        assert replacement.live is False
        with path.open("a") as stream:
            stream.write('{"step":3,"data":{}}\n')
        live = next(follower)
        assert live["step"] == 3 and live.live is True
    finally:
        follower.close()


@pytest.mark.parametrize("seconds", [40, 70])
def test_retry_window_starts_at_each_attempt_and_backoff_after_completion(tmp_path, seconds):
    now = [100.0]
    history = tmp_path / "history.jsonl"
    records = [dict(schema_version=1, record_type="verl_step_observation", record_id=str(i),
                    observed_at=i, analysis_window={"start": i, "end": i+1}) for i in (1, 2)]
    history.write_text("".join(json.dumps(record) + "\n" for record in records))
    def analyze(record, prior):
        now[0] += seconds
        return dict(trigger="step_observed", trigger_record_id=record["record_id"],
                    verdict="insufficient_data", missing_sources=["prometheus:no_data"])
    engine = SimpleNamespace(config={"retry_seconds": 60, "retry_interval_seconds": 10},
                             jsonl_cache=None, threefs=None, clock=lambda: now[0], analyze=analyze)
    output = tmp_path / "diagnostics"
    assert run_once(engine, history, output, periodic_when_idle=False) == 2
    reports = [json.loads(line) for line in (output / "diagnostics.jsonl").read_text().splitlines()]
    assert [row["first_attempt_at"] for row in reports] == [100, 100+seconds]
    if seconds < 60:
        assert [row["retry_at"] for row in reports] == [150, 190]
        assert all(row["analysis_status"] == "provisional" for row in reports)
    else:
        assert all(row["analysis_status"] == "final" and row["retry_at"] is None for row in reports)


def snapshot(worker="0", node="n", observed_at=100):
    return dict(schema_version=2, run_id="r", producer="app", role="worker", worker_id=worker,
                node=node, observed_at=observed_at, samples=[dict(name="loss", value=1)])


def test_cached_collection_rechecks_freshness_node_and_atomic_replacement(tmp_path):
    cache, counts = SnapshotCache(), {}
    path = tmp_path / "worker.json"
    atomic_write_text(path, json.dumps(snapshot()))
    def collect(now=101, node="n"):
        return collect_snapshots([tmp_path], [], cache=cache, counters=counts,
                                 node=node, max_age_seconds=5, now=now)
    first = collect()
    assert build_metrics(first) == build_metrics(collect())
    assert counts == {"snapshot_reads": 1, "snapshot_cache_hits": 1}
    assert collect(node="other") == []
    assert collect(now=106) == []  # Cache hits must not revive stale values.
    atomic_write_text(path, json.dumps(snapshot(observed_at=106)))
    assert collect(now=107)[0]["observed_at"] == 106
    assert counts["snapshot_reads"] == 2
    path.write_text('{"broken":')
    assert collect() == [] and counts["snapshot_rejections"] == 1
    path.unlink()
    assert collect() == [] and len(cache._entries) == 0


def test_snapshot_cache_bounds_and_completed_run_eviction(tmp_path):
    root = tmp_path / "run"
    metrics = root / "telemetry-metrics"
    metrics.mkdir(parents=True)
    for worker in range(3):
        (metrics / f"{worker}.json").write_text(json.dumps(snapshot(str(worker))))
    cache = SnapshotCache(max_files=2)
    assert len(collect_snapshots([], [tmp_path], cache=cache)) == 3
    assert len(cache._entries) == 2
    (root / "telemetry-health.json").write_text('{"workload":{"status":"finished"}}')
    assert collect_snapshots([], [tmp_path], cache=cache) == []
    assert not cache._entries and cache._bytes == 0
    cache = SnapshotCache(max_bytes=1)
    assert len(collect_snapshots([metrics], [], cache=cache)) == 3
    assert not cache._entries


def test_snapshot_cache_invalidates_same_size_rewrite_and_stat_failure(tmp_path, monkeypatch):
    path = tmp_path / "worker.json"
    value = snapshot()
    path.write_text(json.dumps(value))
    cache, counts = SnapshotCache(), {}
    assert cache.read(path, counts)["samples"][0]["value"] == 1
    original = path.stat()
    value["samples"][0]["value"] = 9
    path.write_text(json.dumps(value))
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert cache.read(path, counts)["samples"][0]["value"] == 9
    assert counts["snapshot_reads"] == 2
    stat = Path.stat
    def denied(self, *args, **kwargs):
        if self == path:
            raise PermissionError("unavailable source")
        return stat(self, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", denied)
    assert collect_snapshots([tmp_path], [], cache=cache, counters=counts) == []
    assert not cache._entries and counts["snapshot_rejections"] == 1


def test_application_cannot_spoof_cache_health_counter():
    value = snapshot()
    value["samples"] = [{"name": "telemetry_application_snapshot_cache_hits_total", "value": 999}]
    counts = {}
    samples = build_metrics([value], counters=counts)
    assert not any(sample.name == "telemetry_application_snapshot_cache_hits_total" for sample in samples)
    assert counts["sample_rejections"] == 1


@pytest.mark.parametrize("async_io", [False, True])
def test_sdk_export_error_and_closed_stderr_preserve_workload_exception(tmp_path, monkeypatch, async_io):
    broken = io.StringIO()
    broken.close()
    monkeypatch.setattr("sys.stderr", broken)
    def failed(*args):
        raise OSError("disk full")
    monkeypatch.setattr(EventRecorder, "_persist", failed)
    monkeypatch.setattr(MetricEmitter, "_persist", staticmethod(failed))
    context = CorrelationContext("r", "app", "worker", "0", "n")
    events = EventRecorder(tmp_path, context, async_io=async_io)
    metrics = MetricEmitter(tmp_path, **context.as_dict(), async_io=async_io)
    with pytest.raises(RuntimeError, match="original workload failure"):
        with events.span("tool.call", phase="environment"):
            raise RuntimeError("original workload failure")
    metrics.emit(step=1, samples=[Metric("loss", 1)])
    assert events.close(2) and metrics.close(2)
    assert events.disabled and metrics.disabled
