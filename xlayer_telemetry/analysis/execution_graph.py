"""Observed SDK execution relationships, independent of Step-number matching.

Parenthood/consumption identifies a relation, not a blocking dependency. Only an
explicit depends_on link can enter critical-path analysis. Resource metric
overlap and configured topology never manufacture such links.
"""

from __future__ import annotations

from collections import deque

from ..events import LINK_RELATIONS
from ..measurements import finite_number
from ..time_alignment import event_window


def _id(value):
    return isinstance(value, str) and 0 < len(value) <= 128


def _interval(record):
    accuracy = record.get('boundary_accuracy', 'unknown')
    accuracy = accuracy if isinstance(accuracy, str) else 'unknown'
    calibrated = accuracy == 'calibrated'
    alignment = record.get('time_alignment', {})
    alignment = alignment if isinstance(alignment, dict) else {}
    prefix = 'correlation_' if calibrated else ''
    start, end = (record.get(prefix + key + '_time_unix_nano') for key in ('start', 'end'))
    duration = finite_number(record.get('duration_seconds'))
    valid = (accuracy in {'exact', 'calibrated'}
             and type(start) is int and type(end) is int and 0 <= start <= end
             and duration is not None and duration >= 0)
    if valid and abs((end-start)/1e9-duration) > 1e-6:
        valid = False  # Never repair a wall-clock jump with inferred bounds.
    uncertainty = finite_number(record.get('time_uncertainty_seconds')) if calibrated else 0.
    reference = record.get('time_reference') if calibrated else None
    if calibrated and (not _id(reference) or uncertainty is None or uncertainty < 0):
        valid = False
    if calibrated and valid:
        mapped = event_window(record, reference_id=reference)
        if (not isinstance(alignment, dict) or 'time_alignment' not in record or None in mapped
                or abs(mapped[0]-start/1e9) > 1e-6 or abs(mapped[1]-end/1e9) > 1e-6
                or uncertainty < alignment.get('uncertainty_seconds', float('inf'))):
            valid = False
    return {'start_ns': start if valid else None, 'end_ns': end if valid else None,
            'duration_seconds': duration, 'accuracy': accuracy,
            'reference': reference, 'uncertainty_seconds': uncertainty, 'valid': valid,
            **({'original_start_ns': record.get('start_time_unix_nano'), 'original_end_ns': record.get('end_time_unix_nano'),
                'reference_session': alignment.get('reference_session')} if calibrated else {})}


def aligned(a, b, clock_quality=None):
    """A node-local exact interval is not proof of cross-node alignment."""
    x, y = a['interval'], b['interval']
    if not x['valid'] or not y['valid'] or not _id(a.get('node')) or not _id(b.get('node')):
        return False
    if a['node'] == b['node']:
        return x['reference'] == y['reference'] and x.get('reference_session') == y.get('reference_session')
    clocks = (clock_quality or {}).get('nodes', {})
    window = (clock_quality or {}).get('window', {})
    start, end = finite_number(window.get('start')), finite_number(window.get('end'))
    return ((clock_quality or {}).get('status') == 'aligned' and start is not None and end is not None
            and start * 1e9 <= min(x['start_ns'], y['start_ns']) and end * 1e9 >= max(x['end_ns'], y['end_ns'])
            and x['accuracy'] == y['accuracy'] == 'calibrated' and x['reference'] == y['reference']
            and x.get('reference_session') == y.get('reference_session')
            and all(clocks.get(node, {}).get('status') == 'aligned' for node in (a['node'], b['node'])))


def topological(keys, edges):
    outgoing, degree = {key: [] for key in keys}, {key: 0 for key in keys}
    for source, target in edges:
        if source in degree and target in degree:
            outgoing[source].append(target)
            degree[target] += 1
    pending = deque(key for key in keys if degree[key] == 0)
    order = []
    while pending:
        source = pending.popleft()
        order.append(source)
        for target in outgoing[source]:
            degree[target] -= 1
            if degree[target] == 0:
                pending.append(target)
    return order


def build_graph(records, *, run_id, relations=(), clock_quality=None, max_spans=4096, max_edges=8192):
    if not _id(run_id) or type(max_spans) is not int or not 1 <= max_spans <= 4096 or type(max_edges) is not int or not 1 <= max_edges <= 8192:
        raise ValueError('invalid graph identity or budget')
    quality = {'truncated': False, 'foreign_records': 0, 'invalid_records': 0,
               'duplicate_spans': 0, 'unresolved_edges': 0, 'cycle': False}
    nodes, raw = {}, {}
    for index, row in enumerate(records):
        if index >= max_spans:
            quality['truncated'] = True
            break
        if not isinstance(row, dict) or row.get('record_type') != 'span':
            continue
        if row.get('run_id') != run_id:
            quality['foreign_records'] += 1
            continue
        if not all(_id(row.get(key)) for key in ('trace_id', 'span_id', 'name', 'phase')):
            quality['invalid_records'] += 1
            continue
        key = (row['trace_id'], row['span_id'])
        if key in nodes:
            nodes[key]['ambiguous'] = True
            quality['duplicate_spans'] += 1
            continue
        attributes = row.get('attributes', {})
        attributes = attributes if isinstance(attributes, dict) else {}
        allowed = ('trajectory_id', 'rollout_id', 'turn', 'tokens', 'tool_calls', 'policy_version',
                   'execution_contract', 'children_complete', 'completion_span_id', 'completion_trace_id',
                   'trajectory_completion', 'time_kind', 'request_id', 'ray_job_id', 'ray_task_id',
                   'ray_actor_id', 'replica', 'engine')
        nodes[key] = {'id': list(key), **{field: row.get(field) if _id(row.get(field)) else None for field in
            ('run_id', 'node', 'worker_id', 'producer', 'role', 'name', 'phase', 'status')},
            'step': row.get('step') if type(row.get('step')) is int and row['step'] >= 0 else None,
            **{field: row[field] for field in ('rank', 'local_rank', 'policy_version')
               if type(row.get(field)) is int and row[field] >= 0},
            **({'gpu': row['gpu']} if _id(row.get('gpu')) else {}),
            'attributes': {field: attributes[field] for field in allowed if field in attributes
                           and isinstance(attributes[field], (str, int, float, bool))
                           and (not isinstance(attributes[field], str) or len(attributes[field]) <= 128)
                           and (not isinstance(attributes[field], float) or finite_number(attributes[field]) is not None)},
            'interval': _interval(row), 'ambiguous': False}
        raw[key] = row
    edges, seen = [], set()

    def add(source, target, kind, relation, provenance):
        if len(edges) >= max_edges:
            quality['truncated'] = True
            return
        if not all(isinstance(key, (list, tuple)) and len(key) == 2 and all(_id(value) for value in key) for key in (source, target)):
            quality['invalid_records'] += 1
            return
        source, target = tuple(source), tuple(target)
        key = (source, target, kind, relation)
        if key in seen:
            return
        seen.add(key)
        a, b = nodes.get(source), nodes.get(target)
        if a is None or b is None or a['ambiguous'] or b['ambiguous']:
            quality['unresolved_edges'] += 1
            kind = 'unknown'
        edges.append({'source': list(source), 'target': list(target), 'kind': kind,
                      'relation': relation, 'provenance': provenance,
                      'timing_status': 'aligned' if a and b and aligned(a, b, clock_quality) else 'unknown'})

    for key, row in raw.items():
        if row.get('parent_span_id') is not None:
            add((key[0], row['parent_span_id']), key, 'observed', 'parent', 'parent_span_id')
        links = row.get('links', [])
        if not isinstance(links, list):
            quality['invalid_records'] += 1
            continue
        if len(links) > 16:
            quality['truncated'] = True
        for link in links[:16]:
            if not isinstance(link, dict) or not isinstance(link.get('relation'), str) or link.get('relation') not in LINK_RELATIONS:
                quality['invalid_records'] += 1
                continue
            add((link.get('trace_id'), link.get('span_id')), key, 'observed', link['relation'], 'span_link')
    for index, row in enumerate(relations):
        if index >= max_edges:
            quality['truncated'] = True
            break
        if not isinstance(row, dict) or not isinstance(row.get('kind'), str) or row.get('kind') not in {'configured', 'temporal', 'unknown'} or not _id(row.get('relation')):
            quality['invalid_records'] += 1
            continue
        add(row.get('source'), row.get('target'), row['kind'], row['relation'], 'caller_declared')
    observed = [(tuple(edge['source']), tuple(edge['target'])) for edge in edges if edge['kind'] == 'observed']
    quality['cycle'] = len(topological(nodes, observed)) != len(nodes)
    return {'schema_version': 1, 'record_type': 'execution_dependency_graph', 'run_id': run_id,
            'nodes': list(nodes.values()), 'edges': edges, 'quality': quality,
            'interpretation': 'instrumented relationships; resource attribution and causality not established'}
