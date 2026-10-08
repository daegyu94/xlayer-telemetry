"""Node identities and conservative temporal correlation under clock skew."""
import json
import hashlib
from pathlib import Path

import pytest

from xlayer_telemetry.analysis.clock_quality import assess_clocks
from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.metrics import Metric, MetricEmitter
from xlayer_telemetry.metrics.textfile import _iter_snapshots, build_metrics
from xlayer_telemetry.step_history import StepHistoryWriter
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, load_config
from xlayer_telemetry.analysis.diagnosis_analysis import select_baseline
from xlayer_telemetry.analysis import llm_diagnosis as llm


def stats(value):
    return dict(min=value, max=value, mean=value, last=value, sample_count=3)


@pytest.mark.parametrize('offset,sync,age,expected', [
    (0.1, 1, 2, 'aligned'), (12, 1, 2, 'unsafe'), (-12, 1, 2, 'unsafe'),
    (0, 0, 2, 'unsafe'), (0, None, 2, 'unknown'), (None, 1, 2, 'unknown'),
    (0, 1, 90, 'unsafe'), (0, 1, -2, 'unsafe'),
])
def test_clock_screening(offset, sync, age, expected):
    calls = []
    def query(expression, *args):
        calls.append(expression)
        value = sync if 'timex' in expression else age if expression.startswith('time()') else offset
        return stats(value) if value is not None else None
    report = assess_clocks(query, cluster='lab', nodes=['gpu-a', 'storage-a'], start=10, end=20)
    assert report['status'] == expected
    assert all('cluster="lab"' in query and 'job="telemetry"' in query for query in calls)
    assert len(report['nodes']) == 2


def test_one_missing_node_is_not_hidden_by_another_and_sync_is_optional():
    def query(expression, *args):
        if 'storage-a' in expression or 'timex_sync_status' in expression:
            return None
        return stats(0.1)
    report = assess_clocks(query, cluster='lab', nodes=['gpu-a', 'storage-a'], start=10, end=20, require_sync=False)
    assert report['status'] == 'unknown'
    assert report['nodes']['gpu-a']['status'] == 'aligned'
    assert report['nodes']['storage-a']['status'] == 'unknown'


def test_shared_output_keeps_node_local_workers_and_runs_distinct(tmp_path):
    paths, events = [], []
    for run, node in [('r', 'gpu-a'), ('r', 'gpu-b'), ('other', 'gpu-a')]:
        paths.append(MetricEmitter(tmp_path, run_id=run, node=node, producer='agent',
                                  role='rollout', worker_id='0').emit(step=1, samples=[Metric('training_loss', 1)]))
        recorder = EventRecorder(tmp_path, CorrelationContext(run, 'agent', 'rollout', '0', node))
        recorder.event('tool.call', phase='rollout')
        events.append(recorder.path)
    assert len(set(paths)) == len(set(events)) == 3
    snapshots = _iter_snapshots(tmp_path)
    assert len(snapshots) == 3
    samples = build_metrics(snapshots)
    assert {(s.labels['run_id'], s.labels['node']) for s in samples if s.name == 'training_loss'} == {
        ('r', 'gpu-a'), ('r', 'gpu-b'), ('other', 'gpu-a')}


def test_wall_clock_jump_does_not_change_duration_or_trace_parent(tmp_path):
    wall = iter([100_000_000_000, 90_000_000_000])
    mono = iter([1_000_000_000, 3_000_000_000])
    recorder = EventRecorder(tmp_path, CorrelationContext('r', 'agent', 'sandbox', '0', 'storage-a'),
                             clock_ns=lambda: next(wall), monotonic_ns=lambda: next(mono))
    with recorder.span('sandbox.exec', phase='environment', trace_id='abc', parent_span_id='parent'):
        pass
    record = json.loads(recorder.path.read_text())
    assert record['duration_seconds'] == 2
    assert record['boundary_accuracy'] == 'clock_discontinuity'
    assert record['trace_id'] == 'abc' and record['parent_span_id'] == 'parent'


def test_step_identity_baseline_and_legacy_restart_are_node_specific(tmp_path):
    path = tmp_path / 'steps.jsonl'
    source = {'step': 1, 'data': {'timing_s/step': 10}}
    a = StepHistoryWriter(path, run_id='r', node='gpu-a', worker_id='0', clock=lambda: 20).append(source)
    b = StepHistoryWriter(path, run_id='r', node='gpu-b', worker_id='0', clock=lambda: 21).append(source)
    assert a['record_id'] != b['record_id']
    assert select_baseline(b, [a]) is None
    # A legacy same-node record must still deduplicate after upgrade.
    a['record_id'] = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]
    path.write_text(json.dumps(a) + '\n')
    assert StepHistoryWriter(path, run_id='r', node='gpu-a', worker_id='0').append(source) is None
    assert StepHistoryWriter(path, run_id='r', node='gpu-b', worker_id='0').append(source) is not None


class Backend:
    def __init__(self, offset=0):
        self.offset, self.queries = offset, []
    def query_range(self, expression, start, end, step):
        self.queries.append(expression)
        if 'timex' in expression: return stats(1)
        if 'timestamp(node_time_seconds' in expression:
            return stats(0 if expression.startswith('time()') else self.offset)
        if 'vllm:kv' in expression: return stats(.98)
        if 'preemptions' in expression: return stats(10) | {'max_series_delta': 2}
        if 'waiting' in expression: return stats(4)
        return None


@pytest.mark.parametrize('offset,state', [(0, 'bottleneck_suspected'), (10, 'insufficient_data')])
def test_split_gpu_rollout_storage_queries_and_clock_guard(offset, state):
    backend = Backend(offset)
    engine = DiagnosticEngine({'prometheus': {'url': 'unused'}, 'cluster': 'lab',
                               'compute_node': 'gpu-b', 'rollout_node': 'rollout-a',
                               'storage_node': 'storage-a', 'storage_device': 'nvme1n1',
                               'clock':{'monitoring_node':'monitor'}}, prometheus=backend)
    report = engine.analyze({'node': 'trainer-a', 'run_id': 'r', 'worker_id': '0',
                            'analysis_window': {'start': 10, 'end': 20}}, [])
    assert report['verdict'] == state
    assert any('node="rollout-a"' in q and 'vllm:kv' in q for q in backend.queries)
    assert any('nodename="gpu-b"' in q and 'gpu_utilization' in q for q in backend.queries)
    assert any('instance="storage-a"' in q and 'device="nvme1n1"' in q for q in backend.queries)
    assert all('cluster="lab"' in q for q in backend.queries)
    if offset:
        assert not report['candidates'] and not report['comparison']['signals']
        assert any(item.startswith('clock:') for item in report['missing_sources'])
    else:
        assert any(item['id'] == 'kv_cache_pressure' for item in report['candidates'])


def test_gpu_zero_from_different_nodes_cannot_make_a_baseline_drop():
    class Prom(Backend):
        def query_range_detail(self, expression, start, end, step):
            if 'gpu_utilization' not in expression: return {'aggregate': None, 'series': []}
            node, value = ('gpu-a', 90) if start == 0 else ('gpu-b', 40)
            return {'aggregate': stats(value), 'series': [{'labels': {'cluster': 'lab', 'instance': node, 'gpu': '0'}, 'stats': stats(value)}]}
    common = dict(run_id='r', node='driver', worker_id='0', boundary_scope='rl_step')
    baseline = common | {'observed_at': 10, 'step_duration_seconds': 10, 'analysis_window': {'start': 0, 'end': 10}}
    current = common | {'observed_at': 30, 'step_duration_seconds': 20, 'analysis_window': {'start': 20, 'end': 30}}
    report = DiagnosticEngine({'prometheus': {'url': 'unused'}}, prometheus=Prom()).analyze(current, [baseline])
    assert 'gpu:baseline_entity_match' in report['missing_sources']
    gpu = next(item for item in report['comparison']['signals'] if item['signal'] == 'gpu_utilization_percent')
    assert gpu['baseline'] is None


def test_llm_clock_guard_preserves_quality_without_rule_verdict():
    packet = llm.packet_from_report({'clock_quality': {'status': 'unsafe'}, 'comparison': {'signals': []}})
    assert llm.model_view(packet)['clock_quality']['status'] == 'unsafe'
    with pytest.raises(ValueError, match='clock alignment'):
        llm.validate_diagnosis({'assessment': 'no_issue_observed', 'summary': 'normal', 'candidates': [], 'limitations': []}, packet)


@pytest.mark.parametrize('clock', [{'enabled': True}, {'require_sync': 'yes'}, {'max_skew_seconds': -1}])
def test_invalid_clock_config(tmp_path, clock):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'schema_version': 1, 'prometheus': {'url': 'unused'}, 'clock': clock}))
    with pytest.raises(ValueError): load_config(path)


def test_long_valid_identity_fits_linux_filename_limit(tmp_path):
    name = 'a-' * 32
    emitter = MetricEmitter(tmp_path, run_id=name, producer=name, role=name, worker_id=name, node=name)
    path = emitter.emit(step=1, samples=[Metric('training_loss', 1)])
    assert path is not None and len(path.name.encode()) < 255


def test_gpu_less_host_collector_does_not_start_gpu_sampler(tmp_path):
    import os
    import subprocess
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path / 'tools/node_exporter-1.9.1.linux-amd64/node_exporter'
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/usr/bin/env bash\nexec sleep 30\n')
    binary.chmod(0o755)
    marker = tmp_path / 'gpu-called'
    fake_python = tmp_path / 'python'
    fake_python.write_text('#!/usr/bin/env bash\ntouch "$MARKER"\nexit 1\n')
    fake_python.chmod(0o755)
    state = tmp_path / 'state'
    (state / 'textfile').mkdir(parents=True)
    (state / 'textfile/gpu.prom').write_text('stale 1\n')
    result = subprocess.run(['bash', str(root / 'scripts/run_telemetry.sh'), 'node'], cwd=root,
        env=os.environ | {'TOOLS_DIR': str(tmp_path / 'tools'), 'OUTPUT_DIR': str(state),
                          'NODE_ADDR': '127.0.0.1', 'ENABLE_GPU_METRICS': '0', 'DURATION': '0.1',
                          'PYTHON': str(fake_python), 'MARKER': str(marker),
                          'TELEMETRY_METRICS_DIR': '', 'TOPOLOGY_DIR': '', 'LOKI_PUSH_URL': '',
                          'TELEMETRY_LOG_ROOTS': ''},
        capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert not marker.exists() and not (state / 'textfile/gpu.prom').exists()


def test_textfile_collector_filters_shared_directory_by_node(tmp_path, monkeypatch):
    from xlayer_telemetry.metrics import textfile
    import sys
    shared = tmp_path / 'shared'
    output = tmp_path / 'output'
    for node in ['gpu-a', 'gpu-b']:
        MetricEmitter(shared, run_id='r', producer='agent', role='rollout', worker_id='0', node=node).emit(
            step=1, samples=[Metric('training_loss', 1)])
    monkeypatch.setattr(sys, 'argv', ['textfile', '--metrics-dir', str(shared), '--textfile-dir', str(output), '--node', 'gpu-a'])
    def stop(*args): raise RuntimeError('end iteration')
    monkeypatch.setattr(textfile.time, 'sleep', stop)
    with pytest.raises(RuntimeError, match='end iteration'): textfile.main()
    content = (output / 'application.prom').read_text()
    assert 'node="gpu-a"' in content and 'node="gpu-b"' not in content


def test_unknown_baseline_clock_withholds_resource_delta():
    class Prom(Backend):
        def query_range(self, expression, start, end, step):
            if start == 0 and 'node_time_seconds' in expression: return None
            return super().query_range(expression, start, end, step)
    common = dict(run_id='r', node='driver', worker_id='0', boundary_scope='rl_step')
    baseline = common | {'observed_at': 10, 'step_duration_seconds': 10, 'analysis_window': {'start': 0, 'end': 10}}
    current = common | {'observed_at': 30, 'step_duration_seconds': 20, 'analysis_window': {'start': 20, 'end': 30}}
    report = DiagnosticEngine({'prometheus': {'url': 'unused'}, 'cluster': 'lab'}, prometheus=Prom()).analyze(current, [baseline])
    assert report['clock_quality']['status'] == 'aligned'
    assert report['clock_quality']['baseline']['status'] == 'unknown'
    assert report['verdict'] == 'insufficient_data' and not report['candidates']
    assert not report['comparison']['signals']


def test_timeline_preserves_cluster_node_identity_and_clock_panels():
    root = Path(__file__).resolve().parents[1]
    dashboard = json.loads((root / 'examples/dashboards/cross-layer-timeline.json').read_text())
    panels = dashboard['panels'] + [child for row in dashboard['panels']
                                    for child in row.get('panels', [])]
    for panel in panels:
        if panel.get('datasource', {}).get('type') == 'prometheus':
            for target in panel['targets']:
                assert 'cluster=~"$cluster"' in target['expr']
    assert any(panel['title'] == 'Node clock synchronization status' for panel in panels)
    assert any(panel['title'] == 'Node clock offset from scrape time' for panel in panels)
