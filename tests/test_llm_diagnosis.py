"""Optional LLM path: no model/runtime is needed by the default test suite."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from xlayer_telemetry.analysis import llm_diagnosis as llm


def packet():
    return {
        "schema_version": 1, "record_type": "llm_observation_packet", "data_origin": "synthetic",
        "current_interval": {"start": 10, "end": 20, "accuracy": "exact"},
        "observations": [{"id": "m1", "signal": "step_duration_seconds", "current": 20,
                          "baseline": 10, "observation_scope": "application"}],
    }


def answer():
    return {
        "assessment": "insufficient_evidence", "summary": "Step duration이 증가했지만 원인은 확인되지 않았습니다.",
        "candidates": [], "limitations": ["No resource observations"],
    }



def response(result=None, payload=None, **changes):
    result = answer() if result is None else result
    if payload and "decision" in payload["format"]["properties"]:
        result = {"decision": "accept", "issues": [], "diagnosis": result}
    return {"model": "qwen3.5:27b", "done": True, "done_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(result)},
            **changes}


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
        if "decision" in payload["format"]["properties"]:
            return response(payload=payload)
        sent = json.loads(payload["messages"][1]["content"])
        assert len(sent["observation_rows"]) == 64
        return response(payload=payload)
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
        if "decision" in payload["format"]["properties"]:
            return response(payload=payload)
        sent["content"] = payload["messages"][1]["content"]
        view = json.loads(payload["messages"][1]["content"])
        assert view["representation"] == "observation_table_v1"
        assert view["observation_rows"][0][view["observation_columns"].index("current")] == 20
        assert "rule catalog" in payload["messages"][0]["content"]
        return response(payload=payload)
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
    envelope = response(inconsistent)
    envelope["message"]["thinking"] = "internal text"
    monkeypatch.setattr(llm, "_post", lambda *args: envelope)
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


def candidate_answer(ids=None):
    return answer() | {"assessment": "bottleneck_suspected", "candidates": [{
        "title": "Possible pressure", "explanation": "측정값이 함께 변했습니다.",
        "observation_scope": "application", "evidence_ids": ids or ["m1"],
        "counter_evidence_ids": [], "missing_evidence": [], "next_checks": [],
    }]}


@pytest.mark.parametrize("missing", [None, {}, {"sample_count": 0},
                                    {"sample_count": 0, "mean": 40}, {"mean": None}])
def test_missing_statistics_are_not_current_evidence(missing):
    source = packet()
    source["observations"].append({"id": "m2", "signal": "disk_busy_ratio",
                                   "observation_scope": "device", "current": missing})
    result = candidate_answer(["m2"])
    with pytest.raises(ValueError, match="available current measurements"):
        llm.validate_diagnosis(result, source)
    result["candidates"][0].update(evidence_ids=["m1"], counter_evidence_ids=["m2"])
    with pytest.raises(ValueError, match="available current measurements"):
        llm.validate_diagnosis(result, source)
    source["observations"] = source["observations"][1:]
    with pytest.raises(ValueError, match="current measurements are unavailable"):
        llm.validate_diagnosis(answer() | {"assessment": "no_issue_observed"}, source)
    llm.validate_diagnosis(answer(), source)


@pytest.mark.parametrize("valid", [0, {"mean": 0, "sample_count": 1}, {"sampled_increase": 0}])
def test_observed_zero_is_valid_evidence(valid):
    source = packet()
    source["observations"][0]["current"] = valid
    llm.validate_diagnosis(candidate_answer(), source)


@pytest.mark.parametrize("invalid", [True, "40", float("nan"), float("inf"),
                                    {"mean": "40"}, {"sample_count": -1},
                                    {"sample_count": 1.5}, {"verdict": "storage"}])
def test_packet_rejects_untyped_or_nonfinite_measurements(invalid):
    source = packet()
    source["observations"][0]["current"] = invalid
    with pytest.raises(ValueError):
        llm.validate_packet(source)


def test_observation_extra_rule_fields_do_not_reach_model(monkeypatch):
    source = packet()
    source["observations"][0]["verdict"] = "storage"
    monkeypatch.setattr(llm, "_get", lambda *args: pytest.fail("invalid input must not reach Ollama"))
    with pytest.raises(ValueError, match="unsupported observation fields"):
        llm.diagnose(source)


@pytest.mark.parametrize("field", ["summary", "title", "explanation"])
def test_fabricated_ids_in_text_are_rejected(field):
    result = candidate_answer()
    target = result if field == "summary" else result["candidates"][0]
    target[field] = "m999 indicates a problem."
    with pytest.raises(ValueError, match="nonexistent observations"):
        llm.validate_diagnosis(result, packet())


@pytest.mark.parametrize("namespace", ["node", "cluster", "role"])
def test_same_worker_name_in_different_namespaces_is_not_one_entity(namespace):
    source = packet()
    source["observations"] = [
        {"id": "m1", "signal": "memory_usage", "current": 40, "observation_scope": "worker",
         "labels": {"worker": "0", namespace: "A"}},
        {"id": "m2", "signal": "waiting", "current": 10, "observation_scope": "worker",
         "labels": {"worker": "0", namespace: "B"}},
    ]
    with pytest.raises(ValueError, match="different worker entities"):
        llm.validate_diagnosis(candidate_answer(["m1", "m2"]), source)
    source["observations"][1]["labels"][namespace] = "A"
    llm.validate_diagnosis(candidate_answer(["m1", "m2"]), source)


def test_evidence_must_be_unique_and_not_contradict_its_own_reference():
    with pytest.raises(ValueError, match="unique"):
        llm.validate_diagnosis(candidate_answer(["m1", "m1"]), packet())
    result = candidate_answer()
    result["candidates"][0]["counter_evidence_ids"] = ["m1"]
    with pytest.raises(ValueError, match="both supporting and counter"):
        llm.validate_diagnosis(result, packet())


@pytest.mark.parametrize("baseline", [None, {"start": 0, "end": 5}])
def test_periodic_collection_preserves_explicit_baseline(monkeypatch, baseline):
    windows = []
    def query(self, promql, start, end, step):
        windows.append((start, end))
        return {"series": []}
    monkeypatch.setattr(llm.PrometheusClient, "query_range_detail", query)
    monkeypatch.setattr(llm.time, "time", lambda: 1000)
    result = llm.collect_packet({"prometheus_url": "unused", "baseline_interval": baseline,
                                 "queries": [{"signal": "cpu", "unit": "ratio", "scope": "node", "query": "cpu"}]})
    assert result["baseline_interval"] == baseline
    assert windows == [(940, 1000)] + ([(0, 5)] if baseline else [])


def test_overbroad_prometheus_query_is_not_silently_truncated(monkeypatch):
    monkeypatch.setattr(llm.PrometheusClient, "query_range_detail", lambda *args: {
        "series": [{"labels": {"worker": str(i)}, "stats": {"mean": 1}} for i in range(65)]})
    with pytest.raises(ValueError, match="more than 64 series"):
        llm.collect_packet({"prometheus_url": "unused", "baseline_interval": None,
                            "queries": [{"signal": "cpu", "unit": "ratio", "scope": "node", "query": "cpu"}]})


@pytest.mark.parametrize("changes", [{"done": False}, {"done_reason": "unexpected"},
                                    {"model": "different:latest"}, {"error": "backend failed"},
                                    {"message": None}, {"message": {"role": "tool", "content": "{}"}}])
def test_incomplete_or_mismatched_ollama_response_is_rejected(monkeypatch, changes):
    monkeypatch.setattr(llm, "_get", lambda *args: {})
    monkeypatch.setattr(llm, "_post", lambda *args: response(**changes))
    with pytest.raises(llm.RejectedDiagnosis):
        llm.diagnose(packet())


def test_ollama_latest_alias_preserves_model_digest(monkeypatch):
    monkeypatch.setattr(llm, "_get", lambda *args: {"models": [{"name": "test:latest", "digest": "abc"}]})
    monkeypatch.setattr(llm, "_post", lambda *args: response(payload=args[1], model="test:latest"))
    assert llm.diagnose(packet(), model="test")["model_digest"] == "abc"


@pytest.mark.parametrize("error", [TimeoutError("unavailable"),
                                  llm.RejectedDiagnosis("bad output", response())])
def test_cli_failure_replaces_previous_success(monkeypatch, tmp_path, error):
    source, output = tmp_path / "input.json", tmp_path / "diagnosis.json"
    source.write_text(json.dumps(packet()))
    output.write_text('{"record_type":"llm_diagnosis"}')
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(llm, "diagnose", fail)
    monkeypatch.setattr(sys, "argv", ["diagnose", "--input", str(source), "--output", str(output)])
    with pytest.raises(SystemExit) as exit_status:
        llm.main()
    assert exit_status.value.code == 1
    saved = json.loads(output.read_text())
    assert saved["record_type"] == "llm_diagnosis_failure" and "diagnosis" not in saved
    assert saved["observation_packet"] == packet()
    assert not list(tmp_path.glob(".diagnosis.json.*"))


def evaluator():
    path = Path(__file__).resolve().parents[1] / "examples/local-llm/evaluate.py"
    spec = importlib.util.spec_from_file_location("llm_evaluate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("failure", ["assessment", "coverage", "timeout"])
def test_evaluator_fails_on_quality_checks_and_removes_stale_success(monkeypatch, tmp_path, failure):
    evaluate = evaluator()
    case = "insufficient" if failure == "assessment" else "storage_device"
    directory = tmp_path / (case + "-1")
    directory.mkdir()
    (directory / "diagnosis.json").write_text('{"record_type":"llm_diagnosis","stale":true}')
    def diagnose(*args, **kwargs):
        if failure == "timeout":
            raise TimeoutError("offline")
        diagnosis = (answer() | {"assessment": "no_issue_observed"}
                     if failure == "assessment" else candidate_answer(["m1"]))
        return {"diagnosis": diagnosis, "latency_seconds": 1, "input_sha256": "test", "seed": 42}
    monkeypatch.setattr(evaluate, "diagnose", diagnose)
    monkeypatch.setattr(sys, "argv", ["evaluate", "--output", str(tmp_path), "--case", case])
    with pytest.raises(SystemExit) as exit_status:
        evaluate.main()
    assert exit_status.value.code == 1
    assert "stale" not in json.loads((directory / "diagnosis.json").read_text())
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert len(summary) == 1
    if failure == "timeout":
        assert json.loads((directory / "diagnosis.json").read_text())["record_type"] == "llm_diagnosis_failure"


@pytest.mark.parametrize("extra", [["--case", "typo"], ["--repeats", "0"]])
def test_evaluator_rejects_an_empty_test_selection(monkeypatch, tmp_path, extra):
    monkeypatch.setattr(sys, "argv", ["evaluate", "--output", str(tmp_path), *extra])
    with pytest.raises(SystemExit) as exit_status:
        evaluator().main()
    assert exit_status.value.code == 2


def test_generate_only_cannot_leave_a_previous_diagnosis(monkeypatch, tmp_path):
    directory = tmp_path / "normal-1"
    directory.mkdir()
    for filename in ("diagnosis.json", "rejected-response.json"):
        (directory / filename).write_text('{"stale":true}')
    (tmp_path / "summary.json").write_text('[{"stale":true}]')
    evaluate = evaluator()
    monkeypatch.setattr(evaluate, "diagnose", lambda *args, **kwargs: pytest.fail("must not call model"))
    monkeypatch.setattr(sys, "argv", ["evaluate", "--output", str(tmp_path), "--case", "normal", "--generate-only"])
    evaluate.main()
    assert not (directory / "diagnosis.json").exists()
    assert not (directory / "rejected-response.json").exists()
    assert json.loads((directory / "observations.json").read_text())["data_origin"] == "synthetic"
    assert json.loads((tmp_path / "summary.json").read_text()) == []


def test_cli_malformed_input_clears_previous_success_without_saving_nonfinite_data(monkeypatch, tmp_path):
    source, output = tmp_path / "input.json", tmp_path / "diagnosis.json"
    value = packet()
    value["observations"][0]["current"] = float("nan")
    source.write_text(json.dumps(value))
    output.write_text('{"record_type":"llm_diagnosis"}')
    monkeypatch.setattr(sys, "argv", ["diagnose", "--input", str(source), "--output", str(output)])
    with pytest.raises(SystemExit):
        llm.main()
    saved = json.loads(output.read_text())
    assert saved["record_type"] == "llm_diagnosis_failure"
    assert "observation_packet" not in saved


@pytest.mark.parametrize("window", [{"start": True, "end": 20}, {"start": 10, "end": float("inf")},
                                   {"start": 20, "end": 10}, {"start": "10", "end": "20"}])
def test_malformed_interval_is_rejected(window):
    with pytest.raises(ValueError, match="finite start"):
        llm.validate_packet(packet() | {"current_interval": window})


def test_invalid_ollama_metadata_is_a_controlled_failure(monkeypatch):
    monkeypatch.setattr(llm, "_get", lambda *args: {"models": [None]})
    with pytest.raises(RuntimeError, match="metadata"):
        llm.diagnose(packet())


@pytest.mark.parametrize("changes", [{"query_step_seconds": 0}, {"timeout_seconds": float("inf")},
                                    {"current_interval": []}, {"queries": []}])
def test_invalid_collection_options_fail_before_query(monkeypatch, changes):
    monkeypatch.setattr(llm.PrometheusClient, "query_range_detail", lambda *args: pytest.fail("invalid config must not query"))
    config = {"prometheus_url": "unused", "queries": [
        {"signal": "cpu", "query": "cpu", "unit": "ratio", "scope": "node"}], **changes}
    with pytest.raises(ValueError):
        llm.collect_packet(config)


def test_evidence_review_repairs_overclaim_and_preserves_audit_trail(monkeypatch):
    source = packet()
    source["observations"].append({"id": "m2", "signal": "storage_busy_ratio", "current": .98,
                                   "baseline": .2, "observation_scope": "device"})
    draft = candidate_answer(["m1", "m2"])
    draft["summary"] = "Storage caused GPU stalls."
    draft["candidates"][0].update(title="Possible storage contention causing compute idle",
                                 explanation="Storage caused waiting.")
    repaired = json.loads(json.dumps(draft))
    repaired["summary"] = "Step duration과 storage device busy ratio가 증가했습니다."
    repaired["candidates"][0].update(title="Possible storage contention",
                                    explanation="측정값의 변화가 시간상 겹치며 storage contention이 기여했을 가능성이 있습니다.",
                                    missing_evidence=["Per-run I/O attribution", "GPU stall measurement"])
    calls = []
    def post(url, payload, timeout):
        calls.append(payload)
        if len(calls) == 1:
            return response(draft)
        review_input = json.loads(payload["messages"][1]["content"])
        assert review_input["draft"] == draft
        assert review_input["observations"]["observation_rows"]
        return response({"decision": "revise", "issues": ["Stall time은 미측정이며 correlation은 causation을 증명하지 않습니다."],
                         "diagnosis": repaired})
    monkeypatch.setattr(llm, "_get", lambda *args: {})
    monkeypatch.setattr(llm, "_post", post)
    result = llm.diagnose(source)
    assert len(calls) == 2
    assert result["diagnosis"] == repaired
    assert result["draft_diagnosis"] == draft
    assert result["semantic_review"]["decision"] == "revise"
    assert result["semantic_review"]["seed"] == 43
    assert "draft" not in calls[0]["messages"][1]["content"]
    assert "rule catalog" not in calls[1]["messages"][1]["content"]


@pytest.mark.parametrize("case", ["reject", "accept_changed", "revise_without_issues", "invalid_fields",
                                 "causal_title", "causal_summary", "causal_explanation",
                                 "idle_title", "primary_source",
                                 "missing_possible", "missing_reference", "incomplete"])
def test_failed_evidence_review_never_returns_the_unreviewed_draft(monkeypatch, case):
    draft = candidate_answer()
    changed = json.loads(json.dumps(draft))
    changed["summary"] = "Another summary."
    review = {"decision": "accept", "issues": [], "diagnosis": draft}
    if case == "reject":
        review.update(decision="reject", issues=["unsupported cause"])
    elif case == "accept_changed":
        review["diagnosis"] = changed
    elif case == "revise_without_issues":
        review.update(decision="revise", diagnosis=changed)
    elif case == "invalid_fields":
        review["extra"] = "unexpected"
    elif case == "causal_title":
        draft["candidates"][0]["title"] = "Possible storage pressure causing compute stalls"
    elif case == "causal_summary":
        draft["summary"] = "The step slowed because storage was saturated."
    elif case == "causal_explanation":
        draft["candidates"][0]["explanation"] = "Storage caused the slowdown."
    elif case == "idle_title":
        draft["candidates"][0]["title"] = "Possible storage saturation increasing GPU idle time"
    elif case == "primary_source":
        draft["candidates"][0]["explanation"] = "Storage is the primary source of delay."
    elif case == "missing_possible":
        draft["candidates"][0]["title"] = "Storage pressure"
    elif case == "missing_reference":
        changed["candidates"][0]["evidence_ids"] = ["m999"]
        review.update(decision="revise", issues=["fix text"], diagnosis=changed)
    replies = iter([response(draft), response(review, done=case != "incomplete")])
    monkeypatch.setattr(llm, "_get", lambda *args: {})
    monkeypatch.setattr(llm, "_post", lambda *args: next(replies))
    with pytest.raises(llm.RejectedDiagnosis) as failure:
        llm.diagnose(packet())
    assert failure.value.response["stage"] == "evidence_review"


def test_evidence_review_shares_generation_deadline(monkeypatch):
    clock = iter([0, 601])
    calls = []
    monkeypatch.setattr(llm.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(llm, "_get", lambda *args: {})
    monkeypatch.setattr(llm, "_post", lambda *args: calls.append(args) or response())
    with pytest.raises(TimeoutError, match="deadline exceeded"):
        llm.diagnose(packet(), timeout=600)
    assert len(calls) == 1


@pytest.mark.parametrize("explanation", [
    "The step might be slower due to storage pressure; per-run attribution is missing.",
    "These signals do not prove storage caused the slowdown.",
])
def test_language_guard_retains_hypotheses_and_explicit_causality_limits(explanation):
    result = candidate_answer()
    result["candidates"][0]["explanation"] = explanation
    llm.validate_reviewed_language(result)


@pytest.mark.parametrize("field", ["summary", "title", "explanation"])
def test_korean_postpositions_do_not_hide_fabricated_ids(field):
    result = candidate_answer()
    target = result if field == "summary" else result["candidates"][0]
    target[field] = "m999가 증가했습니다."
    with pytest.raises(ValueError, match="nonexistent observations"):
        llm.validate_diagnosis(result, packet())


def test_korean_postpositions_do_not_hide_missing_evidence_references():
    source = packet()
    source["observations"].append({"id": "m2", "signal": "gpu_utilization_percent", "current": 40,
                                   "observation_scope": "device"})
    result = candidate_answer()
    result["candidates"][0]["explanation"] = "m1은 증가했고 m2는 감소했습니다."
    with pytest.raises(ValueError, match="missing from evidence"):
        llm.validate_diagnosis(result, source)
    result["candidates"][0]["evidence_ids"].append("m2")
    llm.validate_diagnosis(result, source)


@pytest.mark.parametrize("text, rejected", [
    ("storage saturation이 slowdown을 유발했습니다.", True),
    ("storage saturation이 slowdown의 원인입니다.", True),
    ("storage saturation이 slowdown을 유발했을 가능성이 있습니다.", False),
    ("관측값만으로 storage가 원인이라고 단정할 수 없습니다.", False),
])
def test_korean_causality_wording_distinguishes_hypotheses(text, rejected):
    result = candidate_answer()
    result["candidates"][0]["explanation"] = text
    if rejected:
        with pytest.raises(ValueError, match="unqualified causal"):
            llm.validate_reviewed_language(result)
    else:
        llm.validate_reviewed_language(result)


def test_english_only_review_cannot_be_accepted_as_korean_output(monkeypatch):
    draft = answer() | {"summary": "Only English prose."}
    replies = iter([response(draft), response({"decision": "accept", "issues": [], "diagnosis": draft})])
    monkeypatch.setattr(llm, "_get", lambda *args: {})
    monkeypatch.setattr(llm, "_post", lambda *args: next(replies))
    with pytest.raises(llm.RejectedDiagnosis, match="Korean prose"):
        llm.diagnose(packet())


@pytest.mark.parametrize("term", ["사용량을 속성화했습니다.", "런별 바이트 속성이 없습니다.",
                                 "요청 수준 속성 증명이 필요합니다."])
def test_attribution_is_not_translated_as_object_attributes(term):
    result = answer() | {"limitations": [term]}
    with pytest.raises(ValueError, match="attribution in English"):
        llm.validate_korean_prose(result)


def test_korean_prose_keeps_technical_terms_in_english():
    result = candidate_answer()
    result["summary"] = "GPU utilization이 감소했고 storage busy가 증가했습니다."
    result["candidates"][0]["missing_evidence"] = ["특정 run의 I/O인지 확인할 attribution evidence가 없습니다."]
    llm.validate_korean_prose(result)
