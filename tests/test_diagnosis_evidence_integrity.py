"""Regression checks for source evidence that must not imply observed activity."""

import pytest

from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine


class EmptyPrometheus:
    def query_range(self, *args):
        return None


class DistributionWindows:
    def __init__(self, current, baseline):
        self.windows = iter((current, baseline))

    def query_window(self, *args):
        return next(self.windows)


def distribution(count, p99):
    return {"metricName": "client_read_latency", "count": count,
            "weighted_mean": p99, "max": p99, "max_observed_p99": p99}


@pytest.mark.parametrize("current_count,baseline_count", [(0, 5), (5, 0), (None, 5), (5, None)])
def test_threefs_no_samples_cannot_produce_legacy_latency_finding(current_count, baseline_count):
    engine = DiagnosticEngine({"prometheus": {"url": "http://unused"}},
                              prometheus=EmptyPrometheus(), clock=lambda: 101,
                              threefs=DistributionWindows([distribution(current_count, 20)],
                                                          [distribution(baseline_count, 2)]))
    report = engine.analyze(None, [])
    assert report["findings"] == []
    assert report["verdict"] != "bottleneck_suspected"
    # Raw backend rows stay available for inspection; absence isn't converted to zero.
    assert report["evidence"]["threefs_distributions"][0]["count"] == current_count


def test_observed_threefs_latency_regression_keeps_shared_scope():
    engine = DiagnosticEngine({"prometheus": {"url": "http://unused"}},
                              prometheus=EmptyPrometheus(), clock=lambda: 101,
                              threefs=DistributionWindows([distribution(5, 20)], [distribution(5, 2)]))
    finding, = engine.analyze(None, [])["findings"]
    assert finding["candidate"] == "latency_regression"
    assert finding["attribution"] == "shared_storage_window"
    assert finding["signals"]["metrics"][0]["ratio"] == 10


def statistics(value):
    return {"min": value, "max": value, "mean": value, "sample_count": 3}


def tool_event(start, end, span, *, node="tool-a", worker="worker-a"):
    return {"schema_version": 1, "record_type": "span", "name": "tool.call",
            "run_id": "r", "node": node, "worker_id": worker, "role": "rollout", "producer": "agent",
            "trace_id": "trace", "span_id": span, "status": "ok", "boundary_accuracy": "exact",
            "attributes": {"tool": "pytest"}, "duration_seconds": end - start,
            "start_time_unix_nano": int(start * 1e9), "end_time_unix_nano": int(end * 1e9)}


class ConflictingToolMetrics:
    def query_range_detail(self, query, start, end, step):
        if "agent_tool_call_duration_seconds" in query:
            value, labels = 999, {"run_id": "r", "node": "metric-b", "worker_id": "worker-b", "tool": "grep"}
        elif "sandbox_io_pressure_ratio" in query:
            value, labels = .4, {"nodename": "metric-b", "worker_id": "worker-b"}
        elif "node_disk_io_time_seconds_total" in query:
            value, labels = .99, {"nodename": "metric-b", "device": "nvme0n1"}
        else:
            return {"aggregate": None, "series": []}
        stats = statistics(value)
        return {"aggregate": stats, "series": [{"stats": stats, "labels": labels}]}


def tool_report(tmp_path, events):
    import json

    (tmp_path / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    engine = DiagnosticEngine({"prometheus": {"url": "http://unused"},
                               "sandbox": {"enabled": True, "events_dir": str(tmp_path),
                                           "node": "metric-b", "device": "nvme0n1"}},
                              prometheus=ConflictingToolMetrics(), clock=lambda: 101)
    before = {"run_id": "r", "node": "trainer", "worker_id": "driver", "record_id": "before",
              "step": 1, "boundary_scope": "rl_step", "observed_at": 70, "step_duration_seconds": 10,
              "analysis_window": {"start": 60, "end": 70, "accuracy": "approximate"}}
    current = {**before, "record_id": "now", "step": 2, "observed_at": 100,
               "analysis_window": {"start": 80, "end": 100, "accuracy": "approximate"}}
    return engine.analyze(current, [before])


def test_event_override_replaces_metric_identity_and_boundary(tmp_path):
    report = tool_report(tmp_path, [tool_event(61, 62, "before"), tool_event(81, 91, "now")])
    candidate = next(item for item in report["candidates"] if item["id"] == "sandbox_local_storage_pressure")
    tool = next(item for item in candidate["evidence"] if item["signal"] == "tool_duration_seconds")
    assert tool["value"] == 10 and tool["baseline"] == 1
    assert tool["source"] == "event_span_time_window"
    assert tool["labels"] == {"run_id": "r", "node": "tool-a", "worker_id": "worker-a",
                              "role": "rollout", "producer": "agent", "tool": "pytest"}
    assert tool["query"] is None and tool["sampling_quality"] is None
    assert tool["boundary_accuracy"] == "exact"
    assert tool["window"]["start"] == 81 and tool["window"]["end"] == 91
    assert set(candidate["related_nodes"]) == {"tool-a", "metric-b"}
    assert "tool_sandbox_resource_attribution" in candidate["missing_evidence"]
    comparison = next(row for row in report["comparison"]["signals"] if row["signal"] == "tool_duration_seconds")
    assert comparison["labels"] == tool["labels"]


def test_tool_event_baseline_does_not_compare_different_workers(tmp_path):
    report = tool_report(tmp_path, [tool_event(61, 62, "before", worker="other-worker"),
                                   tool_event(81, 91, "now")])
    comparison = next(row for row in report["comparison"]["signals"] if row["signal"] == "tool_duration_seconds")
    assert comparison["baseline"] is None
    assert not any(item["id"] == "sandbox_local_storage_pressure" for item in report["candidates"])


def test_tool_event_baseline_selects_matching_worker_not_longest_other_worker(tmp_path):
    report = tool_report(tmp_path, [tool_event(61, 62, "before"),
                                   tool_event(62, 69, "unrelated", worker="other-worker"),
                                   tool_event(81, 91, "now")])
    comparison = next(row for row in report["comparison"]["signals"] if row["signal"] == "tool_duration_seconds")
    assert comparison["baseline"] == 1


@pytest.mark.parametrize("engine_label", ["engine", "engine_id"])
@pytest.mark.parametrize("namespace", ["instance", "component", "model", "model_name"])
def test_llm_local_engine_index_keeps_endpoint_and_model_namespace(engine_label, namespace):
    from xlayer_telemetry.analysis.llm_diagnosis import validate_diagnosis

    packet = {"schema_version": 1, "record_type": "llm_observation_packet",
              "current_interval": {"start": 10, "end": 20, "accuracy": "exact"},
              "observations": [
                  {"id": "m1", "signal": "kv_usage", "current": .99, "observation_scope": "service",
                   "labels": {"cluster": "c", "node": "n", engine_label: "0", namespace: "a"}},
                  {"id": "m2", "signal": "waiting", "current": 10, "observation_scope": "service",
                   "labels": {"cluster": "c", "node": "n", engine_label: "0", namespace: "b"}}]}
    answer = {"assessment": "bottleneck_suspected", "summary": "Observed pressure", "limitations": [],
              "candidates": [{"title": "Possible KV pressure", "explanation": "Two signals changed.",
                              "observation_scope": "service", "evidence_ids": ["m1", "m2"],
                              "counter_evidence_ids": [], "missing_evidence": [], "next_checks": []}]}
    with pytest.raises(ValueError, match="different.*entities"):
        validate_diagnosis(answer, packet)
    # Comparing peers for the same signal remains legitimate.
    packet["observations"][1]["signal"] = "kv_usage"
    validate_diagnosis(answer, packet)
    packet["observations"][1]["signal"] = "waiting"
    packet["observations"][1]["labels"][namespace] = "a"
    validate_diagnosis(answer, packet)


def test_investigation_projection_keeps_units_statistics_and_expressions():
    from xlayer_telemetry.analysis.diagnostics import _investigation_rows

    measures = [
        {"signal": "disk_read_latency_seconds", "current": .04, "baseline": .01, "delta_percent": 300,
         "scope": "device", "unit": "seconds/operation", "window_statistic": "max", "query": "disk_latency"},
        {"signal": "disk_write_bytes_per_second", "current": 400, "baseline": 100, "delta_percent": 300,
         "scope": "device", "unit": "bytes/s", "window_statistic": "mean", "query": "disk_throughput"},
    ]
    evidence = [{**item, "value": item["current"], "observation_scope": item["scope"]} for item in measures]
    report = {"comparison": {"signals": measures},
              "candidates": [{"id": "pressure", "evidence": evidence[:1], "counter_evidence": evidence[1:]}]}
    rows = _investigation_rows(report)
    for row in (item for item in rows if item["row_kind"] in {"comparison", "evidence"}):
        source = next(item for item in measures if item["signal"] == row["signal"])
        for key in ("unit", "window_statistic", "query"):
            assert row[key] == source[key]


def calibrated_window(raw_start, raw_end, session):
    anchor, offset = (100., -12.) if session == "old" else (200., -112.)
    return {"start": raw_start + offset, "end": raw_end + offset, "accuracy": "calibrated_approximate",
            "time_alignment": {"status": "aligned", "method": "four_timestamp", "node": "n",
                               "reference_id": "monitor", "reference_session": session,
                               "offset_seconds": offset, "round_trip_seconds": .04,
                               "exchange_uncertainty_seconds": .02, "uncertainty_seconds": .04,
                               "local_anchor": anchor, "valid_from": anchor - 10, "valid_until": anchor + 60,
                               "drift_ppm": 100., "raw_window": {"start": raw_start, "end": raw_end}}}


def calibrated_tool(raw_start, raw_end, session, span):
    event = tool_event(raw_start, raw_end, span, node="n")
    event.update(boundary_accuracy="calibrated",
                 time_alignment=calibrated_window(raw_start, raw_end, session)["time_alignment"])
    return event


def calibrated_report(tmp_path, events):
    import json

    (tmp_path / "sandbox-events.jsonl").write_text("".join(json.dumps(item) + "\n" for item in events))
    config = {"node": "n", "cluster": "c", "prometheus": {"url": "http://unused"},
              "clock": {"calibration_reference": "monitor"},
              "sandbox": {"enabled": True, "node": "n", "events_dir": str(tmp_path),
                          "device_major_minor": "259:0"}}
    before = {"run_id": "r", "node": "n", "worker_id": "driver", "record_id": "before",
              "observed_at": 212., "step_duration_seconds": 10,
              "analysis_window": calibrated_window(210., 212., "new")}
    now = {**before, "record_id": "now", "observed_at": 222.,
           "analysis_window": calibrated_window(220., 222., "new")}
    return DiagnosticEngine(config, prometheus=EmptyPrometheus(), clock=lambda: 123).analyze(now, [before])


@pytest.mark.parametrize("event_session", ["old", None])
def test_tool_join_rejects_other_or_unidentified_reference_session(tmp_path, event_session):
    current = calibrated_tool(120., 122., "old", "old-current")
    baseline = calibrated_tool(110., 111., "old", "old-before")
    if event_session is None:
        for event in (current, baseline):
            event["time_alignment"].pop("reference_session")
    report = calibrated_report(tmp_path, [current, baseline])
    assert report["clock_quality"]["status"] == "aligned"
    assert report["clock_quality"]["baseline"]["status"] == "aligned"
    assert "tool_duration_seconds" not in report["evidence"]
    assert not report["candidates"]


def test_tool_join_uses_current_and_baseline_window_sessions(tmp_path):
    current = calibrated_tool(220., 222., "new", "current")
    other_baseline = calibrated_tool(110., 111., "old", "before-old")
    report = calibrated_report(tmp_path, [current, other_baseline])
    comparison = next(row for row in report["comparison"]["signals"] if row["signal"] == "tool_duration_seconds")
    assert comparison["current"] == 2
    assert comparison["baseline"] is None
    matching_baseline = calibrated_tool(210., 211., "new", "before-new")
    report = calibrated_report(tmp_path, [current, other_baseline, matching_baseline])
    candidate = next(item for item in report["candidates"] if item["id"] == "sandbox_local_storage_pressure")
    tool = next(item for item in candidate["evidence"] if item["signal"] == "tool_duration_seconds")
    assert tool["value"] == 2 and tool["baseline"] == 1
    assert tool["window"]["time_alignment"]["reference_session"] == "new"


@pytest.mark.parametrize("session,expected", [("old", "unknown"), ("new", "observed")])
def test_sandbox_device_mapping_keeps_reference_session(tmp_path, session, expected):
    raw = 122. if session == "old" else 222.
    event = {"schema_version": 1, "record_type": "event", "name": "sandbox.resource_sample",
             "run_id": "r", "node": "n", "timestamp_unix_nano": int(raw * 1e9),
             "time_alignment": calibrated_window(raw, raw, session)["time_alignment"],
             "attributes": {"sandbox_node": "n", "io_devices": {"259:0": {"wbytes": 1024}}}}
    report = calibrated_report(tmp_path, [event])
    assert report["sandbox_device_mapping"]["status"] == expected


@pytest.mark.parametrize("current_rows", [[], [distribution(0, 20)], [distribution(None, 20)]])
def test_threefs_baseline_or_empty_distribution_is_not_current_evidence(current_rows):
    engine = DiagnosticEngine({"prometheus": {"url": "http://unused"}},
                              prometheus=EmptyPrometheus(), clock=lambda: 101,
                              threefs=DistributionWindows(current_rows, [distribution(5, 2)]))
    report = engine.analyze(None, [])
    assert report["verdict"] == "insufficient_data"
    assert "threefs:no_data" in report["missing_sources"]
    assert report["evidence"]["threefs_baseline_distributions"] == [distribution(5, 2)]


def test_zero_latency_with_observed_operations_is_real_current_evidence():
    engine = DiagnosticEngine({"prometheus": {"url": "http://unused"}},
                              prometheus=EmptyPrometheus(), clock=lambda: 101,
                              threefs=DistributionWindows([distribution(5, 0)], [distribution(5, 2)]))
    report = engine.analyze(None, [])
    assert report["verdict"] == "no_anomaly_observed"
    assert "threefs:no_data" not in report["missing_sources"]
