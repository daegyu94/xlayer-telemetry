"""Persist diagnosis journals and immutable Grafana/Loki projections."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

from ..fileio import append_jsonl, atomic_write_text, json_objects
from ..time_alignment import alignment_metadata
from .clock_quality import project_clock_quality
from .observation_contracts import Observation
from .diagnosis_analysis import finite
from .jsonl_cache import JSONLCache
from .storage_series import project_storage_series, project_storage_summary

def _existing_reports(path: Path, *, cache: JSONLCache | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    reports: dict[str, dict[str, Any]] = {}
    latest = None
    try:
        for value in (cache.read(path) if cache is not None else json_objects(path)):
            if value.get("record_type") == "bottleneck_diagnosis":
                latest = value
            record_id = value.get("trigger_record_id")
            if isinstance(record_id, str):
                reports[record_id] = value
    except FileNotFoundError:
        pass
    return reports, latest

def _investigation_path(directory: Path, report: Mapping[str, Any]) -> Path | None:
    if report.get("trigger") != "step_observed" or report.get("analysis_status") == "provisional":
        return None
    key = re.sub(r"[^A-Za-z0-9_.-]", "_", str(report.get("trigger_record_id") or report.get("generated_at")))
    return directory / "investigation" / f"{key}.jsonl"

def _write_investigation(directory: Path, report: Mapping[str, Any], *, missing_only: bool = False) -> None:
    path = _investigation_path(directory, report)
    if path is None or (missing_only and path.is_file()):
        return
    # Completed projections are immutable for Alloy/Loki. Recovery only creates
    # missing files, so existing tailed records are not replayed.
    rows = _investigation_rows(report)
    atomic_write_text(path, "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows))

def write_report(directory: Path, report: Mapping[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, separators=(",", ":"), sort_keys=True) + "\n"
    append_jsonl(directory / "diagnostics.jsonl", encoded)
    atomic_write_text(directory / "latest.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    _write_investigation(directory, report)

def _projection_recovery(engine, output, reports, latest):
    """Plan repair from the journal; only the controller may persist files."""
    missing = []
    verified = getattr(engine, "_verified_projections", set())
    for report in reports.values():
        path = _investigation_path(output, report)
        if path is None:
            continue
        identity = (str(path.absolute()), str(report.get("revision")), str(report.get("generated_at")))
        if identity in verified:
            continue
        if not path.is_file():
            missing.append(report)
        elif len(verified) < 100_000:
            # The files are immutable; remember successful checks within this
            # worker lifetime, bounded independently of the report log length.
            verified.add(identity)
    recover_latest = None
    if latest is not None:
        try:
            published = json.loads((output / "latest.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            published = None
        if published != latest:
            recover_latest = latest
    return {"investigation": missing, "latest": recover_latest}

def _investigation_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    def evidence_text(item: Mapping[str, Any]) -> str:
        labels = ",".join(f"{key}={value}" for key, value in item.get("labels", {}).items())
        suffix = f":{labels}" if labels else ""
        return f"{item['signal']}={item['value']} ({item['observation_scope']}{suffix})"

    def quality_fields(value):
        value = (value or {}).get("current", {})
        return {"query_step_seconds": value.get("query_step_seconds"),
                "range_window_seconds": value.get("range_window_seconds"),
                "evaluation_count": value.get("evaluation_count"),
                "source_age_seconds": value.get("source_age_seconds"),
                "quality_warnings": ", ".join(value.get("warnings", []))}

    window = report.get("analysis_window", {})
    comparison = report.get("comparison", {})
    baseline = comparison.get("baseline_interval") or {}
    common = {
        "schema_version": 1, "run_id": report.get("run_id"), "node": report.get("node"),
        "diagnosis_method": report.get("diagnosis_method", "rule"), "generated_at": report.get("generated_at"),
        "workload_comparability": comparison.get("workload_comparability", "unverified"),
        "sandbox_device_mapping": (report.get("sandbox_device_mapping") or {}).get("status", "not_configured"),
        "data_origin": report.get("data_origin", "observed"),
        "step": report.get("step"), "record_id": report.get("trigger_record_id"),
        "boundary_scope": report.get("boundary_scope"), "execution_mode": report.get("execution_mode"),
        "worker_id": report.get("worker_id"),
        "observed_at": window.get("end"), "boundary_accuracy": window.get("accuracy"),
        "time_reference": alignment_metadata(window).get("reference_id"),
        "time_reference_session": alignment_metadata(window).get("reference_session"),
        "time_uncertainty_seconds": alignment_metadata(window).get("uncertainty_seconds"),
        "window_start_ms": math.floor(window["start"] * 1000) if finite(window.get("start")) is not None else None,
        "window_end_ms": math.ceil(window["end"] * 1000) if finite(window.get("end")) is not None else None,
        "baseline_record_id": comparison.get("baseline_record_id"),
        "baseline_start_ms": math.floor(baseline["start"] * 1000) if finite(baseline.get("start")) is not None else None,
        "baseline_end_ms": math.ceil(baseline["end"] * 1000) if finite(baseline.get("end")) is not None else None,
    }
    symptom = report.get("symptom", {})
    strong = [item for item in report.get("candidates", []) if item.get("state") == "strong_signal"]
    rows = [{**common, "row_kind": "summary", "verdict": report.get("verdict"),
             **project_clock_quality(report.get('clock_quality', {})),
             "step_duration_seconds": symptom.get("step_duration_seconds"),
             "candidate_count": len(report.get("candidates", [])),
             "strong_candidate_count": len(strong),
             "primary_candidate": strong[0].get("id") if strong else None,
             "missing_sources": ", ".join(report.get("missing_sources", [])),
             "run_model_identifier": report['run_model'].get('identifier') if isinstance(report.get('run_model'), Mapping) else None,
             "run_model_source": report['run_model'].get('source') if isinstance(report.get('run_model'), Mapping) else None,
             "storage_overview": json.dumps(report.get('storage_overview'), separators=(',', ':')),
             **({'async_decision': json.dumps(report['async_decision'], separators=(',', ':'))} if report.get('async_decision') else {}),
             **({'rollout_replicas': json.dumps(report['rollout_replicas'], separators=(',', ':'))}
                if report.get('rollout_replicas') is not None else {})}]
    for replica in report.get('rollout_replicas', []):
        for entity in replica.get('entities', []) or [{}]:
            rows.append({**common, 'row_kind': 'replica', 'replica_id': replica['id'],
                         'replica_status': replica['status'], 'replica_clock_status': replica['clock_status'],
                         'replica_nodes': replica['nodes'], 'replica_endpoint': replica['instance'],
                         'run_relation': 'configured', 'resource_attribution': 'not_established',
                         'replica_entity': entity.get('identity'), 'replica_signals': entity.get('signals', {}),
                         'replica_missing_sources': entity.get('missing_sources', [])})
    for candidate in report.get("candidates", []):
        rows.append({**common, "row_kind": "candidate", "candidate_id": candidate.get("id"),
                     "component": candidate.get("component"), "state": candidate.get("state"),
                     "summary": candidate.get("summary"),
                     "evidence_summary": "; ".join(evidence_text(item) for item in candidate.get("evidence", [])),
                     "counter_evidence_summary": ", ".join(item["signal"] for item in candidate.get("counter_evidence", [])),
                     "missing_evidence_summary": ", ".join(candidate.get("missing_evidence", [])),
                     "observation_scope": candidate.get("observation_scope")})
        rows[-1]['resource_attribution'] = candidate.get('resource_attribution', 'not_established')
        rows[-1]['run_relation'] = candidate.get('run_relation', 'not_reported')
        rows[-1]['signal_strength'] = candidate.get('signal_strength', candidate.get('state'))
        rows[-1]['context_status'] = candidate.get('context_status')
        rows[-1]['replica_id'] = candidate.get('replica_id')
        rows[-1]['serving_context'] = json.dumps(candidate.get('serving_context'), separators=(',', ':'))
        for kind, items in (("supporting", candidate.get("evidence", [])),
                            ("counter", candidate.get("counter_evidence", []))):
            for item in items:
                observation = Observation.decode({**item, 'current': item.get('value')})
                rows.append({**common, "row_kind": "evidence", "candidate_id": candidate.get("id"),
                             "evidence_type": kind, "signal": item.get("signal"),
                             "current": observation.value, "baseline": item.get("baseline"),
                             "observation_status": observation.status,
                             "observation_scope": item.get("observation_scope"),
                             "source": item.get("source"),
                             "unit": item.get("unit"), "window_statistic": item.get("window_statistic"),
                             "query": item.get("query"),
                             "entity": ",".join(f"{key}={value}" for key, value in item.get("labels", {}).items()),
                             "sampling_quality": json.dumps(item.get("sampling_quality"), separators=(",", ":")),
                             **quality_fields(item.get("sampling_quality"))})
        for name in candidate.get("missing_evidence", []):
            rows.append({**common, "row_kind": "evidence", "candidate_id": candidate.get("id"),
                         "evidence_type": "missing", "signal": name,
                         "current": None, "baseline": None, "observation_status": "unknown",
                         "observation_scope": candidate.get("observation_scope"),
                         "source": None, "entity": ""})
    for signal in comparison.get("signals", []):
        if signal.get("baseline") is None:
            continue
        observation = Observation.decode(signal)
        rows.append({**common, "row_kind": "comparison", "signal": signal["signal"],
                     "observation_status": observation.status,
                     "observation_scope": signal["scope"],
                     "entity": ",".join(f"{key}={value}" for key, value in signal.get("labels", {}).items()),
                     "current": signal["current"],
                     "baseline": signal["baseline"], "delta_percent": signal["delta_percent"],
                     "unit": signal.get("unit"), "window_statistic": signal.get("window_statistic"),
                     "query": signal.get("query"),
                     "comparison_status": signal.get('comparison_status', 'not_reported'),
                     "comparison_quality": json.dumps(signal.get('comparison_quality'), separators=(',', ':')),
                     "sampling_quality": json.dumps(signal.get("sampling_quality"), separators=(",", ":")),
                     **quality_fields(signal.get("sampling_quality"))})
    rows.extend(project_storage_series(report.get("storage_series", {}), common))
    rows.extend(project_storage_summary(report.get("storage_series", {}), common))
    return rows


def repair_projections(directory: Path, recovery: Mapping[str, Any]) -> None:
    """The controller applies a worker's read-only recovery plan."""
    for report in recovery.get("investigation", []):
        _write_investigation(directory, report, missing_only=True)
    if recovery.get("latest") is not None:
        atomic_write_text(directory / "latest.json",
                          json.dumps(recovery["latest"], indent=2, sort_keys=True) + "\n")
