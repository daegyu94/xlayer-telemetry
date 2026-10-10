"""Opt-in bounded summaries of existing SDK events and scoped observations.

This research API does not start collectors, query backends or load profilers.
Durations are sums of observed spans, never inferred critical-path occupancy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import statistics
from typing import Any, Iterable, Mapping

from ..events import CorrelationContext
from ..measurements import finite_number


ACCURACIES = {"exact", "calibrated", "approximate", "calibrated_approximate", "sampled", "unknown", "clock_discontinuity"}
STATUSES = {"observed", "not_configured", "query_failed", "no_data", "stale", "unknown"}
ENTITY_FIELDS = {"node", "worker_id", "rank", "gpu", "device", "engine", "interface", "service", "runtime"}


def _text(value: Any, name: str, limit: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"invalid {name}")
    return value


@dataclass(frozen=True)
class Boundary:
    context: CorrelationContext
    step: int
    sequence: int
    scope: str = "rl_step"
    phase: str = "rl_step"
    duration_seconds: float | None = None
    accuracy: str = "unknown"
    rollout_id: str | None = None
    workload: Mapping[str, Any] = field(default_factory=dict)
    time_reference: str | None = None
    time_uncertainty_seconds: float | None = None

    def __post_init__(self):
        if self.scope not in {"rl_step", "trainer_update", "rollout"} or self.accuracy not in ACCURACIES:
            raise ValueError("invalid boundary scope or accuracy")
        if any(type(value) is not int or value < 0 for value in (self.step, self.sequence)):
            raise ValueError("step and sequence must be nonnegative integers")
        _text(self.phase, "phase")
        if self.scope == "rollout":
            _text(self.rollout_id, "rollout_id")
        if self.duration_seconds is not None and (finite_number(self.duration_seconds) is None or self.duration_seconds < 0):
            raise ValueError("duration must be finite and nonnegative")
        if len(self.workload) > 16:
            raise ValueError("at most 16 workload dimensions")
        for key, value in self.workload.items():
            _text(key, "workload key")
            if not isinstance(value, (str, int, float, bool)) or isinstance(value, str) and len(value) > 128:
                raise ValueError("workload values must be compact scalars")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and finite_number(value) is None:
                raise ValueError("nonfinite workload value")
        if self.time_reference is not None:
            _text(self.time_reference, "time_reference")
        if self.time_uncertainty_seconds is not None and (finite_number(self.time_uncertainty_seconds) is None or self.time_uncertainty_seconds < 0):
            raise ValueError("invalid clock uncertainty")
        if self.accuracy.startswith("calibrated") and (self.time_reference is None or self.time_uncertainty_seconds is None):
            raise ValueError("calibrated boundary needs reference and uncertainty")

    @classmethod
    def from_step(cls, record: Mapping[str, Any], context: CorrelationContext) -> Boundary:
        """Adapt StepHistoryWriter without turning replay ingestion into time."""
        if any(record.get(key) != getattr(context, key) for key in ("run_id", "node", "worker_id")):
            raise ValueError("step observation identity differs from context")
        window = record.get("analysis_window", {})
        alignment = window.get("time_alignment", {})
        return cls(context=context, step=record["step"], sequence=record["step"],
                   scope=record["boundary_scope"], duration_seconds=record.get("step_duration_seconds"),
                   accuracy=window.get("accuracy", "unknown"), workload=record.get("workload", {}),
                   time_reference=record.get("time_reference", alignment.get("reference_id")),
                   time_uncertainty_seconds=record.get("time_uncertainty_seconds", alignment.get("uncertainty_seconds")))

    def as_dict(self) -> dict:
        return {"context": self.context.as_dict(), "step": self.step, "sequence": self.sequence,
                "scope": self.scope, "phase": self.phase, "duration_seconds": self.duration_seconds,
                "accuracy": self.accuracy, "rollout_id": self.rollout_id, "workload": dict(self.workload),
                "time_reference": self.time_reference, "time_uncertainty_seconds": self.time_uncertainty_seconds}


def _belongs(record: Mapping[str, Any], boundary: Boundary) -> bool:
    if record.get("step") != boundary.step:
        return False
    if any(record.get(key) != value for key, value in boundary.context.as_dict().items()):
        return False
    attributes = record.get("attributes", {})
    if not isinstance(attributes, Mapping):
        return False
    if boundary.scope == "rollout":
        return attributes.get("rollout_id") == boundary.rollout_id
    # Async trainer updates do not automatically contain rollouts/tool work.
    return attributes.get("boundary_scope", "rl_step") == boundary.scope


def _summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean": None, "max": None, "sum": None}
    return {"count": len(values), "mean": statistics.fmean(values), "max": max(values), "sum": sum(values)}


def _event_entity(record: Mapping) -> dict:
    # Stable instrumented identities survive grouping; request/trace IDs do not.
    attributes = record.get("attributes", {})
    entity={key: attributes[key] for key in sorted(ENTITY_FIELDS) if key in attributes
            and (isinstance(attributes[key], str) and 0 < len(attributes[key]) <= 128
                 or key == "rank" and type(attributes[key]) is int and attributes[key] >= 0)}
    if 'node' not in attributes and isinstance(record.get('node'),str) and record['node']:
        entity['node']=record['node']
    return dict(sorted(entity.items()))


def summarize(boundary: Boundary, events: Iterable[Mapping[str, Any]],
              observations: Iterable[Mapping[str, Any]] = (), *, max_records: int = 4096,
              max_groups: int = 32, max_observations: int = 64) -> dict:
    """Consume bounded records; preserve each metric entity and missing status.

    Observations must already belong to this boundary's query window. This API
    does not infer that membership from timestamps or resample/rate counters.
    """
    for value, ceiling in ((max_records, 16384), (max_groups, 64), (max_observations, 128)):
        if type(value) is not int or not 1 <= value <= ceiling:
            raise ValueError("invalid signature budget")
    groups: dict[tuple, dict] = {}
    spans: dict[tuple[str, str], Mapping] = {}
    time_provenance: dict[str, Any] = {"original_start_ns_min": None, "original_end_ns_max": None,
                                     "correlation_start_ns_min": None, "correlation_end_ns_max": None,
                                     "references": [], "max_uncertainty_seconds": None}
    quality = {"scanned_events": 0, "accepted_events": 0, "excluded_events": 0,
               "invalid_events": 0, "duplicate_spans": 0, "dropped_groups": 0,
               "events_truncated": False, "observations_truncated": False,
               "unresolved_parent_edges": 0, "invalid_parent_edges": 0}
    accuracies: dict[str, int] = {}
    for index, record in enumerate(events):
        if index == max_records:
            quality["events_truncated"] = True
            break
        quality["scanned_events"] += 1
        if not isinstance(record, Mapping) or not _belongs(record, boundary):
            quality["excluded_events"] += 1
            continue
        name, phase = record.get("name"), record.get("phase")
        if (not isinstance(name, str) or not 0 < len(name) <= 128
                or not isinstance(phase, str) or not 0 < len(phase) <= 128
                or record.get("record_type") not in {"event", "span"}):
            quality["invalid_events"] += 1
            continue
        entity = _event_entity(record)
        key = (phase, name, tuple(entity.items()))
        if key not in groups and len(groups) >= max_groups:
            quality["dropped_groups"] += 1
            continue
        span_key = (record.get("trace_id"), record.get("span_id"))
        is_span = record["record_type"] == "span"
        valid_id = all(isinstance(value, str) and 0 < len(value) <= 128 for value in span_key)
        if is_span and valid_id:
            if span_key in spans:
                quality["duplicate_spans"] += 1
                continue
            spans[span_key] = record
        group = groups.setdefault(key, {"phase": phase, "name": name, "entity": entity, "count": 0, "span_count": 0,
                                        "errors": 0, "durations": [], "accuracy_counts": {}, "duration_accuracy_counts": {}})
        group["count"] += 1
        group["span_count"] += int(is_span)
        group["errors"] += int(record.get("status") == "error")
        accuracy = record.get("boundary_accuracy", "unknown")
        accuracy = accuracy if accuracy in ACCURACIES else "unknown"
        duration = finite_number(record.get("duration_seconds"))
        if is_span and duration is not None and duration >= 0:
            group["durations"].append(duration)
            counts = group["duration_accuracy_counts"]
            counts[accuracy] = counts.get(accuracy, 0) + 1
        accuracies[accuracy] = accuracies.get(accuracy, 0) + 1
        group["accuracy_counts"][accuracy] = group["accuracy_counts"].get(accuracy, 0) + 1
        quality["accepted_events"] += 1
        for source, target, operation in (("start_time_unix_nano", "original_start_ns_min", min),
                                          ("end_time_unix_nano", "original_end_ns_max", max),
                                          ("correlation_start_time_unix_nano", "correlation_start_ns_min", min),
                                          ("correlation_end_time_unix_nano", "correlation_end_ns_max", max)):
            value = record.get(source)
            if type(value) is int and value >= 0:
                before = time_provenance[target]
                time_provenance[target] = value if before is None else operation(before, value)
        reference = record.get("time_reference")
        if isinstance(reference, str) and len(reference) <= 128 and reference not in time_provenance["references"]:
            if len(time_provenance["references"]) < 8:
                time_provenance["references"].append(reference)
            else:
                quality["events_truncated"] = True
        uncertainty = finite_number(record.get("time_uncertainty_seconds"))
        if uncertainty is not None and uncertainty >= 0:
            before = time_provenance["max_uncertainty_seconds"]
            time_provenance["max_uncertainty_seconds"] = uncertainty if before is None else max(before, uncertainty)
    # Memoize functional parent chains so deep valid traces remain O(records).
    valid_chains: dict[tuple[str, str], bool] = {}
    for key in spans:
        cursor, path, visited = key, [], set()
        while cursor in spans and cursor not in valid_chains and cursor not in visited:
            visited.add(cursor)
            path.append(cursor)
            parent_id = spans[cursor].get("parent_span_id")
            cursor = (cursor[0], parent_id) if isinstance(parent_id, str) else (cursor[0], None)
        valid = cursor not in visited and valid_chains.get(cursor, True)
        for item in path:
            valid_chains[item] = valid
    relations: dict[tuple, dict] = {}
    for (trace_id, span_id), child in spans.items():
        parent_id = child.get("parent_span_id")
        if parent_id is None:
            continue
        if not isinstance(parent_id, str) or not 0 < len(parent_id) <= 128:
            quality["invalid_parent_edges"] += 1
            continue
        parent = spans.get((trace_id, parent_id))
        if parent is None:
            quality["unresolved_parent_edges"] += 1
            continue
        # Clocks never manufacture or validate instrumented edges.
        if not valid_chains[(trace_id, span_id)]:
            quality["invalid_parent_edges"] += 1
        else:
            parent_entity, child_entity = _event_entity(parent), _event_entity(child)
            key = (parent["phase"], parent["name"], child["phase"], child["name"],
                   tuple(parent_entity.items()), tuple(child_entity.items()))
            if key not in relations and len(relations) >= max_groups:
                quality["dropped_groups"] += 1
                continue
            row = relations.setdefault(key, {"parent_phase": key[0], "parent_name": key[1],
                                            "child_phase": key[2], "child_name": key[3], "count": 0,
                                            "parent_entity": parent_entity, "child_entity": child_entity,
                                            "child_duration_sum": 0.0, "duration_count": 0})
            row["count"] += 1
            duration = finite_number(child.get("duration_seconds"))
            if duration is not None and duration >= 0:
                row["child_duration_sum"] += duration
                row["duration_count"] += 1
    metrics = []
    for index, observation in enumerate(observations):
        if index == max_observations:
            quality["observations_truncated"] = True
            break
        signal = _text(observation.get("signal"), "signal")
        scope = _text(observation.get("scope"), "scope", 64)
        source = _text(observation.get("source"), "source")
        unit = observation.get("unit")
        if unit is not None:
            _text(unit, "unit", 64)
        entity = observation.get("entity", {})
        if not isinstance(entity, Mapping) or set(entity) - ENTITY_FIELDS:
            raise ValueError("unsupported metric entity")
        for name, value in entity.items():
            if type(value) is int and name == "rank" and value >= 0:
                continue
            _text(value, "entity value")
        status = observation.get("status", "unknown")
        accuracy = observation.get("accuracy", "sampled")
        if status not in STATUSES or accuracy not in ACCURACIES:
            raise ValueError("invalid observation quality")
        value = finite_number(observation.get("value")) if status == "observed" else None
        if status == "observed" and value is None:
            status = "unknown"
        metrics.append({"signal": signal, "scope": scope, "entity": dict(entity), "source": source,
                        "value": value, "unit": unit, "status": status, "accuracy": accuracy})
    return {"schema_version": 1, "record_type": "behavior_signature", "boundary": boundary.as_dict(),
            "events": [{**{key: value for key, value in row.items() if key != "durations"},
                        "duration_seconds": _summary(row["durations"])} for row in groups.values()],
            "relations": list(relations.values()), "observations": metrics,
            "quality": {**quality, "event_accuracy_counts": accuracies}, "time_provenance": time_provenance,
            "interpretation": "observed span sums; correlation only; not critical path or ownership"}


def _compatible(current: Mapping, other: Mapping, *, peer: bool) -> bool:
    a, b = current["boundary"], other["boundary"]
    same_context = (all(a["context"].get(key) == b["context"].get(key)
                        for key in ("run_id", "producer", "role", "policy_version")) if peer
                    else a["context"] == b["context"])
    return (same_context
            and a["scope"] == b["scope"] and a["phase"] == b["phase"]
            and a["workload"] == b["workload"] and bool(a["workload"])
            and (b["step"] == a["step"] and b["context"] != a["context"] if peer
                 else b["sequence"] < a["sequence"]))


def _incomplete(signature: Mapping) -> bool:
    return any(signature["quality"].get(key) for key in ("events_truncated", "observations_truncated", "dropped_groups"))


def _duration_accuracy(row: Mapping) -> dict:
    # Earlier artifacts only counted all events together. Point-event accuracy
    # cannot certify the spans contributing to a duration; keep it unknown.
    return row.get("duration_accuracy_counts", {"unknown": row["duration_seconds"]["count"]})


def _precise_duration(counts: Mapping) -> bool:
    return bool(counts) and all(key in {"exact", "calibrated"} and type(value) is int and value > 0
                                for key, value in counts.items())


def compare(current: Mapping, references: Iterable[Mapping], *, peer: bool = False,
            max_references: int = 32, slowdown_ratio: float = 1.5) -> dict:
    """Compare bounded exact-workload references without entity substitution."""
    if type(max_references) is not int or not 1 <= max_references <= 128:
        raise ValueError("invalid reference budget")
    if finite_number(slowdown_ratio) is None or slowdown_ratio <= 1:
        raise ValueError("slowdown ratio must exceed 1")
    eligible, truncated, seen, rejected_incomplete = [], False, set(), 0
    for index, item in enumerate(references):
        if index == max_references:
            truncated = True
            break
        if _compatible(current, item, peer=peer):
            if _incomplete(item):
                rejected_incomplete += 1
                continue
            boundary = item["boundary"]
            identity = (*sorted(boundary["context"].items()), boundary["scope"], boundary["phase"],
                        boundary["sequence"], boundary["step"], boundary.get("rollout_id"))
            if identity not in seen:
                seen.add(identity)
                eligible.append(item)
    duration = current["boundary"]["duration_seconds"]
    previous = [item["boundary"]["duration_seconds"] for item in eligible
                if finite_number(item["boundary"]["duration_seconds"]) is not None]
    baseline = statistics.median(previous) if previous else None
    ratio = duration / baseline if duration is not None and baseline is not None and baseline > 0 else None
    groups = []
    for row in current["events"]:
        matches = [other for item in eligible for other in item["events"]
                   if (other["phase"], other["name"], other["entity"]) == (row["phase"], row["name"], row["entity"])
                   and other["duration_seconds"]["mean"] is not None]
        values = [other["duration_seconds"]["mean"] for other in matches]
        baseline_accuracy: dict[str, int] = {}
        for other in matches:
            for accuracy, count in _duration_accuracy(other).items():
                baseline_accuracy[accuracy] = baseline_accuracy.get(accuracy, 0) + count
        before = statistics.median(values) if values else None
        now = row["duration_seconds"]["mean"]
        groups.append({"phase": row["phase"], "name": row["name"], "current_mean": now,
                       "entity": row["entity"], "accuracy_counts": row["accuracy_counts"],
                       "duration_accuracy_counts": _duration_accuracy(row),
                       "baseline_duration_accuracy_counts": baseline_accuracy,
                       "baseline_mean": before, "delta": now-before if now is not None and before is not None else None})
    relations = []
    for row in current["relations"]:
        key = tuple(row[field] for field in ("parent_phase", "parent_name", "child_phase", "child_name"))
        def per_parent(signature, relation):
            parent_count = sum(group["span_count"] for group in signature["events"]
                               if (group["phase"], group["name"]) == key[:2] and group["entity"] == relation["parent_entity"])
            return (relation["count"] / parent_count,
                    relation["child_duration_sum"] / parent_count if relation["duration_count"] == relation["count"] else None)
        now_count, now_time = per_parent(current, row)
        before = [per_parent(item, other) for item in eligible for other in item["relations"]
                  if tuple(other[field] for field in ("parent_phase", "parent_name", "child_phase", "child_name")) == key
                  and other["parent_entity"] == row["parent_entity"] and other["child_entity"] == row["child_entity"]]
        count = statistics.median(value[0] for value in before) if before else None
        times = [value[1] for value in before if value[1] is not None]
        seconds = statistics.median(times) if times else None
        relations.append({"parent_phase": key[0], "parent_name": key[1], "child_phase": key[2], "child_name": key[3],
                          "parent_entity": row["parent_entity"], "child_entity": row["child_entity"],
                          "children_per_parent": now_count, "baseline_children_per_parent": count,
                          "count_delta": now_count-count if count is not None else None,
                          "child_seconds_per_parent": now_time, "baseline_child_seconds_per_parent": seconds,
                          "duration_delta": now_time-seconds if now_time is not None and seconds is not None else None})
    metric_deltas = []
    for row in current["observations"]:
        values = [other["value"] for item in eligible for other in item["observations"]
                  if all(other[field] == row[field] for field in ("signal", "scope", "entity", "source", "unit"))
                  and other["status"] == "observed" and other["value"] is not None]
        before = statistics.median(values) if values else None
        metric_deltas.append({**row, "baseline": before,
                              "delta": row["value"]-before if row["value"] is not None and before is not None else None})
    return {"comparison": "peer" if peer else "history", "reference_count": len(eligible),
            "rejected_incomplete_references": rejected_incomplete,
            "references_truncated": truncated, "baseline_duration_seconds": baseline,
            "duration_ratio": ratio, "slow": ratio >= slowdown_ratio if ratio is not None else None,
            "events": groups, "relations": relations, "observations": metric_deltas,
            "missing_evidence": [] if ratio is not None else ["comparable_nonzero_duration_baseline"]}


# Conservative investigation candidates, not an attribution or cause classifier.
_CANDIDATES = {
    "cpu_contention": ("host_cpu_pressure_ratio", "node", .2, "cpu"),
    "gpu_pressure": ("gpu_utilization_percent", "device", 90.0, "compute"),
    "network_pressure": ("network_utilization_ratio", "network-interface", .8, "communication"),
    "storage_pressure": ("disk_busy_ratio", "device", .9, "storage"),
}


def candidates(signature: Mapping, comparison: Mapping) -> list[dict]:
    rows = []
    context = signature["boundary"]["context"]
    for name, (signal, scope, threshold, phase) in _CANDIDATES.items():
        supporting, against, missing = [], [], []
        expected_unit = "percent" if signal == "gpu_utilization_percent" else "ratio"
        metric_rows = [row for row in signature["observations"] if row["signal"] == signal and row["scope"] == scope
                       and row["entity"].get("node") == context["node"]
                       and (signal != "gpu_utilization_percent" or context.get("gpu") is not None and row["entity"].get("gpu") == context["gpu"])]
        # Keep every entity separate. A hot different device/engine is not evidence.
        for row in metric_rows:
            if row["unit"] != expected_unit:
                missing.append({"signal": signal, "entity": row["entity"], "reason": "unit_mismatch_or_unknown"})
            elif row["status"] != "observed" or row["accuracy"] in {"unknown", "clock_discontinuity"}:
                missing.append({"signal": signal, "entity": row["entity"], "reason": row["status"] if row["status"] != "observed" else row["accuracy"]})
            else:
                (supporting if row["value"] >= threshold else against).append({**row, "threshold": threshold})
        if not metric_rows:
            missing.append({"signal": signal, "reason": "missing_matching_entity"})
        event_rows = [row for row in comparison["events"] if row["phase"] == phase]
        event_observed, duration_observed = False, False
        for row in event_rows:
            if row["baseline_mean"] is None or row["baseline_mean"] <= 0 or row["current_mean"] is None:
                continue
            duration_observed = True
            precise = (_precise_duration(row.get("duration_accuracy_counts", {}))
                       and _precise_duration(row.get("baseline_duration_accuracy_counts", {})))
            event_observed |= precise
            slow = row["current_mean"] >= 1.5 * row["baseline_mean"]
            (supporting if slow else against).append({"event": row["name"], "phase": phase,
                                                     "current": row["current_mean"], "baseline": row["baseline_mean"],
                                                     "duration_accuracy_counts": row.get("duration_accuracy_counts", {}),
                                                     "baseline_duration_accuracy_counts": row.get("baseline_duration_accuracy_counts", {})})
            if slow and not precise:
                missing.append({"event": row["name"], "phase": phase, "reason": "span_time_unknown_or_approximate"})
        if not duration_observed:
            missing.append({"phase": phase, "reason": "missing_comparable_span_duration"})
        if signature["boundary"]["accuracy"] in {"unknown", "clock_discontinuity"}:
            missing.append({"reason": "boundary_time_unknown_for_resource_correlation"})
        resource_support = any(item.get("signal") == signal for item in supporting)
        phase_support = any(item.get("phase") == phase for item in supporting)
        hot_metrics = [row for row in metric_rows if row["status"] == "observed" and row["accuracy"] not in {"unknown", "clock_discontinuity"}
                       and row["unit"] == expected_unit and row["value"] is not None and row["value"] >= threshold]
        slow_events = [row for row in event_rows if row["baseline_mean"] is not None and row["baseline_mean"] > 0
                       and row["current_mean"] is not None and row["current_mean"] >= 1.5 * row["baseline_mean"]
                       and _precise_duration(row.get("duration_accuracy_counts", {}))
                       and _precise_duration(row.get("baseline_duration_accuracy_counts", {}))]
        paired_entity = any(event['entity'].get('node')==metric['entity'].get('node') and all(event["entity"].get(key, context.get(key)) == value
                                   for key, value in metric["entity"].items() if key in {"gpu", "engine", "device", "interface"})
                            for metric in hot_metrics for event in slow_events)
        if resource_support and phase_support and not paired_entity:
            missing.append({"reason": "missing_matching_instrumented_resource_identity"})
        if _incomplete(signature):
            missing.append({"reason": "signature_budget_exhausted"})
        rows.append({"candidate": name, "state": "supported_candidate" if resource_support and phase_support and not missing else "supporting_signal" if supporting else "missing_evidence" if missing else "against",
                     "supporting": supporting, "against": against, "missing": missing,
                     "coverage": {"required_sources": 2, "observed_sources": int(any(row["status"] == "observed" and row["unit"] == expected_unit and row["accuracy"] not in {"unknown", "clock_discontinuity"} for row in metric_rows)) + int(event_observed)},
                     "confidence_kind": "source_quality", "causality": "not_established"})
    return rows


def encode(signature: Mapping) -> str:
    return json.dumps(signature, separators=(",", ":"), sort_keys=True, allow_nan=False) + "\n"
