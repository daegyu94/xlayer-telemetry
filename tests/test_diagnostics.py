import json
from pathlib import Path

import pytest

from xlayer_telemetry.analysis import diagnostics
from xlayer_telemetry import prometheus
from xlayer_telemetry.analysis.diagnostics import (
    DiagnosticEngine,
    PrometheusClient,
    ThreeFSClient,
    run_once,
)
from xlayer_telemetry.step_history import StepHistoryWriter
from xlayer_telemetry.adapters.verl import iter_file_records


class FakePrometheus:
    def __init__(self, values):
        self.values = values

    def query_range(self, query, start, end, step):
        for needle, value in self.values.items():
            if needle in query:
                return value
        return None


class FakeThreeFS:
    def __init__(self, current, baseline):
        self.responses = [current, baseline]

    def query_window(self, start, end):
        return self.responses.pop(0)


def test_step_history_deduplicates_and_marks_async_update(tmp_path: Path) -> None:
    path = tmp_path / "steps.jsonl"
    record = {
        "step": 7,
        "data": {
            "perf/time_per_step": 12.0,
            "timing_s/gen": 8.0,
            "timing_s/update_actor": 2.0,
        },
    }
    writer = StepHistoryWriter(
        path,
        run_id="run-1",
        node="gpu-a",
        worker_id="driver",
        execution_mode="async",
        clock=lambda: 100.0,
    )

    written = writer.append(record)
    assert written is not None
    assert writer.append(record) is None
    restarted = StepHistoryWriter(
        path,
        run_id="run-1",
        node="gpu-a",
        worker_id="driver",
        execution_mode="async",
    )
    assert restarted.append(record) is None

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["boundary_scope"] == "trainer_update"
    assert loaded["analysis_window"] == {
        "start": 88.0,
        "end": 100.0,
        "accuracy": "approximate",
        "source": "file_logger_observation_minus_reported_duration",
    }
    assert loaded["stage_durations_seconds"] == {"gen": 8.0, "update_actor": 2.0}


def test_replayed_steps_have_no_external_correlation_window(tmp_path: Path) -> None:
    source = tmp_path / "verl.jsonl"
    source.write_text(json.dumps({"step": 1, "data": {"timing_s/step": 10}}) + "\n")
    writer = StepHistoryWriter(tmp_path / "steps.jsonl", run_id="r", node="n",
                               worker_id="driver", clock=lambda: 1000)
    item = writer.append(next(iter_file_records(source, follow=False, poll_interval=0.1)), live=False)
    assert item["observed_at"] == 1000
    assert item["analysis_window"] == {
        "start": None, "end": None, "accuracy": "unknown",
        "source": "replayed_file_logger_without_event_time"}
    prom = FakePrometheus({"gpu_utilization": {"mean": 95}})
    report = DiagnosticEngine({"schema_version": 1, "prometheus": {"url": "http://prometheus"}},
                              prometheus=prom, clock=lambda: 1000).analyze(item, [])
    assert report["verdict"] == "insufficient_data"
    assert report["evidence"] == {}
    assert "step_event_time" in report["missing_sources"]


def test_file_follower_distinguishes_backlog_from_new_records(tmp_path: Path) -> None:
    source = tmp_path / "verl.jsonl"
    source.write_text(json.dumps({"step": 1, "data": {}}) + "\n")
    records = iter_file_records(source, follow=True, poll_interval=0.01)
    assert next(records).live is False
    with source.open("a") as stream:
        stream.write(json.dumps({"step": 2, "data": {}}) + "\n")
    assert next(records).live is True
    records.close()


def test_no_data_diagnosis_retries_and_finalizes_after_scrape(tmp_path: Path) -> None:
    history = tmp_path / "steps.jsonl"
    history.write_text(json.dumps({"schema_version": 1, "record_type": "verl_step_observation",
                                   "record_id": "one", "run_id": "r", "node": "n",
                                   "observed_at": 100, "analysis_window": {"start": 90, "end": 100}}) + "\n")
    clock = [101.0]
    prom = FakePrometheus({})
    engine = DiagnosticEngine({"schema_version": 1, "prometheus": {"url": "http://prometheus"},
                               "retry_interval_seconds": 5}, prometheus=prom, clock=lambda: clock[0])
    output = tmp_path / "diagnostics"
    assert run_once(engine, history, output, periodic_when_idle=False) == 1
    assert json.loads((output / "latest.json").read_text())["analysis_status"] == "provisional"
    assert not (output / "investigation").exists()
    assert run_once(engine, history, output, periodic_when_idle=False) == 0
    prom.values = {"": {"mean": 50}}  # Every attempted source has arrived.
    clock[0] = 106.0
    assert run_once(engine, history, output, periodic_when_idle=False) == 1
    report = json.loads((output / "latest.json").read_text())
    assert report["analysis_status"] == "final"
    assert report["revision"] == 2
    assert (output / "investigation" / "one.jsonl").is_file()
    assert run_once(engine, history, output, periodic_when_idle=False) == 0


def test_missing_backend_has_bounded_retry_and_replay_baseline_is_safe(tmp_path: Path) -> None:
    history = tmp_path / "steps.jsonl"
    records = [
        {"schema_version": 1, "record_type": "verl_step_observation", "record_id": "old",
         "run_id": "r", "node": "n", "worker_id": "driver", "boundary_scope": "rl_step",
         "observed_at": 80, "step_duration_seconds": 10,
         "analysis_window": {"start": None, "end": None, "accuracy": "unknown"}},
        {"schema_version": 1, "record_type": "verl_step_observation", "record_id": "new",
         "run_id": "r", "node": "n", "worker_id": "driver", "boundary_scope": "rl_step",
         "observed_at": 100, "step_duration_seconds": 20,
         "analysis_window": {"start": 90, "end": 100, "accuracy": "approximate"}},
    ]
    history.write_text("".join(json.dumps(record) + "\n" for record in records))
    clock = [101.0]
    engine = DiagnosticEngine({"schema_version": 1, "prometheus": {"url": "http://prometheus"},
                               "retry_seconds": 10, "retry_interval_seconds": 5},
                              prometheus=FakePrometheus({}), clock=lambda: clock[0])
    output = tmp_path / "diagnostics"
    assert run_once(engine, history, output, periodic_when_idle=False) == 2
    clock[0] = 111.0
    assert run_once(engine, history, output, periodic_when_idle=False) == 1
    report = json.loads((output / "latest.json").read_text())
    assert report["trigger_record_id"] == "new"
    assert report["analysis_status"] == "final"
    assert report["comparison"]["baseline_record_id"] is None
    assert report["comparison"]["baseline_interval"] is None
    assert run_once(engine, history, output, periodic_when_idle=False) == 0


def test_counter_reset_and_single_sample_are_distinct() -> None:
    reset = prometheus.series_stats([{"points": [[0, 100], [1, 0], [2, 20]]}])
    assert reset["max_series_delta"] == 20
    assert prometheus.series_stats([{"points": [[0, 100]]}])["max_series_delta"] is None
    assert DiagnosticEngine._signals(None, {"gpu_evictions_delta": {"max": 3,
                                     "max_series_delta": 0}}, [])["gpu_evictions_delta"] == 3


def test_weight_update_alias_is_counted_once() -> None:
    signals = DiagnosticEngine._signals({"stage_durations_seconds": {
        "weight_sync": 5, "update_weights": 5, "all_reduce": 2}}, {}, [])
    assert signals["communication_duration_seconds"] == 7
    assert DiagnosticEngine._signals({"stage_durations_seconds": {
        "update_weights": 5}}, {}, [])["communication_duration_seconds"] == 5


def test_vllm_evidence_does_not_join_different_engines() -> None:
    class DetailedPrometheus:
        def __init__(self, kv_values):
            self.kv_values = kv_values

        def query_range_detail(self, query, start, end, step):
            values = {
                "num_requests_waiting": (0, 5),
                "kv_cache_usage_perc": self.kv_values,
                "num_preemptions_total": (0, 2),
            }
            for needle, pair in values.items():
                if needle in query:
                    field = "max_series_delta" if needle == "num_preemptions_total" else "max"
                    return {"aggregate": {field: max(pair)}, "series": [
                        {"labels": {"node": "n", "instance": name}, "stats": {field: value}}
                        for name, value in zip(("engine-a", "engine-b"), pair)]}
            return {"aggregate": None, "series": []}

    engine = DiagnosticEngine({"schema_version": 1, "prometheus": {"url": "http://prometheus"}},
                              prometheus=DetailedPrometheus((0.99, 0.1)), clock=lambda: 101)
    current = {"record_id": "one", "run_id": "r", "node": "n", "step": 1,
               "analysis_window": {"start": 90, "end": 100}, "step_duration_seconds": 20}
    report = engine.analyze(current, [])
    assert not any(candidate["id"] == "kv_cache_pressure" and
                   candidate["state"] == "strong_signal" for candidate in report["candidates"])
    assert all(evidence["labels"].get("instance") in {None, "engine-a", "engine-b"}
               for candidate in report["candidates"] for evidence in candidate["evidence"])
    engine.prometheus = DetailedPrometheus((0.1, 0.99))
    report = engine.analyze(current, [])
    pressure = next(candidate for candidate in report["candidates"]
                    if candidate["id"] == "kv_cache_pressure")
    assert pressure["state"] == "strong_signal"
    assert {item["labels"]["instance"] for item in pressure["evidence"]} == {"engine-b"}


def test_async_diagnosis_correlates_external_signals_without_claiming_step_ownership() -> None:
    config = {
        "schema_version": 1,
        "prometheus": {"url": "http://prometheus"},
        "execution_mode": "async",
    }
    prom = FakePrometheus(
        {
            "num_requests_waiting": {"max": 3.0},
            "kv_cache_usage": {"max": 0.95},
            "num_preemptions_total": {"max_series_delta": 2.0},
            "ray_tasks": {"max": 4.0},
            "policy_version_lag": {"max": 3.0},
            "gpu_utilization": {"mean": 65.0},
            "MemAvailable": {"min": 0.5},
            "disk_io_time": {"max": 0.2},
        }
    )
    current_3fs = [{"metricName": "client_read_latency", "count": 10, "max_observed_p99": 20.0}]
    baseline_3fs = [{"metricName": "client_read_latency", "count": 10, "max_observed_p99": 5.0}]
    engine = DiagnosticEngine(
        config,
        prometheus=prom,
        threefs=FakeThreeFS(current_3fs, baseline_3fs),
        clock=lambda: 110.0,
    )
    prior = [{"observed_at": 80.0, "stage_durations_seconds": {"gen": 2.0}}]
    current = {
        "record_id": "record-7",
        "run_id": "run-1",
        "node": "gpu-a",
        "step": 7,
        "execution_mode": "async",
        "boundary_scope": "trainer_update",
        "stage_durations_seconds": {"gen": 8.0},
        "analysis_window": {"start": 90.0, "end": 100.0, "accuracy": "approximate"},
    }

    report = engine.analyze(current, prior)

    assert report["verdict"] == "bottleneck_suspected"
    assert report["boundary_scope"] == "trainer_update"
    assert {finding["component"] for finding in report["findings"]} >= {"vllm", "ray", "verl_async", "3fs"}
    assert any("not owned by this step" in item for item in report["limitations"])
    threefs = next(item for item in report["findings"] if item["component"] == "3fs")
    assert threefs["attribution"] == "shared_storage_window"
    assert threefs["signals"]["metrics"][0]["ratio"] == 4.0


def test_no_external_samples_is_insufficient_data() -> None:
    engine = DiagnosticEngine(
        {"schema_version": 1, "prometheus": {"url": "http://prometheus"}},
        prometheus=FakePrometheus({}),
        clock=lambda: 100.0,
    )

    report = engine.analyze(None, [])

    assert report["verdict"] == "insufficient_data"
    assert len(report["missing_sources"]) == len(diagnostics.DEFAULT_QUERIES)


def test_run_once_writes_detailed_and_latest_reports(tmp_path: Path) -> None:
    history = tmp_path / "steps.jsonl"
    history.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "record_type": "verl_step_observation",
                "record_id": "one",
                "run_id": "run-1",
                "node": "gpu-a",
                "step": 1,
                "analysis_window": {"start": 90.0, "end": 100.0},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    engine = DiagnosticEngine(
        {"schema_version": 1, "prometheus": {"url": "http://prometheus"}},
        prometheus=FakePrometheus({}),
        clock=lambda: 101.0,
    )

    assert run_once(engine, history, tmp_path / "diagnostics") == 1
    assert (tmp_path / "diagnostics" / "latest.json").is_file()
    reports = tmp_path / "diagnostics" / "diagnostics.jsonl"
    assert len(reports.read_text().splitlines()) == 1
    assert run_once(
        engine, history, tmp_path / "diagnostics", periodic_when_idle=False
    ) == 0
    assert len(reports.read_text().splitlines()) == 1


def test_threefs_step_waits_for_ingest_settle_before_diagnosis(tmp_path: Path) -> None:
    history = tmp_path / "steps.jsonl"
    history.write_text(json.dumps({
        "schema_version": 1, "record_type": "verl_step_observation",
        "record_id": "late", "run_id": "r", "worker_id": "driver",
        "boundary_scope": "rl_step", "observed_at": 100,
        "analysis_window": {"start": 90, "end": 100},
    }) + "\n")
    clock = [110.0]
    engine = DiagnosticEngine(
        {"schema_version": 1, "prometheus": {"url": "http://prometheus"},
         "threefs": {"url": "http://clickhouse", "settle_seconds": 30}},
        prometheus=FakePrometheus({}), threefs=FakeThreeFS([], []),
        clock=lambda: clock[0],
    )
    output = tmp_path / "diagnostics"
    assert run_once(engine, history, output) == 0
    assert not (output / "diagnostics.jsonl").exists()
    clock[0] = 131.0
    assert run_once(engine, history, output) == 1
    report = json.loads((output / "latest.json").read_text())
    assert report["step"] is None
    assert "threefs:no_data" in report["missing_sources"]


def test_gpu_comparison_uses_the_same_labeled_device() -> None:
    class DetailedPrometheus:
        def query_range_detail(self, query, start, end, step):
            if "telemetry_gpu_utilization_percent" in query:
                values = (40, 90) if start >= 90 else (90, 90)
                return {"aggregate": {"mean": sum(values) / 2},
                        "series": [{"labels": {"gpu": str(index)}, "stats": {"mean": value}}
                                   for index, value in enumerate(values)]}
            if "num_requests_waiting" in query:
                return {"aggregate": {"max": 3 if start >= 90 else 0}, "series": []}
            return {"aggregate": None, "series": []}

    engine = DiagnosticEngine(
        {"schema_version": 1, "prometheus": {"url": "http://prometheus"}},
        prometheus=DetailedPrometheus(), clock=lambda: 101,
    )
    prior = {"run_id": "r", "worker_id": "driver", "boundary_scope": "rl_step",
             "record_id": "prior", "observed_at": 80, "step_duration_seconds": 10,
             "analysis_window": {"start": 70, "end": 80}}
    current = {"run_id": "r", "worker_id": "driver", "boundary_scope": "rl_step",
               "record_id": "current", "observed_at": 100, "step_duration_seconds": 20,
               "analysis_window": {"start": 90, "end": 100, "accuracy": "approximate"}}

    report = engine.analyze(current, [prior])
    starvation = next(item for item in report["candidates"] if item["id"] == "gpu_starvation")
    assert starvation["state"] == "strong_signal"
    assert starvation["related_devices"] == ["0"]
    gpu = next(item for item in starvation["evidence"] if item["signal"] == "gpu_utilization_percent")
    assert gpu["value"] == 40
    assert gpu["baseline"] == 90
    assert gpu["labels"] == {"gpu": "0"}
    assert gpu["observation_scope"] == "device"


def test_threefs_rejects_unbounded_filter_columns() -> None:
    with pytest.raises(ValueError, match="unsupported 3FS filters"):
        ThreeFSClient("http://clickhouse", filters={"metricName": "anything"})


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload

    def __iter__(self):
        return iter(self.payload.splitlines(keepends=True))


def test_prometheus_client_parses_matrix_and_counter_delta(monkeypatch) -> None:
    payload = json.dumps(
        {
            "status": "success",
            "data": {
                "result": [
                    {"metric": {"gpu": "0"}, "values": [[90, "1"], [100, "4"]]},
                    {"metric": {"gpu": "1"}, "values": [[90, "2"], [100, "8"]]},
                ]
            },
        }
    ).encode()
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(payload)

    monkeypatch.setattr(prometheus, "urlopen", fake_urlopen)

    stats = PrometheusClient("http://prometheus", timeout=3).query_range(
        "metric_name", 90, 100, 2
    )

    assert stats == {
        "min": 1.0,
        "mean": 3.75,
        "max": 8.0,
        "last": 8.0,
        "max_series_delta": 6.0,
        "sample_count": 4.0,
    }
    assert "/api/v1/query_range?" in requests[0][0].full_url
    assert requests[0][1] == 3
    detail = PrometheusClient("http://prometheus", timeout=3).query_range_detail(
        "metric_name", 90, 100, 2
    )
    assert [item["labels"]["gpu"] for item in detail["series"]] == ["0", "1"]


def test_threefs_client_uses_bounded_read_only_query_and_env_auth(
    monkeypatch,
) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(
            b'{"metricName":"client_read_latency","sample_count":2,"max_value":4,"max_observed_p99":3}\n'
        )

    monkeypatch.setattr(diagnostics, "urlopen", fake_urlopen)
    monkeypatch.setenv("THREEFS_CLICKHOUSE_USER", "reader")
    monkeypatch.setenv("THREEFS_CLICKHOUSE_PASSWORD", "secret")
    rows = ThreeFSClient(
        "http://clickhouse:8123", filters={"mount_name": "training"}
    ).query_window(90, 100)

    assert rows[0]["metricName"] == "client_read_latency"
    assert rows[0]["count"] == 2
    assert rows[0]["max"] == 4
    query = requests[0][0].data.decode()
    assert "FROM 3fs.distributions" in query
    assert "AS sample_count" in query
    assert "AS count" not in query
    assert "TIMESTAMP >= toDateTime(90)" in query
    assert "TIMESTAMP < toDateTime(100)" in query
    assert "mount_name = 'training'" in query
    assert requests[0][0].get_header("Authorization").startswith("Basic ")


def test_query_configuration_preserves_explicit_overrides_and_optional_scopes():
    engine = DiagnosticEngine({
        'prometheus': {'url': 'http://unused', 'queries': {'tool_duration_seconds': 'my_tool_seconds'}},
        'storage_node': 'storage-a', 'storage_device': 'nvme0n1',
        'sandbox': {'enabled': True, 'node': 'sandbox-a', 'device': 'nvme1n1'},
    })
    queries = engine._queries('cluster-a', with_sandbox=True)
    assert queries['tool_duration_seconds'] == 'my_tool_seconds'
    assert 'job="native"' in queries['vllm_requests_waiting']
    assert 'telemetry_source="vllm"' in queries['vllm_requests_waiting']
    assert 'nodename="{sandbox_node}"' in queries['sandbox_io_pressure_ratio']
    assert 'device="{sandbox_device}"' in queries['sandbox_device_busy_ratio']
    assert 'instance="{storage_node}"' in queries['storage_device_busy_ratio']
    assert 'sandbox_io_pressure_ratio' not in engine._queries('cluster-a')
