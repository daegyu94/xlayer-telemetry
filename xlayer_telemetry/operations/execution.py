"""Read a bounded saved SDK span projection; never query or infer framework IDs."""

from __future__ import annotations

from itertools import islice
import json

from ..analysis.execution_graph import build_graph
from ..analysis.trajectory_path import critical_path
from ..analysis.behavior_signature import Boundary, summarize
from ..events import CorrelationContext


def _signature(row, records):
    context = CorrelationContext(**{key: row[key] for key in
        ('run_id', 'producer', 'role', 'worker_id', 'node', 'rank', 'local_rank', 'gpu', 'policy_version') if key in row})
    attributes = row.get('attributes', {})
    # Existing span sums remain independent of critical-path duration.
    boundary = Boundary(context, row['step'], row['step'], duration_seconds=row.get('duration_seconds'),
                        accuracy=row.get('boundary_accuracy', 'unknown'),
                        workload={key: attributes[key] for key in ('tokens', 'tool_calls', 'policy_version') if key in attributes},
                        time_reference=row.get('time_reference'), time_uncertainty_seconds=row.get('time_uncertainty_seconds'))
    return summarize(boundary, records)


def inspect_execution(run, *, root=None):
    manifest = run / 'telemetry-manifest.json'
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 1024 * 1024:
        raise ValueError('saved Run manifest missing or too large')
    data = json.loads(manifest.read_text())
    if not isinstance(data, dict):
        raise ValueError('saved Run manifest must be an object')
    run_id = data.get('run_id')
    records, truncated, invalid, remaining = [], False, 0, 4 * 1024 * 1024
    directory = run / 'telemetry-events'
    paths = sorted(islice(directory.glob('*.jsonl'), 17)) if not directory.is_symlink() else []
    if len(paths) > 16:
        truncated = True
    for path in paths[:16]:
        if path.is_symlink() or path.name.startswith('verl-steps'):
            continue
        with path.open('rb') as stream:
            while remaining > 0 and len(records) < 4096:
                line = stream.readline(min(remaining, 64 * 1024) + 1)
                remaining -= len(line)
                if len(line) > 64 * 1024 or remaining < 0:
                    truncated = True
                    break
                if not line:
                    break
                if not line.endswith(b'\n'):
                    invalid += 1
                    break
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError):
                    invalid += 1
                    continue
                if isinstance(row, dict) and row.get('record_type') == 'span':
                    records.append(row)
            if stream.read(1):
                truncated = True
    graph = build_graph(records, run_id=run_id)
    graph['quality']['truncated'] |= truncated
    graph['quality']['invalid_records'] += invalid
    origin = data.get('data_origin')
    result = {'graph': graph, 'source': 'stored_sdk_artifacts',
              'data_origin': origin if isinstance(origin, str) and 0 < len(origin) <= 64 else 'unknown', 'critical_path': None,
              'limitations': ['No automatic framework propagation; cross-node path needs window-matched clock evidence via Python API.']}
    if root is not None:
        result['critical_path'] = critical_path(graph, root)
        selected = [row for row in records if (row.get('trace_id'), row.get('span_id')) == tuple(root) and row.get('run_id') == run_id]
        if len(selected) == 1 and type(selected[0].get('step')) is int:
            try:
                result['behavior_signature'] = _signature(selected[0], records)
            except (ValueError, TypeError):
                result['behavior_signature_status'] = 'invalid_or_missing_boundary_context'
            if 'behavior_signature' in result and (graph['quality']['truncated'] or graph['quality']['invalid_records']):
                result['behavior_signature']['quality']['events_truncated'] = True
    return result
