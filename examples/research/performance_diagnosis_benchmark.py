"""CPU fault evaluation plus measured analyzer/SDK cost; no physical GPU faults."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc

if __package__:
    from .diagnosis_faults import MetricSource, cases
else:
    from diagnosis_faults import MetricSource, cases
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine


def evaluate(repeats=16):
    result = {}
    for robust in (False, True):
        totals = defaultdict(lambda: {'cases': 0, 'exact': 0, 'false_positive': 0, 'abstained': 0,
                                      'expected_signals': 0, 'detected_signals': 0})
        costs, queries = [], []
        for case in cases(repeats=repeats):
            source = MetricSource(case)
            config = {'prometheus': {'url': 'http://unused'}, 'sampling': {'check_source_freshness': True},
                      'baseline': {'match_fields': ['perf/total_num_tokens', 'policy_version'], 'robust': {'enabled': robust}}}
            began = time.perf_counter()
            report = DiagnosticEngine(config, prometheus=source, clock=lambda: 1010).analyze(case.current, case.history)
            costs.append((time.perf_counter()-began)*1000)
            queries.append(source.calls)
            strong = {row['id'] for row in report['candidates'] if row['state'] == 'strong_signal'}
            supported = {row['id'] for row in report['candidates'] if row['state'] in {'strong_signal', 'supporting_signal'}}
            stats = totals[case.kind]
            stats['cases'] += 1
            stats['exact'] += int(strong == case.strong_expected)
            stats['false_positive'] += int(bool(strong-case.strong_expected))
            stats['abstained'] += int(case.abstain and not strong)
            stats['expected_signals'] += len(case.expected)
            stats['detected_signals'] += len(supported & case.expected)
        result['robust' if robust else 'existing'] = {'scenarios': dict(totals),
            'median_analysis_ms': statistics.median(costs), 'p95_analysis_ms': sorted(costs)[int(.95*len(costs))-1],
            'queries': {'min': min(queries), 'max': max(queries), 'total': sum(queries)}}
    return result


def graph_fixture(count):
    records = []
    for index in range(count):
        root = index == 0
        start, end = (0, count-1) if root else (index-1, index)
        records.append({'record_type': 'span', 'run_id': 'run', 'trace_id': 'trace', 'span_id': str(index),
            'name': 'trajectory' if root else 'generation', 'phase': 'trajectory' if root else 'generation',
            'step': 1, 'node': 'n', 'worker_id': 'w', 'producer': 'sdk', 'role': 'actor',
            'start_time_unix_nano': start*10**9, 'end_time_unix_nano': end*10**9,
            'boundary_accuracy': 'exact', 'duration_seconds': end-start,
            **({} if root else {'parent_span_id': '0'}),
            'attributes': {'execution_contract': 'dependency_dag', 'children_complete': True,
                           'completion_span_id': str(count-1)} if root else {},
            'links': [{'trace_id': 'trace', 'span_id': str(index-1), 'relation': 'depends_on'}] if index > 1 else []})
    return records


def graph_cost():
    from xlayer_telemetry.analysis.execution_graph import build_graph
    from xlayer_telemetry.analysis.trajectory_path import critical_path
    rows = []
    for count in (64, 256, 1024, 4096):
        records = graph_fixture(count)
        costs = []
        for _ in range(7):
            began = time.perf_counter()
            graph = build_graph(records, run_id='run')
            path = critical_path(graph, ('trace', '0'))
            costs.append((time.perf_counter()-began)*1000)
        tracemalloc.start()
        path = critical_path(build_graph(records, run_id='run'), ('trace', '0'))
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert path['status'] == 'observed_path' and path['observed_path_seconds'] == count-1
        rows.append({'spans': count, 'median_ms': statistics.median(costs), 'peak_traced_bytes': peak})
    return rows


def sdk_cost(modes=('existing', 'linked')):
    from xlayer_telemetry.events import CorrelationContext, EventRecorder
    if 'linked' in modes:
        from xlayer_telemetry.events import SpanIdentity, SpanLink
    result = {}
    # Alternate modes to reduce filesystem/order bias. Both write real JSONL;
    # this measures cooperative SDK overhead, not workload/profiler overhead.
    samples = {mode: {'times': [], 'sizes': []} for mode in modes}
    with tempfile.TemporaryDirectory(prefix='xltel-diagnosis-sdk-') as directory:
        for batch in range(6):
            for mode in (modes if batch % 2 == 0 else tuple(reversed(modes))):
                recorder = EventRecorder(Path(directory)/f'{mode}-{batch}', CorrelationContext('run', 'sdk', 'actor', 'w', 'n'))
                began = time.perf_counter()
                for _ in range(100):
                    with recorder.span('generation', phase='generation', step=1,
                        **({'links': [SpanLink(SpanIdentity('trace', 'predecessor'), 'depends_on')]} if mode == 'linked' else {})):
                        pass
                samples[mode]['times'].append((time.perf_counter()-began)*1e6/100)
                samples[mode]['sizes'].append(recorder.path.stat().st_size/100)
        for mode, values in samples.items():
            result[mode] = {'median_us_per_span': statistics.median(values['times']), 'bytes_per_span': statistics.median(values['sizes'])}
    return result


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=16)
    parser.add_argument('--baseline-checkout', type=Path, help='Read-only prior checkout; run the same input oracle in a separate Python process')
    args = parser.parse_args()
    if not 1 <= args.repeats <= 128: parser.error('repeats must be between 1 and 128')
    result = {'data_origin': 'synthetic_fault_injection', 'seed': 20261010,
        'environment': {'python': platform.python_version(), 'platform': platform.platform()},
        'diagnosis': evaluate(args.repeats), 'graph': graph_cost(), 'sdk': sdk_cost(),
        'limitations': ['No physical cause accuracy, GPU/3FS/Ray/LLM inference measured.',
                        'Query responses injected; measured CPU/JSONL cost is real.']}
    if args.baseline_checkout:
        before = args.baseline_checkout.resolve()
        if not (before/'xlayer_telemetry/analysis/diagnostics.py').is_file(): parser.error('baseline checkout missing diagnostics.py')
        command = [sys.executable, '-c',
            "import json,sys; from performance_diagnosis_benchmark import evaluate,sdk_cost; "
            "print(json.dumps({'diagnosis':evaluate(int(sys.argv[1]))['existing'],'sdk':sdk_cost(('existing',))['existing']}))",
            str(args.repeats)]
        output = subprocess.run(command, cwd=before, env={**os.environ,
            'PYTHONPATH': os.pathsep.join((str(before), str(Path(__file__).resolve().parent)))},
            capture_output=True, text=True, timeout=60, check=True)
        result['baseline_checkout'] = json.loads(output.stdout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
