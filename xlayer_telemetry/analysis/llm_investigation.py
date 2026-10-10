"""Project an explicitly requested, accepted LLM diagnosis into Grafana evidence."""

from __future__ import annotations

import json
import math
from pathlib import Path
import uuid

from ..fileio import atomic_write_text, json_objects
from ..measurements import finite_number


def selected_report(root: Path, record_id: str) -> dict:
    selected = None
    for report in json_objects(root / "diagnostics/diagnostics.jsonl"):
        if report.get("trigger_record_id") == record_id:
            if selected is not None and (report.get("run_id"), report.get("node")) != (selected.get("run_id"), selected.get("node")):
                raise ValueError("record ID has ambiguous run/node identity")
            if selected is None or report.get("revision", 0) >= selected.get("revision", 0):
                selected = report
    if selected is None:
        raise ValueError("selected step has no diagnosis report")
    if selected.get("analysis_status") == "provisional":
        raise ValueError("selected step diagnosis is provisional; wait for final telemetry")
    return selected


def project_result(root: Path, result: dict) -> Path:
    """No draft/failure projection and no invented rule strength or confidence."""
    if result.get("record_type") != "llm_diagnosis" or result.get("semantic_review", {}).get("decision") not in {"accept", "revise"}:
        raise ValueError("only reviewed LLM diagnoses can be projected")
    packet = result["observation_packet"]
    context = result.get("context", {})
    window = result.get("current_interval") or {}
    start, end = finite_number(window.get("start")), finite_number(window.get("end"))
    invocation = uuid.uuid4().hex
    common = {"schema_version": 1, "diagnosis_method": "llm", "diagnosis_invocation_id": invocation,
              "generated_at": result["generated_at"], "model": result.get("model"),
              "run_id": context.get("run_id"), "node": context.get("node"), "step": context.get("step"),
              "record_id": context.get("trigger_record_id"), "observed_at": end,
              "boundary_accuracy": window.get("accuracy", "unknown"),
              "window_start_ms": math.floor(start*1000) if start is not None else None,
              "window_end_ms": math.ceil(end*1000) if end is not None else None,
              "data_origin": result.get("data_origin", "unknown"),
              "workload_comparability": packet.get("baseline_comparability", "unverified")}
    answer = result["diagnosis"]
    rows = [{**common, "row_kind": "summary", "verdict": answer["assessment"],
             "summary": answer["summary"], "candidate_count": len(answer["candidates"]),
             "step_duration_seconds": next((item.get("current") for item in packet["observations"] if item["signal"] == "step_duration_seconds"), None),
             "primary_candidate": None, "strong_candidate_count": 0,
             "missing_sources": ", ".join(packet.get("missing_sources", []))}]
    observations = {item["id"]: item for item in packet["observations"]}
    for index, candidate in enumerate(answer["candidates"]):
        identifier = f"llm_hypothesis_{index+1}"
        rows.append({**common, "row_kind": "candidate", "candidate_id": identifier,
                     "component": "llm", "state": "hypothesis", "summary": candidate["title"] + ": " + candidate["explanation"],
                     "observation_scope": candidate["observation_scope"],
                     "missing_evidence_summary": ", ".join(candidate["missing_evidence"]),
                     "evidence_summary": ", ".join(candidate["evidence_ids"]),
                     "counter_evidence_summary": ", ".join(candidate["counter_evidence_ids"])})
        for kind, ids in (("supporting", candidate["evidence_ids"]), ("counter", candidate["counter_evidence_ids"])):
            for key in ids:
                item = observations[key]
                rows.append({**common, "row_kind": "evidence", "candidate_id": identifier,
                             "observation_id": key, "evidence_type": kind, "signal": item["signal"],
                             "current": item.get("current"), "baseline": item.get("baseline"),
                             "unit": item.get("unit"), "window_statistic": item.get("window_statistic"),
                             "comparison_status": item.get('comparison_status'),
                             "query": item.get("query"),
                             "observation_scope": item["observation_scope"], "source": item.get("source"),
                             "entity": ",".join(f"{k}={v}" for k, v in item.get("labels", {}).items()),
                             "sampling_quality": json.dumps(item.get("sampling_quality"))})
        for name in candidate["missing_evidence"]:
            rows.append({**common, "row_kind": "evidence", "candidate_id": identifier,
                         "evidence_type": "missing", "signal": name,
                         "observation_scope": candidate["observation_scope"]})
    path = root / "diagnostics/investigation" / f"llm-{invocation}.jsonl"
    atomic_write_text(path, "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows))
    return path
