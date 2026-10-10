"""Independent raw-input fault oracle. No production diagnosis imports.

Fault labels are used only by the evaluator, never placed in an engine input.
These are injected telemetry phenotypes, not physical root-cause experiments.
"""

from dataclasses import dataclass
import random


@dataclass
class Case:
    kind: str
    current: dict
    history: list
    metrics: dict
    expected: set
    strong_expected: set
    abstain: bool = False
    stale: bool = False


def step(stamp, duration, *, tokens=1000, run='run', stages=None):
    return {'record_id': str(stamp), 'run_id': run, 'node': 'n', 'worker_id': 'w',
        'execution_mode': 'sync', 'boundary_scope': 'rl_step', 'observed_at': stamp,
        'step_duration_seconds': duration, 'stage_durations_seconds': stages or {'gen': duration},
        'analysis_window': {'start': stamp-duration, 'end': stamp, 'accuracy': 'approximate'},
        'workload': {'perf/total_num_tokens': tokens, 'policy_version': 7}}


def cases(seed=20261010, repeats=16):
    rng = random.Random(seed)
    kinds = ('normal', 'gpu_wait', 'network_wait', 'host_memory', 'noisy_tail',
             'short_cohort', 'workload_shift', 'stale', 'missing', 'multi_job')
    for kind in kinds:
        for _ in range(repeats):
            durations = [10 * rng.uniform(.98, 1.02) for i in range(9)]
            current_duration = 10 if kind in {'normal', 'host_memory', 'multi_job'} else 22
            expected = set()
            if kind == 'noisy_tail':
                durations = [5, 10, 10, 20, 20]
                current_duration = 16
            if kind == 'short_cohort': durations = durations[:1]
            history = [step(100 + i * 30, value) for i, value in enumerate(durations)]
            current = step(1000, current_duration, tokens=4000 if kind == 'workload_shift' else 1000)
            changed = kind not in {'normal', 'host_memory', 'multi_job'}
            metrics = {'gpu': (40 if changed else 90, 90), 'queue': (3 if changed else 0, 0),
                       'memory': (.05 if kind == 'host_memory' else .5, .5),
                       'swap': (4 if kind == 'host_memory' else 0, 0), 'rdma': (1e8, 1e8)}
            if kind == 'gpu_wait': expected = {'gpu_starvation', 'rollout_queue_backlog'}
            if kind == 'network_wait':
                current['stage_durations_seconds'] = {'weight_sync': 5}
                for row in history: row['stage_durations_seconds'] = {'weight_sync': 1}
                metrics['rdma'] = (4e8, 1e8)
                expected = {'gpu_starvation', 'communication_bound'}
            if kind == 'host_memory': expected = {'host_memory_pressure'}
            if kind == 'missing':
                metrics.pop('gpu')
                metrics.pop('queue')
            if kind == 'multi_job':
                history += [step(990, 1, run='other-job')]
            strong_expected = expected if kind == 'gpu_wait' else {'gpu_starvation'} if kind == 'network_wait' else set()
            yield Case(kind, current, history, metrics, expected, strong_expected,
                       abstain=kind in {'short_cohort', 'workload_shift', 'stale', 'missing'}, stale=kind == 'stale')


class MetricSource:
    """Prometheus query responses with raw scalar/entity/freshness information."""
    def __init__(self, case):
        self.case, self.calls = case, 0

    def query_range_detail(self, query, start, end, query_step):
        self.calls += 1
        if query.startswith('timestamp('):
            stamp = end-100 if self.case.stale and end == 1000 else end-1
            stats = {'max': stamp}
            return {'aggregate': stats, 'series': [{'stats': stats, 'source_timestamps': [start+1, stamp]}]}
        selectors = {'gpu': 'gpu_utilization', 'queue': 'num_requests_waiting',
                     'memory': 'MemAvailable', 'swap': 'pswpin', 'rdma': 'infiniband'}
        name = next((key for key, text in selectors.items() if text in query), None)
        if name not in self.case.metrics:
            return {'aggregate': None, 'series': []}
        value = self.case.metrics[name][0 if end == 1000 else 1]
        labels = {'node': 'n', 'instance': 'endpoint'}
        if name == 'gpu': labels['gpu'] = '0'
        stats = {'min': value, 'max': value, 'mean': value, 'sample_count': 5}
        return {'aggregate': stats, 'series': [{'labels': labels, 'stats': stats}]}
