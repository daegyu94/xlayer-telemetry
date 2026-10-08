"""Collection observations are shared context, never phase I/O ownership."""
import pytest

from xlayer_telemetry.analysis.storage_series import collect_storage_series, project_storage_series, validate_series_settings


LABELS = {key: "" for key in ("host", "tag", "mount_name", "instance", "io", "uid", "method", "pod", "thread", "statusCode")}
LABELS.update(host="storage-a", instance="batchRead", method="read")


def distribution(stamp, value):
    return {"timestamp_seconds": stamp, "metricName": "storage_client.overall_latency", "labels": dict(LABELS),
            "count": 5, "weighted_mean": value, "max": value, "max_observed_p99": value, "report_count": 1}


class Source:
    def __init__(self, rows, broken=False):
        self.rows, self.calls, self.broken = rows, [], broken

    def query_distribution_series(self, start, end, **kwargs):
        self.calls.append((start, end, kwargs))
        if self.broken:
            raise TimeoutError("backend-url-secret")
        return [row for row in self.rows if start <= row["timestamp_seconds"] < end]

    def query_counter_series(self, *args, **kwargs):
        return []


def settings(**extra):
    return {"enabled": True, "distribution_metrics": ["storage_client.overall_latency"],
            "distribution_units": {"storage_client.overall_latency": "ns"}, **extra}


def test_timestamp_spike_survives_and_sparse_buckets_are_not_zero_filled():
    rows = [distribution(101, 10), distribution(103, 900), distribution(104, 10)]
    result = collect_storage_series(Source(rows), {"start": 100, "end": 105, "accuracy": "approximate"},
                                    None, settings=settings(), clock_quality={"status": "unchecked"}, queried_at=200)
    assert [point["timestamp_seconds"] for point in result["current"]["distributions"]] == [101, 103, 104]
    assert result["current"]["quality"]["collection_interval"] == "unknown"
    assert result["current"]["quality"]["coverage"] == "returned_reports_only"
    assert result["current"]["distributions"][1]["max_observed_p99"] == 900


def test_failed_series_does_not_become_empty_or_leak_backend_error():
    result = collect_storage_series(Source([], broken=True), {"start": 100, "end": 105}, None,
                                    settings=settings(), clock_quality={}, queried_at=200)
    assert result["current"]["distribution_status"] == "query_failed"
    assert result["current"]["counter_status"] == "not_configured"
    assert "backend-url-secret" not in str(result)


def test_projection_keeps_owner_index_time_and_raw_baseline_clock():
    result = collect_storage_series(Source([distribution(81, 10), distribution(101, 20)]),
        {"start": 100, "end": 105}, {"start": 80, "end": 85}, settings=settings(), clock_quality={}, queried_at=200)
    rows = project_storage_series(result, {"observed_at": 105, "window_end_ms": 105000, "record_id": "owner"})
    assert {row["observed_at"] for row in rows} == {105}
    assert {row["sample_timestamp_ms"] for row in rows} == {81000, 101000}
    assert {row["window_role"] for row in rows} == {"current", "baseline"}
    assert all(row["source_host"] == "storage-a" and row["unit"] == "ns" for row in rows)
    assert all(row["row_kind"] == "storage_sample" and row["observation_scope"] == "shared-service" for row in rows)


def test_short_phase_and_unknown_host_clock_never_certify_phase_attribution():
    result = collect_storage_series(Source([distribution(100, 20)]),
        {"start": 99.8, "end": 100.2, "accuracy": "exact"}, None,
        settings=settings(), clock_quality={"status": "aligned", "nodes": {"trainer": {"status": "aligned"}}}, queried_at=200)
    quality = result["current"]["quality"]
    assert quality["phase_attribution"] == "not_established"
    assert quality["timestamp_resolution_seconds"] == 1
    assert quality["host_clock_coverage"] == "unknown"
    assert "interval_shorter_than_timestamp_resolution" in quality["issues"]
    assert not result["comparison"]["eligible"]


@pytest.mark.parametrize("extra", [{"max_points": 2001}, {"enabled": 1}, {"distribution_units": {"m": "guessed"}},
                                   {"counter_metrics": ["x"] * 17}])
def test_series_configuration_is_bounded(extra):
    with pytest.raises(ValueError):
        validate_series_settings(settings(**extra))


def test_engine_optional_series_reaches_saved_projection_without_default_query_growth():
    from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, _investigation_rows
    class Storage(Source):
        def query_window(self, *args):
            return []
    class Prom:
        def query_range(self, *args):
            return None
    record = {"record_id": "r1", "run_id": "run", "node": "trainer", "worker_id": "driver",
              "boundary_scope": "trainer_update", "execution_mode": "async", "observed_at": 105,
              "step_duration_seconds": 5, "analysis_window": {"start": 100, "end": 105, "accuracy": "approximate"}}
    source = Storage([distribution(103, 900)])
    config = {"prometheus": {"url": "http://unused"}, "threefs": {"url": "http://unused"}}
    ordinary = DiagnosticEngine(config, prometheus=Prom(), threefs=source).analyze(record, [])
    assert not source.calls and "storage_series" not in ordinary
    enhanced = DiagnosticEngine({**config, "threefs": {**config["threefs"], "time_series": settings()}},
                                prometheus=Prom(), threefs=source).analyze(record, [])
    sample, = [row for row in _investigation_rows(enhanced) if row["row_kind"] == "storage_sample"]
    assert sample["record_id"] == "r1" and sample["observed_at"] == 105
    assert sample["sample_timestamp_ms"] == 103000 and sample["unit"] == "ns"
    assert sample["boundary_scope"] == "trainer_update" and sample["phase_attribution"] == "not_established"


def test_concurrent_phase_owner_does_not_relabel_or_duplicate_source_ownership():
    result = collect_storage_series(Source([distribution(101, 20)]), {"start": 100, "end": 105}, None,
        settings=settings(), clock_quality={}, queried_at=200)
    a=project_storage_series(result,{"record_id":"rollout-owner","node":"trainer-a","observed_at":105})[0]
    b=project_storage_series(result,{"record_id":"update-owner","node":"trainer-b","observed_at":105})[0]
    assert a["series_key"] == b["series_key"] and a["source_host"] == b["source_host"] == "storage-a"
    assert a["phase_attribution"] == b["phase_attribution"] == "not_established"


def test_baseline_delta_requires_explicit_source_clock_coverage():
    window={"start":100,"end":105,"accuracy":"approximate"}
    base={"start":80,"end":85,"accuracy":"approximate"}
    clock={"status":"aligned","nodes":{"storage-monitor":{"status":"aligned"}}}
    clock["baseline"]={"status":"aligned","nodes":clock["nodes"]}
    source=Source([distribution(81,10),distribution(101,20)])
    before=collect_storage_series(source,window,base,settings=settings(),clock_quality=clock,queried_at=200)
    assert before["comparison"]["rows"][0]["delta"] is None
    after=collect_storage_series(source,window,base,settings=settings(host_clock_nodes={"storage-a":"storage-monitor"}),clock_quality=clock,queried_at=200)
    assert after["comparison"]["rows"][0]["delta"] == 10
    assert after["current"]["quality"]["phase_attribution"] == "not_established"


def test_series_point_budget_is_global_across_current_and_baseline():
    source=Source([distribution(81,10),distribution(101,20),distribution(102,30)])
    result=collect_storage_series(source,{"start":100,"end":105},{"start":80,"end":85},
        settings=settings(max_points=2),clock_quality={},queried_at=200)
    assert len(result["current"]["distributions"])==2
    assert result["baseline"]["distribution_status"]=='budget_exhausted'
    assert len(source.calls)==1


def test_cli_explicit_window_requires_both_bounds_and_does_not_reset_user_state():
    from xlayer_telemetry.subsystems import inspect_threefs
    for args in ({'start':100},{'end':105},{'start':105,'end':100},{'start':100,'end':4000}):
        with pytest.raises(ValueError):inspect_threefs({},**args)
    assert inspect_threefs({},start=100,end=105,series=True)=={'status':'not_configured'}
