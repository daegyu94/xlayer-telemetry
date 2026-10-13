"""Pure assessment stages between bounded collection and artifact assembly."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .diagnosis_analysis import evaluate_rules, resource_run_relation
from .evidence_quality import RESOLUTION_BLOCKERS
from .metric_queries import SIGNAL_SOURCES, signal_metadata
from .observation_contracts import EntityRef, EvidenceBundle


def evaluate_stage(bundle: EvidenceBundle, current: Mapping[str, Any], baseline: Mapping[str, Any], *,
                   thresholds: Mapping[str, float], context: Mapping[str, Any],
                   enabled: bool) -> list[dict[str, Any]]:
    if not enabled or not bundle.timing_usable:
        return []
    return evaluate_rules(current, baseline, thresholds=thresholds, context=context)


def describe_signals(rows: list[dict[str, Any]], quality: Mapping[str, Any],
                     queries: Mapping[str, str]) -> list[dict[str, Any]]:
    """Attach measured quality and canonical vocabulary before decoding evidence."""
    result = deepcopy(rows)
    for row in result:
        name = SIGNAL_SOURCES.get(row['signal'], row['signal'])
        row['sampling_quality'] = quality.get(name)
        if row['signal'] in SIGNAL_SOURCES:
            row.update(signal_metadata(row['signal']))
            if name in queries or row['signal'] in queries:
                row['query'] = queries.get(name, queries.get(row['signal']))
    return result


def assess_comparisons(rows: list[dict[str, Any]], bundle: EvidenceBundle) -> list[dict[str, Any]]:
    """Raw values remain inspectable when sampling cannot support a delta."""
    result = deepcopy(rows)
    for row, current, baseline in zip(result, bundle.current, bundle.baseline):
        if any(RESOLUTION_BLOCKERS.intersection(value.sampling_quality.get('warnings', []))
               for value in (current, baseline)):
            row.update(delta=None, delta_percent=None, comparison_status='insufficient_sampling_coverage')
    return result


def assess_candidate_context(candidates: list[dict[str, Any]], *, config: Mapping[str, Any],
                             run_id: str, workload_comparability: str, missing: list[str],
                             tool_event: Any) -> list[dict[str, Any]]:
    """Resource relation and workload comparison constrain interpretation."""
    result = deepcopy(candidates)
    for candidate in result:
        evidence = candidate.get('evidence', [])
        resources = [item for item in evidence if item.get('observation_scope') != 'application']
        if any(str(item.get('source', '')).partition(':')[0] in {'prometheus', '3fs_clickhouse'}
               for item in resources):
            candidate['resource_attribution'] = 'not_established'
            candidate['run_relation'] = resource_run_relation(resources, config, run_id)
            candidate['missing_evidence'].append('run_resource_attribution_unverified')
        if any(item['signal'] in {
                'step_duration_seconds', 'rollout_duration_seconds', 'communication_duration_seconds',
                'actor_update_duration_seconds', 'critic_update_duration_seconds', 'checkpoint_duration_seconds',
        } and item.get('baseline') is not None for item in evidence) and workload_comparability != 'matched_configured_fields':
            candidate['missing_evidence'].append('workload_comparability_unverified')
            if candidate['state'] == 'strong_signal':
                candidate['state'] = 'supporting_signal'
        if candidate['id'] == 'gpu_memory_pressure' and 'gpu:memory_entity_match' in missing:
            candidate['missing_evidence'].append('gpu_memory_entity_match')
        if candidate['id'] == 'sandbox_local_storage_pressure' and tool_event is not None:
            candidate['missing_evidence'].append('tool_sandbox_resource_attribution')
        candidate['missing_evidence'] = list(dict.fromkeys(candidate['missing_evidence']))
    return result


def cap_candidate_quality(candidates: list[dict[str, Any]], bundle: EvidenceBundle,
                          quality: Mapping[str, Any], queries: Mapping[str, str]) -> list[dict[str, Any]]:
    """Monotonic, idempotent sampling cap shared with LLM evidence restrictions."""
    if not bundle.timing_usable:
        return []
    result = deepcopy(candidates)
    for candidate in result:
        for item in candidate.get('evidence', []) + candidate.get('counter_evidence', []):
            name = SIGNAL_SOURCES.get(item['signal'], item['signal'])
            entity = EntityRef.decode(item.get('observation_scope', 'unknown'), item.get('labels', {}))
            matched = any(observation.signal == item['signal'] and observation.entity.matches(entity)
                          for observation in bundle.current)
            item['sampling_quality'] = quality.get(name) if matched else None
            limits = bundle.limits(item['signal'], entity)
            if not matched and quality.get(name) is not None:
                item['sampling_quality'] = {'current': {'warnings': ['selected_entity_evaluation_count_unknown']}}
                limits = (f"current:{item['signal']}:selected_entity_evaluation_count_unknown",)
            candidate['missing_evidence'].extend(limits)
            if limits and candidate['state'] == 'strong_signal':
                candidate['state'] = 'supporting_signal'
            if item['signal'] in SIGNAL_SOURCES:
                item.update(signal_metadata(item['signal']))
                if name in queries or item['signal'] in queries:
                    item['query'] = queries.get(name, queries.get(item['signal']))
        candidate['missing_evidence'] = list(dict.fromkeys(candidate['missing_evidence']))
        candidate['signal_strength'] = candidate['state']
    return result
