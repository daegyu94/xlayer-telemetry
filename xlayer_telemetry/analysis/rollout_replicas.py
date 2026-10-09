"""Declared rollout placements and per-engine evidence, not request ownership."""
from __future__ import annotations

import re

from ..prometheus import escape_label
from ..time_alignment import validate_alignment, alignment_metadata
from .evidence_quality import quality, source_for_entity, correlation_quality_issues, INVALID_SOURCE_TIME
from .rollout_state import comparison_issues, apply_serving_limits


def validate_rollout_replicas(config):
    replicas = config.get('rollout_replicas', [])
    text = lambda value: isinstance(value, str) and 0 < len(value) <= 256 and value.strip() == value
    if not isinstance(replicas, list) or len(replicas) > 16:
        raise ValueError('rollout_replicas needs at most 16 declared replicas')
    ids, instances = set(), set()
    for row in replicas:
        if (not isinstance(row, dict) or not {'id', 'instance', 'endpoint_node', 'nodes'} <= set(row)
                or set(row) - {'id', 'instance', 'endpoint_node', 'nodes', 'server_id'}
                or ('server_id' in row and not text(row['server_id']))
                or not all(text(row.get(key)) for key in ('id', 'instance', 'endpoint_node'))
                or not isinstance(row['nodes'], list) or not 1 <= len(row['nodes']) <= 8
                or any(not text(node) for node in row['nodes']) or len(set(row['nodes'])) != len(row['nodes'])
                or row['endpoint_node'] not in row['nodes'] or row['id'] in ids or row['instance'] in instances):
            raise ValueError('rollout_replicas requires unique id/instance and an endpoint_node in nodes')
        ids.add(row['id']); instances.add(row['instance'])
    if replicas and (not text(config.get('run_id')) or not text(config.get('cluster'))
                     or config.get('run_engine_instances')):
        raise ValueError('rollout_replicas requires run_id/cluster and cannot mix run_engine_instances')
    if len({node for row in replicas for node in row['nodes']}) > 28:
        raise ValueError('rollout_replicas exceeds the clock inventory node budget')


def scope_queries(queries, config):
    """One range query per metric across declared endpoints; no replica fan-out."""
    replicas = config.get('rollout_replicas', [])
    if not replicas:
        return queries
    regex = lambda values: escape_label('(' + '|'.join(re.escape(value).replace(r'\-', '-') for value in sorted(set(values))) + ')')
    nodes = regex(row['endpoint_node'] for row in replicas)
    endpoints = regex(row['instance'] for row in replicas)
    return {name: expression.replace('node="{rollout_node}"', 'node=~"' + nodes + '",instance=~"' + endpoints + '"')
            if name.startswith(('vllm_', 'mooncake_connector_')) else expression
            for name, expression in queries.items()}


def matches_replica(labels, replica, cluster):
    return (labels.get('cluster') == cluster and labels.get('node') == replica['endpoint_node']
            and labels.get('instance') == replica['instance'])


def observations(config, current_series, baseline_series, sources, queries, sampling,
                 window, baseline_window, clocks, thresholds, serving=None):
    # Local import avoids a dependency cycle with the existing rule catalogue.
    from .diagnosis_analysis import vllm_identity, evaluate_rules, compare_signals
    from .metric_queries import PROFILE_SIGNALS
    result = []
    base_nodes = {config.get('node'), config.get('clock', {}).get('monitoring_node')}
    clock_config = config.get('clock', {})
    def clock_status(quality_value, interval, replica):
        required = (set(replica['nodes']) | base_nodes) - {None, ''}
        nodes = quality_value.get('system_clock_screening', quality_value).get('nodes', {})
        aligned = (bool(clock_config.get('monitoring_node')) and
                   all(nodes.get(node, {}).get('status') == 'aligned' for node in required))
        if 'time_alignment' in interval or clock_config.get('calibration_reference'):
            alignment = interval.get('time_alignment')
            aligned = aligned and isinstance(alignment, dict) and alignment.get('node') == config.get('node') and validate_alignment(
                interval, clock_config.get('calibration_reference'),
                max_uncertainty=min(float(clock_config.get('max_skew_seconds', 1)), (interval['end']-interval['start'])/10))['status'] == 'aligned'
        return 'aligned' if aligned else 'unknown'

    names = [name for name in queries if name.startswith(('vllm_', 'mooncake_connector_'))]
    expressions = {('vllm_preemptions_delta' if name == 'vllm_preemptions_total' else name): queries[name] for name in names}
    metadata = {'vllm_requests_waiting': {'unit': 'requests', 'window_statistic': 'max'},
                'vllm_kv_cache_usage': {'unit': 'ratio', 'window_statistic': 'max'},
                'vllm_preemptions_delta': {'unit': 'count', 'window_statistic': 'max_series_delta'}}
    metadata.update({name: {'unit': spec.unit, 'window_statistic': spec.statistic} for name, spec in PROFILE_SIGNALS.items()})
    for replica in config.get('rollout_replicas', []):
        population = {vllm_identity(item) for name in names for item in current_series.get(name, [])
                      if matches_replica(item.get('labels', {}), replica, config['cluster'])}
        if len(population) > 8:
            result.append({**replica, 'status': 'identity_limit', 'clock_status': 'unknown',
                'baseline_clock_status': 'unknown', 'run_relation': 'configured', 'resource_attribution': 'not_established',
                'entities': [], 'signals': {}, 'candidates': [], 'missing_sources': ['more_than_eight_engine_identities']})
            continue
        current_clock = clock_status(clocks, window, replica)
        prior_clock = clock_status(clocks.get('baseline', {}), baseline_window, replica) if baseline_window else 'unavailable'
        if baseline_window and alignment_metadata(window).get('reference_session') != alignment_metadata(baseline_window).get('reference_session'):
            prior_clock = 'unknown'
        entities = []
        contexts = {'current': (serving or {}).get('current', {}).get(replica['id'], {}),
                    'baseline': (serving or {}).get('baseline', {}).get(replica['id'], {})}
        context_issues = comparison_issues(contexts['current'], contexts['baseline']) if baseline_window else []
        for identity in sorted(population):
            values, before, labels, qualities, missing = {}, {}, {}, {}, []
            for name in names:
                signal = 'vllm_preemptions_delta' if name == 'vllm_preemptions_total' else name
                field = 'max_series_delta' if name == 'vllm_preemptions_total' else PROFILE_SIGNALS[name].statistic if name in PROFILE_SIGNALS else 'max'
                for role, series, interval, target in (('current', current_series, window, values),
                                                      ('baseline', baseline_series, baseline_window, before)):
                    if not interval:
                        continue
                    rows = [row for row in series.get(name, []) if vllm_identity(row) == identity]
                    if len(rows) != 1:
                        missing.append(role + ':' + signal + ':missing_or_duplicate')
                        continue
                    row = rows[0]; value = row.get('stats', {}).get(field)
                    if value is None:
                        missing.append(role + ':' + signal + ':missing_value')
                        continue
                    target[signal] = value / 100 if name == 'vllm_kv_cache_usage' and value > 1 else value
                    labels[signal] = dict(identity)
                    source = source_for_entity(sources.get(name, {}).get(role, {}), dict(identity))
                    qualities.setdefault(signal, {})[role] = quality(queries[name], interval['start'], interval['end'],
                        max(1, float(config['prometheus'].get('query_step_seconds', 2))), row['stats'], source=source,
                        result=sampling.get(name, {}).get(role, {}).get('query_result'))
            rule_values, rule_before = dict(values), dict(before)
            missing.extend(context_issues)
            if context_issues:
                rule_before.clear()
            for signal, pair in qualities.items():
                for role, target in (('current', rule_values), ('baseline', rule_before)):
                    issues = correlation_quality_issues(pair.get(role, {}))
                    missing.extend(role + ':' + signal + ':' + issue for issue in issues)
                    if INVALID_SOURCE_TIME.intersection(issues):
                        target.pop(signal, None)
            if current_clock != 'aligned':
                missing.append('clock:current:unverified')
            if prior_clock not in {'aligned', 'unavailable'}:
                rule_before.clear(); missing.append('clock:baseline:unverified')
            # Duration growth is a trainer-window symptom, not evidence that
            # this replica generated the consumed samples (especially async).
            candidates = evaluate_rules(rule_values, rule_before, thresholds=thresholds,
                context={'signal_labels': labels, 'window': window, 'boundary_accuracy': window.get('accuracy'),
                         'sources': {name: 'prometheus' for name in values}, 'queries': expressions}) if current_clock == 'aligned' else []
            candidates = [candidate for candidate in candidates if candidate['component'] == 'rollout']
            for candidate in candidates:
                candidate.update(resource_attribution='not_established', run_relation='configured',
                                 replica_id=replica['id'])
                candidate['missing_evidence'].append('run_resource_attribution_unverified')
                for item in candidate['evidence'] + candidate['counter_evidence']:
                    item['sampling_quality'] = qualities.get(item['signal'])
                    item.update(metadata.get(item['signal'], {}))
                    issues = [role + ':' + item['signal'] + ':' + issue for role, q in (item['sampling_quality'] or {}).items()
                              for issue in correlation_quality_issues(q)]
                    candidate['missing_evidence'].extend(issues)
                    if issues and candidate['state'] == 'strong_signal':
                        candidate['state'] = 'supporting_signal'
                candidate['signal_strength'] = candidate['state']
            if serving:
                apply_serving_limits(candidates, [replica], serving.get('current', {}))
            signals = {row['signal']: {**row, 'sampling_quality': qualities.get(row['signal'])}
                       for row in compare_signals(values, before, labels=labels)}
            for name, row in signals.items():
                row.update(metadata.get(name, {}), query=expressions.get(name))
                if current_clock != 'aligned' or (baseline_window and prior_clock != 'aligned') or any(
                        correlation_quality_issues(q) for q in qualities.get(name, {}).values()):
                    row.update(delta=None, delta_percent=None, comparison_status='quality_unverified')
                if context_issues:
                    row.update(delta=None, delta_percent=None, comparison_status='replica_context_changed')
            entities.append({'identity': dict(identity), 'signals': signals, 'candidates': candidates,
                             'missing_sources': sorted(set(missing))})
        missing = sorted({issue for entity in entities for issue in entity['missing_sources']})
        if not entities:
            missing.append('endpoint:no_returned_observations')
        result.append({**replica, 'run_relation': 'configured', 'resource_attribution': 'not_established',
                       'clock_status': current_clock, 'baseline_clock_status': prior_clock,
                       **({'serving_context': contexts} if serving else {}),
                       'status': 'missing' if not entities else 'clock_unverified' if current_clock != 'aligned'
                           else 'partial_evidence' if missing else 'observed',
                       'entities': entities, 'missing_sources': missing,
                       'signals': entities[0]['signals'] if len(entities) == 1 else {},
                       'candidates': [candidate for entity in entities for candidate in entity['candidates']]})
    return result
