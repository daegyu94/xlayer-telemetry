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


def test_application_calibration_is_not_source_collection_clock_alignment():
    clock={'status':'aligned','scope':'mapped_workload_to_prometheus_scrape_time',
           'nodes':{'trainer':{'status':'aligned'}},
           'system_clock_screening':{'status':'unsafe','nodes':{'trainer':{'status':'unsafe'}}}}
    result=collect_storage_series(Source([distribution(101,10)]),{'start':100,'end':105},None,
        settings=settings(host_clock_nodes={'storage-a':'trainer'}),clock_quality=clock,queried_at=200)
    assert result['current']['quality']['host_clock_coverage']=='unknown'


def test_structured_series_identity_cannot_collide_on_label_delimiters():
    first, second = distribution(101, 10), distribution(102, 20)
    first['labels'].update(tag='a,thread=x', thread='y')
    second['labels'].update(tag='a', thread='x,thread=y')
    result = collect_storage_series(Source([first, second]), {'start':100, 'end':105}, None,
        settings=settings(), clock_quality={}, queried_at=200)
    rows = project_storage_series(result, {'observed_at':105})
    assert rows[0]['series_key'] != rows[1]['series_key']
    first['labels'] = dict(reversed(list(first['labels'].items())))
    repeated = collect_storage_series(Source([first]), {'start':100, 'end':105}, None,
        settings=settings(), clock_quality={}, queried_at=200)
    assert project_storage_series(repeated, {'observed_at':105})[0]['series_key'] == rows[0]['series_key']


@pytest.mark.parametrize('end, status, delta', [
    (110, 'different_report_window_exposure', None),
    (105, 'shared_report_window', 50),
])
def test_reset_report_amount_delta_requires_equal_window_exposure(end, status, delta):
    class Counters(Source):
        def query_counter_series(self, start, end, **kwargs):
            return [{'timestamp_seconds':start+1, 'metricName':'storage_client.data_payload_bytes',
                     'labels':dict(LABELS), 'kind':'reset_after_collect', 'unit':'bytes',
                     'observed_sum':100 if start == 100 else 50}]
    clock = {'status':'aligned', 'nodes':{'storage-monitor':{'status':'aligned'}}}
    clock['baseline'] = {'status':'aligned', 'nodes':clock['nodes']}
    result = collect_storage_series(Counters([]), {'start':100, 'end':end}, {'start':80, 'end':85},
        settings=settings(counter_metrics=['storage_client.data_payload_bytes'],
                          host_clock_nodes={'storage-a':'storage-monitor'}),
        clock_quality=clock, queried_at=200)
    row, = result['comparison']['rows']
    assert row['current'] == 100 and row['baseline'] == 50
    assert row['delta'] == delta
    assert row['delta_percent'] == (100 if delta is not None else None)
    assert row['comparison_status'] == status


@pytest.mark.parametrize('case,expected', [
    ('longer_window', 'different_report_window_exposure'),
    ('more_reports', 'different_report_population'),
    ('missing_second', 'different_report_population'),
    ('unknown_reports', 'report_population_unknown'),
    ('equal', 'shared_report_window'),
])
def test_distribution_extrema_delta_requires_comparable_report_population(case, expected):
    current = [distribution(101, 20), distribution(102, 50)]
    baseline = [distribution(81, 10), distribution(82, 10)]
    if case == 'more_reports':
        current[0]['report_count'] = 100
    elif case == 'missing_second':
        baseline.pop()
        baseline[0]['report_count'] = 2  # Equal report count, different observed seconds.
    elif case == 'unknown_reports':
        baseline[0].pop('report_count')
    clock = {'status': 'aligned', 'nodes': {'storage': {'status': 'aligned'}}}
    clock['baseline'] = dict(clock)
    result = collect_storage_series(Source(current + baseline),
        {'start': 100, 'end': 110 if case == 'longer_window' else 105}, {'start': 80, 'end': 85},
        settings=settings(host_clock_nodes={'storage-a': 'storage'}), clock_quality=clock, queried_at=200)
    row, = result['comparison']['rows']
    assert (row['current'], row['baseline']) == (50, 10)
    assert row['comparison_status'] == expected
    assert row['delta_percent'] == (400 if case == 'equal' else None)
    assert row['comparison_quality']['current_report_count'] == (101 if case == 'more_reports' else 2)
    assert row['comparison_quality']['current_reported_seconds'] == 2
    assert row['comparison_quality']['complete_collection_coverage'] == 'unknown'
    from xlayer_telemetry.analysis.storage_series import project_storage_summary
    summary = next(row for row in project_storage_summary(result, {}) if row['row_kind'] == 'storage_comparison')
    assert summary['host_clock_coverage'] == 'screened_aligned'


def test_storage_series_demo_preserves_production_candidate_coverage_limits(tmp_path):
    from xlayer_telemetry.demos.diagnosis import generate
    report = generate(tmp_path/'demo', run_id='synthetic-storage-coverage', clock=lambda:2000, storage_series=True)
    candidates = [candidate for candidate in report['candidates']
                  if any(str(item.get('signal','')).startswith('threefs_') for item in candidate.get('evidence',[]))]
    assert candidates
    assert all(candidate['state'] != 'strong_signal' for candidate in candidates)
    assert all('threefs_collection_interval_and_complete_operation_coverage' in candidate['missing_evidence'] for candidate in candidates)
