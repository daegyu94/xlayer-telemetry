"""Measurement metadata survives saved-report input and reviewed LLM projection."""

import json
import sys

import pytest

from xlayer_telemetry.analysis import llm_diagnosis as llm
from xlayer_telemetry.analysis.llm_investigation import project_result


def report():
    return {
        "record_type": "bottleneck_diagnosis", "run_id": "r", "node": "n",
        "trigger_record_id": "step-2", "step": 2, "revision": 1, "analysis_status": "final",
        "analysis_window": {"start": 10, "end": 20, "accuracy": "exact"},
        "verdict": "secret-verdict", "candidates": [{"id": "secret-rule"}],
        "thresholds": {"secret-threshold": 1},
        "comparison": {"signals": [
            {"signal": "disk_read_latency_seconds", "current": .04, "baseline": .01,
             "scope": "device", "labels": {"node": "n", "device": "nvme0n1"},
             "unit": "seconds/operation", "window_statistic": "max", "query": "disk_latency"},
            {"signal": "disk_write_bytes_per_second", "current": 100, "baseline": 100,
             "scope": "device", "labels": {"node": "n", "device": "nvme0n1"},
             "unit": "bytes/s", "window_statistic": "mean", "query": "disk_throughput"},
        ]},
    }


def answer():
    return {
        "assessment": "bottleneck_suspected", "summary": "관측 구간에서 disk latency가 증가했습니다.",
        "candidates": [{
            "title": "Possible disk latency pressure",
            "explanation": "m1의 latency가 증가했고 m2의 throughput은 유지됐습니다.",
            "evidence_ids": ["m1"], "counter_evidence_ids": ["m2"],
            "missing_evidence": ["Run attribution이 확인되지 않았습니다."],
            "observation_scope": "device", "next_checks": ["Device trace를 확인합니다."],
        }],
        "limitations": ["Correlation은 causation을 증명하지 않습니다."],
    }


def restore(view):
    return [view.get("common_observation_fields", {}) | dict(zip(view["observation_columns"], row))
            for row in view["observation_rows"]]


def test_saved_report_keeps_per_signal_units_and_statistics_without_rule_leakage():
    source = report()
    packet = llm.packet_from_report(source)
    llm.validate_packet(packet)
    for original, observation, model_row in zip(source["comparison"]["signals"], packet["observations"],
                                               restore(llm.model_view(packet))):
        for key in ("unit", "window_statistic", "current", "baseline", "labels"):
            assert observation[key] == model_row[key] == original[key]
        assert "query" not in observation  # Saved queries can exceed the model-input budget.
    assert "secret-" not in json.dumps(packet)


@pytest.mark.parametrize("with_metadata", [True, False])
def test_projection_keeps_available_metadata_for_supporting_and_counter_evidence(tmp_path, with_metadata):
    packet = llm.packet_from_report(report())
    for original, observation in zip(report()["comparison"]["signals"], packet["observations"]):
        for key in ("unit", "window_statistic", "query"):
            if with_metadata:
                observation[key] = original[key]
            else:
                observation.pop(key, None)
    result = {"record_type": "llm_diagnosis", "generated_at": "2026-10-05T00:00:00Z",
              "semantic_review": {"decision": "accept"}, "context": packet["context"],
              "current_interval": packet["current_interval"], "observation_packet": packet,
              "diagnosis": answer()}
    rows = [json.loads(line) for line in project_result(tmp_path, result).read_text().splitlines()]
    evidence = [row for row in rows if row.get("observation_id")]
    assert [row["evidence_type"] for row in evidence] == ["supporting", "counter"]
    for original, row in zip(packet["observations"], evidence):
        for key in ("unit", "window_statistic", "query"):
            assert row[key] == original.get(key)
    assert "window_statistic" not in next(row for row in rows if row.get("evidence_type") == "missing")


def test_statistic_is_optional_validated_and_compacted_without_inference():
    packet = llm.packet_from_report(report())
    for item in packet["observations"]:
        item["window_statistic"] = "max"
    llm.validate_packet(packet)
    view = llm.model_view(packet)
    assert view["common_observation_fields"]["window_statistic"] == "max"
    assert restore(view) == packet["observations"]
    for item in packet["observations"]:
        item.pop("window_statistic")
    llm.validate_packet(packet)  # Existing schema-version-1 packets remain valid.
    assert "window_statistic" not in json.dumps(llm.model_view(packet))


def test_saved_report_missing_statistic_stays_unknown_in_mixed_model_rows():
    source = report()
    source["comparison"]["signals"][0].pop("window_statistic")
    packet = llm.packet_from_report(source)
    llm.validate_packet(packet)
    assert "window_statistic" not in packet["observations"][0]
    view = llm.model_view(packet)
    assert "window_statistic" not in view.get("common_observation_fields", {})
    assert [item["window_statistic"] for item in restore(view)] == [None, "mean"]
    source["comparison"]["signals"][1]["window_statistic"] = None
    packet = llm.packet_from_report(source)
    llm.validate_packet(packet)
    assert all("window_statistic" not in item for item in packet["observations"])


def test_withheld_storage_comparison_status_survives_model_input():
    source = report()
    row = source['comparison']['signals'][0]
    row.update(comparison_status='different_report_population', delta=None, delta_percent=None)
    packet = llm.packet_from_report(source)
    llm.validate_packet(packet)
    model_row = restore(llm.model_view(packet))[0]
    assert model_row['comparison_status'] == 'different_report_population'
    assert model_row['current'] == .04 and model_row['baseline'] == .01
    assert model_row['delta'] is None and model_row['delta_percent'] is None
    assert 'comparison_status' not in packet['observations'][1]


@pytest.mark.parametrize("invalid", [None, "", " ", 1, True, ["max"], {"mean": 1}])
def test_invalid_statistic_is_rejected(invalid):
    packet = llm.packet_from_report(report())
    packet["observations"][0]["window_statistic"] = invalid
    with pytest.raises(ValueError, match="window_statistic must be nonempty text"):
        llm.validate_packet(packet)


def test_saved_report_long_queries_do_not_expand_model_input():
    source = report()
    source["comparison"]["signals"] = [
        source["comparison"]["signals"][index % 2] | {"query": f"metric_{index}" + "x" * 1000}
        for index in range(32)
    ]
    packet = llm.packet_from_report(source)
    llm.validate_packet(packet)
    view = llm.model_view(packet)
    assert len(view["observation_rows"]) == 32
    assert len(json.dumps(view, separators=(",", ":"), ensure_ascii=False).encode()) < 8192
    assert "query" not in view["observation_columns"]
    assert [item["window_statistic"] for item in restore(view)] == ["max", "mean"] * 16


def test_selected_report_cli_passes_metadata_to_both_model_calls_and_grafana(tmp_path, monkeypatch):
    folder = tmp_path / "diagnostics"
    folder.mkdir()
    (folder / "diagnostics.jsonl").write_text(json.dumps(report()) + "\n")
    output = folder / "llm.json"
    monkeypatch.setattr(sys, "argv", ["llm_diagnosis", "--run-root", str(tmp_path),
                                    "--record-id", "step-2", "--output", str(output)])
    monkeypatch.setattr(llm, "_get", lambda *args: {})
    sent = []

    def post(url, payload, timeout):
        content = json.loads(payload["messages"][1]["content"])
        reviewing = "decision" in payload["format"]["properties"]
        sent.append(content["observations"] if reviewing else content)
        response = {"decision": "accept", "issues": [], "diagnosis": answer()} if reviewing else answer()
        return {"model": "qwen3.5:27b", "done": True, "done_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(response)}}

    monkeypatch.setattr(llm, "_post", post)
    llm.main()
    assert len(sent) == 2
    for view in sent:
        assert [(item["unit"], item["window_statistic"]) for item in restore(view)] == [
            ("seconds/operation", "max"), ("bytes/s", "mean")]
        assert "secret-" not in json.dumps(view)
    result = json.loads(output.read_text())
    assert result["record_type"] == "llm_diagnosis"
    projected, = (folder / "investigation").glob("llm-*.jsonl")
    evidence = [json.loads(line) for line in projected.read_text().splitlines()
                if json.loads(line).get("observation_id")]
    assert [(row["evidence_type"], row["unit"], row["window_statistic"]) for row in evidence] == [
        ("supporting", "seconds/operation", "max"), ("counter", "bytes/s", "mean")]
