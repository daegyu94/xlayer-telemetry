"""Backend annotations and dropped samples remain bounded, private evidence quality."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from xlayer_telemetry import prometheus
from xlayer_telemetry.analysis import llm_diagnosis as llm
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, write_report
from xlayer_telemetry.analysis.evidence_quality import validate_quality
from xlayer_telemetry.analysis.llm_diagnosis import (
    collect_packet, model_view, packet_from_report, validate_packet,
)


SECRET = "http://user:private-token@backend.invalid/?secret=do-not-forward"
SIGNAL = "vllm_queue_p95_seconds"
QUERY = 'histogram_quantile(0.95, rate(vllm:request_queue_time_seconds_bucket{node="n"}[1m]))'
CURRENT = {"start": 100, "end": 140, "accuracy": "exact"}
BASELINE = {"start": 0, "end": 40, "accuracy": "exact"}


@pytest.fixture(autouse=True)
def stub_direct_loader(monkeypatch):
    # The direct deadline client can use a different HTTP transport. Keep these
    # parsing/propagation fixtures on the same response stub as the rule client.
    monkeypatch.setattr(llm.PrometheusClient, "_load_payload",
                        lambda self, request: prometheus._read_json(request, self.timeout), raising=False)


def payload(*, current=True, annotated=True, only_invalid=False):
    start = 100 if current else 0
    valid = {"metric": {"engine": "repaired", "node": "n"},
             "values": [[start, "1" if current else ".1"], [start + 10, "1.2" if current else ".2"]]}
    invalid = {"metric": {"engine": "omitted", "node": "n"},
               "values": [[start, "NaN"], [start + 10, "+Inf"]]}
    result = [] if only_invalid else [valid]
    if annotated or only_invalid:
        result.append(invalid)
    return {"status": "success", "data": {"resultType": "matrix", "result": result},
            **({"warnings": [SECRET], "infos": [SECRET + "/repair"]} if annotated else {})}


def config():
    return {"prometheus_url": "http://unused", "query_step_seconds": 10,
            "current_interval": CURRENT, "baseline_interval": BASELINE,
            "queries": [{"signal": SIGNAL, "query": QUERY, "unit": "seconds", "scope": "service"}]}


def response(request, _timeout):
    params = parse_qs(urlsplit(request.full_url).query)
    if "request_queue_time_seconds_bucket" not in params["query"][0]:
        return {"status": "success", "data": {"result": []}}
    current = float(params["start"][0]) >= 100
    return payload(current=current, annotated=current)


def assert_private(value):
    encoded = json.dumps(value, allow_nan=False)
    assert SECRET not in encoded and "private-token" not in encoded


def test_client_preserves_safe_query_wide_partial_result_summary(monkeypatch):
    monkeypatch.setattr(prometheus, "_read_json", lambda *_: payload())
    detail = prometheus.PrometheusClient("http://unused").query_range_detail(QUERY, 100, 140, 10)
    assert detail["result_quality"] == {
        "scope": "query", "warning_count": 1, "info_count": 1,
        "discarded_sample_count": 2, "discarded_series_count": 1,
    }
    assert [item["labels"]["engine"] for item in detail["series"]] == ["repaired"]
    assert detail["aggregate"]["min"] == 1
    assert detail["aggregate"]["max"] == 1.2
    assert detail["aggregate"]["sample_count"] == 2
    assert_private(detail)


def test_partly_invalid_series_counts_discarded_samples_without_losing_valid_values(monkeypatch):
    body = payload(annotated=False)
    body["data"]["result"][0]["values"] += [None, [120, "NaN"], [130, "0"]]
    monkeypatch.setattr(prometheus, "_read_json", lambda *_: body)
    detail = prometheus.PrometheusClient("http://unused").query_range_detail(QUERY, 100, 140, 10)
    assert detail["result_quality"]["discarded_sample_count"] == 2
    assert detail["result_quality"]["discarded_series_count"] == 0
    assert detail["aggregate"]["min"] == 0
    assert detail["aggregate"]["sample_count"] == 3


def test_annotation_counts_are_capped_and_info_does_not_claim_discarded_coverage(monkeypatch):
    body = payload(annotated=False)
    body["infos"] = [SECRET] * 1001
    monkeypatch.setattr(prometheus, "_read_json", lambda *_: body)
    detail = prometheus.PrometheusClient("http://unused").query_range_detail(QUERY, 100, 140, 10)
    assert detail["result_quality"] == {
        "scope": "query", "warning_count": 0, "info_count": 1000,
        "discarded_sample_count": 0, "discarded_series_count": 0,
    }
    assert_private(detail)


@pytest.mark.parametrize("field,value", [("warnings", SECRET), ("infos", [None])])
def test_malformed_annotations_fail_with_private_safe_error(monkeypatch, field, value):
    body = payload(annotated=False)
    body[field] = value
    monkeypatch.setattr(prometheus, "_read_json", lambda *_: body)
    with pytest.raises(RuntimeError, match="annotations") as error:
        prometheus.PrometheusClient("http://unused").query_range_detail(QUERY, 100, 140, 10)
    assert SECRET not in str(error.value)


@pytest.mark.parametrize("annotated_window", ["current", "baseline"])
def test_direct_llm_preserves_window_specific_quality_and_finite_evidence(monkeypatch, annotated_window):
    def read(request, _timeout):
        current = float(parse_qs(urlsplit(request.full_url).query)["start"][0]) >= 100
        return payload(current=current, annotated=current == (annotated_window == "current"))
    monkeypatch.setattr(prometheus, "_read_json", read)
    packet = collect_packet(config())
    validate_packet(packet)
    assert len(packet["observations"]) == 1
    row = packet["observations"][0]
    assert row["labels"] == {"engine": "repaired", "node": "n"}
    assert row["observation_scope"] == "service"
    assert row["current"]["max"] == 1.2 and row["baseline"]["max"] == .2
    quality = row["sampling_quality"][annotated_window]
    assert quality["query_result"]["discarded_series_count"] == 1
    assert "backend_infos" in quality["warnings"]
    assert f"{annotated_window}:{SIGNAL}:backend_warnings:1" in packet["missing_sources"]
    assert f"{annotated_window}:{SIGNAL}:discarded_samples:2" in packet["missing_sources"]
    assert "discarded_series_count" in json.dumps(model_view(packet))
    assert_private(packet)
    assert_private(model_view(packet))


def test_all_invalid_query_keeps_counts_in_missing_sources_without_zero_observations(monkeypatch):
    monkeypatch.setattr(prometheus, "_read_json", lambda *_: payload(only_invalid=True))
    packet = collect_packet(config())
    validate_packet(packet)
    assert packet["observations"] == []
    for window in ("current", "baseline"):
        assert f"{window}:{SIGNAL}:no_data" in packet["missing_sources"]
        assert f"{window}:{SIGNAL}:discarded_samples:2" in model_view(packet)["missing_sources"]
        assert f"{window}:{SIGNAL}:discarded_series:1" in packet["missing_sources"]
    assert_private(packet)


def test_clean_query_keeps_success_without_backend_limitations(monkeypatch):
    monkeypatch.setattr(prometheus, "_read_json", lambda *_: payload(annotated=False))
    packet = collect_packet(config())
    validate_packet(packet)
    assert packet["missing_sources"] == []
    for window in ("current", "baseline"):
        result = packet["observations"][0]["sampling_quality"][window]["query_result"]
        assert result == {"scope": "query", "warning_count": 0, "info_count": 0,
                          "discarded_sample_count": 0, "discarded_series_count": 0}


def records():
    current = {"run_id": "r", "node": "n", "worker_id": "w", "record_id": "step-2", "step": 2,
               "observed_at": 140, "step_duration_seconds": 40, "stage_durations_seconds": {"gen": 30},
               "analysis_window": CURRENT}
    previous = {**current, "record_id": "step-1", "step": 1, "observed_at": 40,
                "step_duration_seconds": 20, "stage_durations_seconds": {"gen": 10},
                "analysis_window": BASELINE}
    return current, previous


def test_rule_report_saved_projection_and_llm_keep_query_quality(monkeypatch, tmp_path):
    monkeypatch.setattr(prometheus, "_read_json", response)
    current, previous = records()
    report = DiagnosticEngine({"node": "n", "prometheus": {
        "url": "http://unused", "query_step_seconds": 10, "metric_profiles": ["vllm"],
    }}, clock=lambda: 140).analyze(current, [previous])
    quality = report["sampling_quality"][SIGNAL]
    assert quality["current"]["query_result"]["warning_count"] == 1
    assert quality["baseline"]["query_result"]["warning_count"] == 0
    assert f"prometheus:{SIGNAL}:current:discarded_series:1" in report["missing_sources"]
    candidate = next(item for item in report["candidates"] if item["id"] == "rollout_queue_latency")
    assert f"current:{SIGNAL}:backend_infos:1" in candidate["missing_evidence"]
    metric = next(item for item in candidate["evidence"] if item["signal"] == SIGNAL)
    assert metric["sampling_quality"] == quality
    assert metric["value"] == 1.2 and metric["baseline"] == .2
    assert metric["labels"] == {"engine": "repaired", "node": "n"}
    write_report(tmp_path, report)
    saved = json.loads((tmp_path / "latest.json").read_text())
    packet = packet_from_report(saved)
    validate_packet(packet)
    assert "discarded_series_count" in json.dumps(model_view(packet))
    for path in tmp_path.rglob("*.json*"):
        assert SECRET not in path.read_text()
    assert_private(report)
    assert_private(model_view(packet))


@pytest.mark.parametrize("annotated", [False, True])
def test_backend_annotation_caps_strong_rule_without_discarding_valid_measurement(monkeypatch, annotated):
    def read(request, _timeout):
        params = parse_qs(urlsplit(request.full_url).query)
        if "num_requests_waiting" not in params["query"][0]:
            return {"status": "success", "data": {"result": []}}
        current = float(params["start"][0]) >= 100
        body = payload(current=current, annotated=False)
        if current and annotated:
            body["infos"] = [SECRET]
        return body
    monkeypatch.setattr(prometheus, "_read_json", read)
    current, previous = records()
    report = DiagnosticEngine({"node": "n", "prometheus": {"url": "http://unused"}},
                              clock=lambda: 140).analyze(current, [previous])
    candidate = next(item for item in report["candidates"] if item["id"] == "rollout_queue_backlog")
    assert candidate["state"] == "supporting_signal"
    assert 'current:vllm_requests_waiting:source_freshness_unknown' in candidate['missing_evidence']
    assert ("current:vllm_requests_waiting:backend_infos:1" in candidate["missing_evidence"]) == annotated
    assert not any("discarded" in item for item in candidate["missing_evidence"])


@pytest.mark.parametrize("field,value", [("warning_count", -1), ("info_count", 1001),
                                          ("discarded_series_count", True), ("discarded_sample_count", 1.5),
                                          ("scope", "engine"), ("raw_warning", SECRET)])
def test_saved_quality_rejects_unbounded_or_raw_result_metadata(field, value):
    result = {"scope": "query", "warning_count": 0, "info_count": 0,
              "discarded_sample_count": 0, "discarded_series_count": 0}
    result[field] = value
    with pytest.raises(ValueError, match="query result quality"):
        validate_quality({"current": {"query_result": result}})


def test_older_saved_quality_remains_valid():
    validate_quality({"current": {"warnings": ["source_freshness_unknown"], "freshness": "unknown"}})
