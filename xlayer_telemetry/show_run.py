"""Print a serverless summary of one post-training or Agent RL run.

No collector or database is required. The command reads manifests, framework
summaries, the latest metric snapshot per worker, and recent correlation events
from the run's output directory.
"""

from __future__ import annotations

import argparse
import heapq
import json
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from .fileio import json_objects


def _load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _flatten(data: dict[str, Any]) -> list[str]:
    lines = []
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"  {key}: {{...{len(value)} fields}}")
        elif isinstance(value, list):
            lines.append(f"  {key}: [...{len(value)} items]")
        else:
            lines.append(f"  {key}: {value}")
    return lines


def _format_sample(sample: dict[str, Any]) -> str:
    name = sample.get("name")
    labels = sample.get("labels")
    if isinstance(labels, dict) and labels:
        dimensions = ",".join(
            f"{key}={value}" for key, value in sorted(labels.items())
        )
        name = f"{name}{{{dimensions}}}"
    return f"{name}={sample.get('value')}"


def _recent_events(paths: Iterable[Path], limit: int = 20) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    recent = []
    sequence = 0
    for path in paths:
        try:
            for record in json_objects(path):
                if record.get("schema_version") == 1:
                    timestamp = record.get("start_time_unix_nano", record.get("timestamp_unix_nano", 0))
                    if type(timestamp) is not int:
                        continue
                    sequence += 1
                    heapq.heappush(recent, (timestamp, sequence, record))
                    if len(recent) > limit:
                        heapq.heappop(recent)
        except OSError:
            continue
    return [record for _, _, record in sorted(recent)]


def summarize(output_dir: Path) -> str:
    lines = [f"Run: {output_dir}"]

    health = _load(output_dir / "telemetry-health.json")
    if health is not None:
        lines.append(f"\n[telemetry completeness] status={health.get('status')} workload_exit={health.get('workload', {}).get('exit_code')}")
        for name, item in health.get("sidecars", {}).items():
            lines.append(f"  {name}: {item.get('status')} last_success_at={item.get('last_success_at')}")
        lines.append(f"  final_export={health.get('final_export', {})}")
        if health.get("diagnostics"):
            lines.append(f"  diagnosis_coverage={health['diagnostics']}")
        lines.extend(f"  issue: {issue}" for issue in health.get("issues", []))

    manifest_path = output_dir / "telemetry-manifest.json"
    manifest = _load(manifest_path)
    if manifest is not None:
        lines.append("\n[telemetry-manifest.json]")
        lines.extend(_flatten(manifest))
        sources = manifest.get("sources")
        if isinstance(sources, dict) and sources:
            lines.append("\n[declared sources -- metadata only; endpoints were not queried]")
            for name, endpoint in sorted(sources.items()):
                if isinstance(endpoint, str):
                    try:
                        parts = urlsplit(endpoint)
                        endpoint = urlunsplit((parts.scheme, parts.netloc.rsplit('@', 1)[-1], parts.path, '', ''))
                    except ValueError:
                        endpoint = "(invalid endpoint; see manifest)"
                lines.append(f"  {name}: {endpoint}")

    metadata_files = sorted(
        p for p in output_dir.glob("run-metadata-*.json") if "-rank-" not in p.stem
    ) + sorted(output_dir.glob("summary-*.json"))
    if not metadata_files:
        lines.append("\n(no run-metadata-*.json or summary-*.json found in this directory)")
    for path in metadata_files:
        data = _load(path)
        if data is None:
            continue
        lines.append(f"\n[{path.name}]")
        lines.extend(_flatten(data))

    metrics_dir = output_dir / "telemetry-metrics"
    metrics_files = sorted(metrics_dir.glob("*.json")) if metrics_dir.is_dir() else []
    if not metrics_files:
        lines.append("\n(no telemetry-metrics snapshots found; check the logger/SDK producer and its output path)")
    for path in metrics_files:
        snapshot = _load(path)
        if (snapshot is None or snapshot.get("schema_version") != 2
                or not isinstance(snapshot.get("samples"), list)):
            continue
        metrics = " ".join(
            _format_sample(sample)
            for sample in snapshot.get("samples", [])
            if isinstance(sample, dict)
        )
        context = " ".join(
            f"{name}={snapshot[name]}"
            for name in ("node", "rank", "local_rank", "gpu")
            if snapshot.get(name) is not None
        )
        lines.append(
            f"\n[{snapshot.get('producer')}/{snapshot.get('role')} worker "
            f"{snapshot.get('worker_id')}{(' ' + context) if context else ''}] "
            f"step {snapshot.get('step')}: {metrics}"
        )

    events_dir = output_dir / "telemetry-events"
    event_files = sorted(path for path in events_dir.glob("*.jsonl") if not path.name.startswith("verl-steps")) if events_dir.is_dir() else []
    events = _recent_events(event_files)
    if events:
        lines.append("\n[recent telemetry events]")
    for event in events:
        duration = (
            f" duration={event.get('duration_seconds')}s"
            if event.get("duration_seconds") is not None
            else ""
        )
        lines.append(
            f"  step={event.get('step')} phase={event.get('phase')} "
            f"{event.get('role')}/{event.get('worker_id')} {event.get('name')}"
            f"{duration} status={event.get('status', 'event')} "
            f"trace_id={event.get('trace_id')}"
        )

    diagnosis = _load(output_dir / "diagnostics" / "latest.json")
    if diagnosis is not None and diagnosis.get("schema_version") == 1:
        lines.append(
            f"\n[latest bottleneck diagnosis] verdict={diagnosis.get('verdict')} "
            f"trigger={diagnosis.get('trigger')} step={diagnosis.get('step')} "
            f"scope={diagnosis.get('boundary_scope')}"
        )
        for finding in diagnosis.get("findings", []):
            if isinstance(finding, dict):
                lines.append(
                    f"  {finding.get('component')}: {finding.get('candidate')}"
                )
        if diagnosis.get("query_execution"):
            lines.append(f"  query_execution={diagnosis['query_execution']}")
        if diagnosis.get("analysis_execution"):
            lines.append(f"  analysis_execution={diagnosis['analysis_execution']}")
        if diagnosis.get("jsonl_cache"):
            lines.append(f"  jsonl_cache={diagnosis['jsonl_cache']}")
        comparison = diagnosis.get("comparison", {})
        if comparison.get("baseline_record_id"):
            lines.append(f"  baseline_record_id={comparison['baseline_record_id']}")
        for candidate in diagnosis.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            lines.append(
                f"  candidate {candidate.get('id')} [{candidate.get('state')}] "
                f"scope={candidate.get('observation_scope')}"
            )
            for item in candidate.get("evidence", []):
                lines.append(
                    f"    evidence {item.get('signal')}={item.get('value')} "
                    f"scope={item.get('observation_scope')} source={item.get('source')}"
                )
            if candidate.get("missing_evidence"):
                lines.append("    missing=" + ", ".join(candidate["missing_evidence"]))
        missing = diagnosis.get("missing_sources", [])
        if missing:
            lines.append(f"  missing_sources={len(missing)}")
            lines.extend(f"    {source}" for source in missing)
        if diagnosis.get("clock_quality"):
            lines.append(f"  clock_quality={diagnosis['clock_quality'].get('status', 'unknown')}")
        storage_series = diagnosis.get("storage_series", {})
        if storage_series.get("enabled"):
            lines.append("  storage_collection_series: shared reports, resolution=1s, collection_interval=unknown")
            for role in ("current", "baseline"):
                entry=storage_series.get(role, {})
                count=len(entry.get("distributions", []))+len(entry.get("counters", []))
                lines.append(f"    {role}: {count} points distributions={entry.get('distribution_status')} counters={entry.get('counter_status')} host_clock={entry.get('quality', {}).get('host_clock_coverage', 'unknown')}")
        lines.append("  full_report=diagnostics/latest.json")

    for pattern in ("artifacts/**/*.pt.trace.json", "artifacts/**/*.nsys-rep", "artifacts/nccl-baseline/manifest.env", "artifacts/nccl-baseline/all-reduce.log"):
        for path in sorted(output_dir.glob(pattern)):
            if path.is_file():
                lines.append(f"  investigation_artifact={path.relative_to(output_dir)}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path, help="a run's output-dir (same path the launcher used)")
    args = parser.parse_args()
    if not args.output_dir.is_dir():
        parser.error(f"not a directory: {args.output_dir}")
    print(summarize(args.output_dir))


if __name__ == "__main__":
    main()
