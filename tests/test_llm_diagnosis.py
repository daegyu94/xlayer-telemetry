"""Optional LLM path: no model/runtime is needed by the default test suite."""
import hashlib
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
    result["assessment"] = "bottleneck_suspected"
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
    value = packet() | {"context": {"log": "x" * 33000}}
    with pytest.raises(ValueError, match="32KiB"):
        llm.validate_packet(value)


def test_model_view_preserves_measurements_and_entity_labels():
    source = packet()
    source["verdict"] = "rule-derived answer must not enter the model input"
    source["topology"] = [{"from": "trainer", "to": "engine-A"}]
    source["missing_sources"] = ["network:unavailable"]
    source["observations"] = [
        {"id": "m1", "signal": "vllm_waiting", "unit": "requests",
         "observation_scope": "service", "labels": {"engine": "A"},
         "baseline": {"mean": 0, "sample_count": 2},
         "current": {"mean": 3, "sample_count": 3},
         "source": "prometheus", "query": "waiting{engine='A'}"},
        {"id": "m2", "signal": "vllm_waiting", "unit": "requests",
         "observation_scope": "service", "labels": {"engine": "B"},
         "baseline": None, "current": {"mean": 5, "sample_count": 3},
         "source": "prometheus", "query": "waiting{engine='B'}"},
    ]
    view = llm.model_view(source)
    assert view["common_observation_fields"] == {
        "unit": "requests", "observation_scope": "service", "source": "prometheus",
    }
    restored = [dict(zip(view["observation_columns"], row)) |
                view["common_observation_fields"] for row in view["observation_rows"]]
    assert restored == source["observations"]
    assert view["current_interval"] == source["current_interval"]
    assert view["topology"] == source["topology"]
    assert view["missing_sources"] == source["missing_sources"]
    assert "verdict" not in view


def test_model_view_reduces_repeated_series_without_dropping_them(monkeypatch):
    source = packet()
    source["observations"] = [
        {"id": f"m{index}", "signal": "worker_duration_seconds", "unit": "seconds",
         "observation_scope": "worker", "labels": {"rank": str(index)},
         "baseline": 10, "current": 12, "source": "prometheus"}
        for index in range(64)
    ]
    llm.validate_packet(source)
    original = json.dumps(source, sort_keys=True)
    compact = json.dumps(llm.model_view(source), sort_keys=True, separators=(",", ":"))
    assert len(original) > 8192
    assert len(compact.encode()) < 8192
    assert len(llm.model_view(source)["observation_rows"]) == 64
    monkeypatch.setattr(llm, "_get", lambda *args: {})
    def post(url, payload, timeout):
        sent = json.loads(payload["messages"][1]["content"])
        assert len(sent["observation_rows"]) == 64
        return {"message": {"content": json.dumps(answer())}}
    monkeypatch.setattr(llm, "_post", post)
    result = llm.diagnose(source)
    assert len(result["observation_packet"]["observations"]) == 64


def test_oversize_model_view_fails_without_dropping_series(monkeypatch):
    source = packet()
    source["observations"] = [
        {"id": f"m{index}", "signal": f"signal_{index}",
         "observation_scope": "service", "labels": {"engine": str(index)},
         "query": f"metric_{index}_" + "x" * 140, "current": index}
        for index in range(64)
    ]
    llm.validate_packet(source)
    monkeypatch.setattr(llm, "_get", lambda *args: pytest.fail("Ollama must not be contacted"))
    with pytest.raises(ValueError, match="model input exceeds 8KiB"):
        llm.diagnose(source)


def test_model_receives_measurements_and_preserves_input(monkeypatch):
    source = packet()
    original = json.dumps(source, sort_keys=True)
    monkeypatch.setattr(llm, "_get", lambda *args: {})
    sent = {}
    def post(url, payload, timeout):
        sent["content"] = payload["messages"][1]["content"]
        view = json.loads(payload["messages"][1]["content"])
        assert view["representation"] == "observation_table_v1"
        assert view["observation_rows"][0][view["observation_columns"].index("current")] == 20
        assert "rule catalog" in payload["messages"][0]["content"]
        return {"model": "test-model", "message": {"content": json.dumps(answer())}, "done_reason": "stop"}
    monkeypatch.setattr(llm, "_post", post)
    result = llm.diagnose(source)
    assert result["diagnosis_method"] == "llm"
    assert result["data_origin"] == "synthetic"
    assert result["observation_packet"] == source
    assert json.dumps(source, sort_keys=True) == original
    assert result["input_sha256"] == hashlib.sha256(original.encode()).hexdigest()
    assert result["model_input_sha256"] == hashlib.sha256(sent["content"].encode()).hexdigest()
    assert result["model_input_bytes"] == len(sent["content"].encode())


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


def test_insufficient_evidence_cannot_return_a_bottleneck_candidate():
    source = packet()
    result = answer() | {"candidates": [{
        "title": "Unproven wait", "explanation": "A source was not measured.",
        "observation_scope": "application", "evidence_ids": ["m1"],
        "counter_evidence_ids": [], "missing_evidence": [], "next_checks": [],
    }]}
    with pytest.raises(ValueError, match="only bottleneck_suspected"):
        llm.validate_diagnosis(result, source)


def test_missing_current_measurements_cannot_be_called_healthy():
    source = packet()
    source["observations"][0]["current"] = None
    healthy = answer() | {"assessment": "no_issue_observed", "summary": "Healthy."}
    with pytest.raises(ValueError, match="current measurements are unavailable"):
        llm.validate_diagnosis(healthy, source)
    llm.validate_diagnosis(answer(), source)


def test_unknown_interval_rejects_candidates_but_allows_insufficient_evidence():
    source = packet() | {"current_interval": {"start": None, "end": None, "accuracy": "unknown"}}
    result = answer() | {"assessment": "bottleneck_suspected", "candidates": [{
        "title": "Storage delay", "explanation": "Possible delay", "observation_scope": "node",
        "evidence_ids": ["m1"], "counter_evidence_ids": [], "missing_evidence": [], "next_checks": [],
    }]}
    with pytest.raises(ValueError, match="interval timing"):
        llm.validate_diagnosis(result, source)
    llm.validate_diagnosis(answer(), source)


def test_explanation_ids_must_be_in_evidence_lists():
    source = packet()
    source["observations"].append({"id": "m2", "signal": "peer_duration_seconds",
                                   "observation_scope": "worker", "current": 10, "baseline": 10})
    result = answer() | {"assessment": "bottleneck_suspected", "candidates": [{
        "title": "Possible straggler", "explanation": "m1 is slow and m2 is stable.",
        "observation_scope": "worker", "evidence_ids": ["m1"],
        "counter_evidence_ids": [], "missing_evidence": [], "next_checks": [],
    }]}
    with pytest.raises(ValueError, match="missing from evidence"):
        llm.validate_diagnosis(result, source)
    result["candidates"][0]["evidence_ids"].append("m2")
    llm.validate_diagnosis(result, source)


def test_candidate_does_not_merge_distinct_engine_signals():
    source = packet()
    source["observations"] = [
        {"id": "m1", "signal": "kv_cache_usage_ratio", "current": .99,
         "baseline": .4, "observation_scope": "service", "labels": {"engine": "A"}},
        {"id": "m2", "signal": "waiting_requests", "current": 5,
         "baseline": 0, "observation_scope": "service", "labels": {"engine": "B"}},
    ]
    candidate = {"title": "Combined engine bottleneck", "explanation": "Two signals changed.",
                 "observation_scope": "service", "evidence_ids": ["m1", "m2"],
                 "counter_evidence_ids": [], "missing_evidence": [], "next_checks": []}
    result = answer() | {"assessment": "bottleneck_suspected", "candidates": [candidate]}
    with pytest.raises(ValueError, match="different engine entities"):
        llm.validate_diagnosis(result, source)

    source["observations"][0]["signal"] = "participant_duration_seconds"
    source["observations"][1]["signal"] = "participant_duration_seconds"
    source["observations"][0]["labels"] = {"rank": "0"}
    source["observations"][1]["labels"] = {"rank": "1"}
    llm.validate_diagnosis(result, source)


def test_cross_layer_device_labels_do_not_imply_one_identity():
    source = packet()
    source["observations"] = [
        {"id": "m1", "signal": "gpu_utilization_percent", "current": 40,
         "baseline": 90, "observation_scope": "device",
         "labels": {"node": "gpu-0", "device": "gpu0"}},
        {"id": "m2", "signal": "storage_device_utilization_percent", "current": 95,
         "baseline": 30, "observation_scope": "device",
         "labels": {"node": "gpu-0", "device": "nvme0n1"}},
    ]
    result = answer() | {"assessment": "bottleneck_suspected", "candidates": [{
        "title": "Possible device pressure", "explanation": "The device signals changed together.",
        "observation_scope": "device", "evidence_ids": ["m1", "m2"],
        "counter_evidence_ids": [], "missing_evidence": [], "next_checks": [],
    }]}
    llm.validate_diagnosis(result, source)
