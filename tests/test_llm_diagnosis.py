"""Optional LLM path: no model/runtime is needed by the default test suite."""
import json

import pytest

from xlayer_telemetry import llm_diagnosis as llm


def packet():
    return {
        "schema_version": 1, "record_type": "llm_observation_packet", "data_origin": "synthetic",
        "current_interval": {"start": 10, "end": 20, "accuracy": "exact"},
        "observations": [{"id": "m1", "signal": "step_duration_seconds", "current": 20,
                          "baseline": 10, "observation_scope": "application"}],
    }


def answer():
    return {
        "assessment": "insufficient_evidence", "summary": "Step took longer; cause is unknown.",
        "candidates": [], "limitations": ["No resource observations"],
    }


def test_saved_report_does_not_send_rule_results_or_thresholds():
    report = {"record_type": "bottleneck_diagnosis", "verdict": "secret-verdict",
              "findings": ["secret-finding"], "candidates": [{"id": "secret-rule"}],
              "thresholds": {"secret-threshold": 1}, "limitations": ["secret-rule-derived-text"],
              "comparison": {"signals": [{"signal": "gpu_utilization_percent", "current": 40,
                                            "baseline": 90, "scope": "device", "labels": {"gpu": "1"}}]}}
    observations = llm.packet_from_report(report)
    assert "secret-" not in json.dumps(observations)
    assert observations["observations"][0]["labels"] == {"gpu": "1"}
    llm.validate_packet(observations)


def test_live_collection_preserves_engine_identity_and_missing_baseline(monkeypatch):
    def query(self, promql, start, end, step):
        if promql == "unavailable":
            raise TimeoutError("backend timeout")
        return {"series": [{"labels": {"instance": "A" if start == 10 else "B"},
                            "stats": {"mean": .99, "sample_count": 2, "max_series_delta": .1}}]}
    monkeypatch.setattr(llm.PrometheusClient, "query_range_detail", query)
    result = llm.collect_packet({
        "prometheus_url": "http://unused", "current_interval": {"start": 10, "end": 20},
        "baseline_interval": {"start": 0, "end": 5}, "queries": [
            {"signal": "kv", "query": "kv_usage", "unit": "ratio", "scope": "service"},
            {"signal": "missing", "query": "unavailable", "unit": "bytes", "scope": "node"},
        ],
    })
    a, b = result["observations"]
    assert a["labels"] == {"instance": "A"} and a["baseline"] is None
    assert b["labels"] == {"instance": "B"} and b["current"] is None
    assert "max_series_delta" not in a["current"]
    assert "current:missing:TimeoutError" in result["missing_sources"]


def test_response_validation_rejects_fabricated_references():
    result = answer()
    result["candidates"] = [{"title": "Possible scheduler wait", "explanation": "Investigate waiting",
                             "evidence_ids": ["invented"], "counter_evidence_ids": [],
                             "missing_evidence": ["scheduler traces"], "observation_scope": "application",
                             "next_checks": ["Collect scheduler delay"]}]
    with pytest.raises(ValueError, match="existing observations"):
        llm.validate_diagnosis(result, packet())
    result["candidates"][0]["evidence_ids"] = ["m1"]
    llm.validate_diagnosis(result, packet())  # free hypothesis name: no catalog membership check


def test_packet_rejects_duplicate_ids_and_oversize():
    value = packet()
    value["observations"] *= 2
    with pytest.raises(ValueError, match="unique"):
        llm.validate_packet(value)
    value = packet() | {"context": {"log": "x" * 30001}}
    with pytest.raises(ValueError, match="8KiB"):
        llm.validate_packet(value)


def test_model_receives_measurements_and_preserves_input(monkeypatch):
    source = packet()
    original = json.dumps(source, sort_keys=True)
    monkeypatch.setattr(llm, "_get", lambda *args: {})
    def post(url, payload, timeout):
        assert json.loads(payload["messages"][1]["content"]) == source
        assert "rule catalog" in payload["messages"][0]["content"]
        return {"model": "test-model", "message": {"content": json.dumps(answer())}, "done_reason": "stop"}
    monkeypatch.setattr(llm, "_post", post)
    result = llm.diagnose(source)
    assert result["diagnosis_method"] == "llm"
    assert result["data_origin"] == "synthetic"
    assert result["observation_packet"] == source
    assert json.dumps(source, sort_keys=True) == original
    assert len(result["input_sha256"]) == 64


def test_timeout_and_truncated_generation_do_not_become_diagnoses(monkeypatch):
    monkeypatch.setattr(llm, "_get", lambda *args: {})
    monkeypatch.setattr(llm, "_post", lambda *args: {"done_reason": "length"})
    with pytest.raises(ValueError, match="token limit"):
        llm.diagnose(packet())
    def fail(*args):
        raise TimeoutError("model timed out")
    monkeypatch.setattr(llm, "_post", fail)
    with pytest.raises(TimeoutError):
        llm.diagnose(packet())


def test_inconsistent_normal_response_is_rejected_and_preserved(monkeypatch):
    monkeypatch.setattr(llm, "_get", lambda *args: {})
    inconsistent = answer() | {"assessment": "no_issue_observed", "candidates": [{
        "title": "Normal metrics", "explanation": "No problem", "observation_scope": "application",
        "evidence_ids": ["m1"], "counter_evidence_ids": [], "missing_evidence": [], "next_checks": [],
    }]}
    response = {"message": {"content": json.dumps(inconsistent), "thinking": "internal text"}}
    monkeypatch.setattr(llm, "_post", lambda *args: response)
    with pytest.raises(llm.RejectedDiagnosis) as error:
        llm.diagnose(packet())
    assert error.value.response["final_output"] == json.dumps(inconsistent)
    assert "thinking" not in json.dumps(error.value.response)


def test_unknown_interval_rejects_candidates_but_allows_insufficient_evidence():
    source = packet() | {"current_interval": {"start": None, "end": None, "accuracy": "unknown"}}
    result = answer() | {"assessment": "bottleneck_suspected", "candidates": [{
        "title": "Storage delay", "explanation": "Possible delay", "observation_scope": "node",
        "evidence_ids": ["m1"], "counter_evidence_ids": [], "missing_evidence": [], "next_checks": [],
    }]}
    with pytest.raises(ValueError, match="interval timing"):
        llm.validate_diagnosis(result, source)
    llm.validate_diagnosis(answer(), source)
