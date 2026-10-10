"""Critical blocking chain of a declared, complete, flat trajectory DAG.

An observed parent or generic Span Link alone is insufficient. Only explicit
completion dependencies are used. Gaps retain unknown ownership/time kind;
parallel and nested inclusive durations are never blindly added.
"""

from __future__ import annotations

from collections import defaultdict
import heapq

from .execution_graph import topological
from .robust_differential import assess


def critical_path(graph, root):
    root = tuple(root)
    nodes = {tuple(node['id']): node for node in graph['nodes']}
    result = {'schema_version': 1, 'record_type': 'trajectory_critical_path', 'run_id': graph['run_id'],
              'root': list(root), 'status': 'unknown', 'path': [], 'observed_path_seconds': None,
              'trajectory_seconds': None, 'measured_wait_seconds': None, 'unattributed_seconds': None,
              'coverage': None, 'missing_evidence': [], 'delay_candidates': [],
              'interpretation': 'observed blocking chain; unknown gaps are not measured waits; no resource cause or attribution'}

    def unknown(reason):
        result['missing_evidence'].append(reason)
        return result

    if root not in nodes:
        return unknown('root_span_missing')
    owner = nodes[root]
    attrs = owner['attributes']
    if attrs.get('execution_contract') != 'dependency_dag' or attrs.get('children_complete') is not True:
        return unknown('complete_blocking_dependency_contract_missing')
    if graph['quality']['truncated'] or graph['quality']['invalid_records']:
        return unknown('graph_incomplete_or_budget_exhausted')
    members = {tuple(edge['target']) for edge in graph['edges']
               if edge['relation'] == 'parent' and tuple(edge['source']) == root and edge['kind'] == 'observed'}
    if not members:
        return unknown('trajectory_operations_missing')
    if any(edge['relation'] == 'parent' and tuple(edge['source']) in members for edge in graph['edges']):
        return unknown('nested_inclusive_spans_require_decomposition')
    if any(edge['kind'] == 'unknown' and (tuple(edge['target']) in members | {root}
            or tuple(edge['source']) in members | {root}) for edge in graph['edges']):
        return unknown('unresolved_execution_relation')
    parents = {tuple(edge['target']): edge for edge in graph['edges']
               if edge['relation'] == 'parent' and tuple(edge['source']) == root}
    dependencies = [edge for edge in graph['edges'] if edge['kind'] == 'observed'
                    and edge['relation'] == 'depends_on' and tuple(edge['target']) in members]
    if any(tuple(edge['source']) not in members for edge in dependencies):
        return unknown('dependency_outside_trajectory')
    if any(nodes[key]['ambiguous'] or not nodes[key]['interval']['valid'] for key in members | {root}):
        return unknown('ambiguous_identity_or_imprecise_span')
    if any(parents[key]['timing_status'] != 'aligned' for key in members):
        return unknown('trajectory_clock_alignment_unverified')
    interval = owner['interval']
    duration = interval['duration_seconds']
    result['trajectory_seconds'] = duration
    for key in members:
        child = nodes[key]['interval']
        # Containment must be consistent; no truncation/clock repair is applied.
        if child['start_ns'] < interval['start_ns'] or child['end_ns'] > interval['end_ns']:
            return unknown('operation_outside_trajectory_window')
        if nodes[key]['node'] != owner['node']:
            margin = (child['uncertainty_seconds'] + interval['uncertainty_seconds']) * 1e9
            if child['start_ns'] - margin < interval['start_ns'] or child['end_ns'] + margin > interval['end_ns']:
                return unknown('cross_node_containment_uncertain')
        elif child['accuracy'] == interval['accuracy'] == 'calibrated' and (
                child['original_start_ns'] < interval['original_start_ns']
                or child['original_end_ns'] > interval['original_end_ns']):
            return unknown('original_node_containment_conflicts_with_mapping')
    pairs = [(tuple(edge['source']), tuple(edge['target'])) for edge in dependencies]
    order = topological([key for key in nodes if key in members], pairs)
    if len(order) != len(members):
        return unknown('blocking_dependency_cycle')
    incoming = defaultdict(list)
    for edge in dependencies:
        source, target = tuple(edge['source']), tuple(edge['target'])
        a, b = nodes[source], nodes[target]
        x, y = a['interval'], b['interval']
        remote = a['node'] != b['node']
        margin = (x['uncertainty_seconds'] + y['uncertainty_seconds']) * 1e9 if remote else 0
        if edge['timing_status'] != 'aligned' or x['end_ns'] + margin > y['start_ns']:
            return unknown('dependency_order_or_clock_uncertainty_unverified')
        if not remote and x['accuracy'] == y['accuracy'] == 'calibrated' and x['original_end_ns'] > y['original_start_ns']:
            return unknown('original_node_order_conflicts_with_mapping')
        incoming[target].append(source)
    completion = (attrs.get('completion_trace_id', root[0]), attrs.get('completion_span_id'))
    if attrs.get('completion_span_id') is None:
        marked = [key for key in members if nodes[key]['attributes'].get('trajectory_completion') is True]
        completion = marked[0] if len(marked) == 1 else None
    if completion not in members:
        return unknown('completion_span_missing')
    # Every operation must be covered by the declared completion barrier. A
    # parent relation alone never makes background work a blocking operation.
    covered, pending = set(), [completion]
    while pending:
        key = pending.pop()
        if key not in covered:
            covered.add(key)
            pending.extend(incoming[key])
    if covered != members:
        return unknown('operations_not_linked_to_completion')
    previous, equivalent = {}, 0
    for key in order:
        predecessors = incoming[key]
        if not predecessors:
            previous[key] = None
            continue
        chosen = max(predecessors, key=lambda p: (nodes[p]['interval']['end_ns'], p))
        last = nodes[chosen]
        for other in predecessors:
            if other == chosen:
                continue
            peer = nodes[other]
            if peer['node'] != last['node']:
                margin = (peer['interval']['uncertainty_seconds'] + last['interval']['uncertainty_seconds']) * 1e9
                if last['interval']['end_ns'] - peer['interval']['end_ns'] <= margin:
                    return unknown('last_completion_ambiguous_with_clock_uncertainty')
            elif peer['interval']['end_ns'] == last['interval']['end_ns']:
                equivalent += 1
        previous[key] = chosen
    path, cursor = [], completion
    while cursor is not None:
        path.append(cursor)
        cursor = previous[cursor]
    path.reverse()
    seconds = sum(nodes[key]['interval']['duration_seconds'] for key in path)
    if seconds > duration + 1e-9:
        return unknown('path_duration_exceeds_trajectory')
    wait = sum(nodes[key]['interval']['duration_seconds'] for key in path
               if nodes[key]['attributes'].get('time_kind') == 'wait')
    candidates = [{'id': list(key), 'name': nodes[key]['name'], 'phase': nodes[key]['phase'],
        'node': nodes[key]['node'], 'worker_id': nodes[key]['worker_id'],
        'seconds': nodes[key]['interval']['duration_seconds'],
        'time_kind': nodes[key]['attributes'].get('time_kind', 'unspecified'),
        'supporting': ['observed_span', 'explicit_blocking_dependency'], 'counter': [],
        'missing': ['resource_cause_not_established']} for key in path]
    result.update(status='observed_path', path=[list(key) for key in path], observed_path_seconds=seconds,
                  measured_wait_seconds=wait, unattributed_seconds=max(0, duration-seconds),
                  coverage=seconds/duration if duration > 0 else None,
                  equivalent_completion_ties=equivalent,
                  context={field: owner.get(field) for field in ('node', 'worker_id', 'producer', 'role',
                                                                'rank', 'local_rank', 'gpu', 'policy_version')},
                  delay_candidates=heapq.nlargest(8, candidates, key=lambda row: row['seconds']),
                  candidate_limit=8)
    if result['unattributed_seconds'] > 1e-9:
        result['missing_evidence'].append('unattributed_wall_time')
    return result


def compare_paths(current, references, *, max_references=31):
    """Matched trajectory history, with finite cohort uncertainty, not a cause."""
    if type(max_references) is not int or not 1 <= max_references <= 31:
        raise ValueError('invalid trajectory reference budget')
    values, seen, truncated = [], set(), False
    for index, row in enumerate(references):
        if index >= max_references:
            truncated = True
            break
        key = (row.get('run_id'), row.get('sequence'))
        if (row.get('status') != 'observed_path' or not all(current.get('context', {}).get(key)
                for key in ('node', 'worker_id', 'producer', 'role')) or row.get('run_id') != current.get('run_id')
                or row.get('context') != current.get('context') or not current.get('workload')
                or row.get('workload') != current.get('workload') or type(row.get('sequence')) is not int
                or type(current.get('sequence')) is not int or row['sequence'] >= current['sequence'] or key in seen):
            continue
        seen.add(key)
        values.append(row.get('trajectory_seconds'))
    quality = assess(current.get('trajectory_seconds'), values)
    comparable = current.get('status') == 'observed_path' and quality['count'] >= 5 and not truncated
    return {'reference_count': quality['count'], 'robust_baseline': quality,
            'long_tail': quality['status'] == 'shift_observed' and quality['robust_z'] > 0 if comparable else None,
            'references_truncated': truncated, 'comparison': 'same_run_same_worker_exact_workload_history',
            'missing_evidence': [] if comparable else ['comparable_complete_trajectory_cohort']}
