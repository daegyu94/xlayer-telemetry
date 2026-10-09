"""Multi-job inputs travel through existing SDK/exporter/diagnosis contracts."""
import json
from pathlib import Path

import pytest

from xlayer_telemetry.demos.multi_job import diagnosis_config, make_schedule, record_job, validate_schedule
from xlayer_telemetry.demos.live import Demo, prometheus_config
from xlayer_telemetry.analysis.diagnosis_analysis import resource_run_relation, select_baseline
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, _investigation_rows
from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.metrics import Metric, MetricEmitter
from xlayer_telemetry.metrics.textfile import _iter_snapshots, build_metrics


ROOT = Path(__file__).parents[1]


def test_three_jobs_share_ids_and_node_but_keep_independent_execution(tmp_path):
    schedule = make_schedule(start=1000, node='gpu-node-0')
    assert len(schedule['jobs']) == 3
    assert len({job['model'] for job in schedule['jobs']}) == 3
    assert len({job['instance'] for job in schedule['jobs']}) == 3
    records = []
    for job in schedule['jobs']:
        root = tmp_path / job['scenario']['run_id']
        rows = record_job(root, job)
        assert rows[0]['step'] == 127 and rows[1]['step'] == 128
        assert rows[0]['worker_id'] == 'driver' and rows[0]['node'] == 'gpu-node-0'
        records.extend(rows)
        assert json.loads((root / 'telemetry-manifest.json').read_text())['model']['identifier'] == job['model']
        assert not (root / 'diagnostics').exists(), 'Producer must not inject scenario diagnosis'
        events = [json.loads(line) for p in (root / 'telemetry-events').glob('*.jsonl') for line in p.read_text().splitlines()]
        assert {event['run_id'] for event in events} == {job['scenario']['run_id']}
        assert any(event.get('record_type') == 'span' for event in events)
        spans = [event for event in events if event.get('record_type') == 'span']
        expected = {row['step']: row['record_id'] for row in rows}
        assert all(span['attributes']['step_record_id'] == expected[span['step']] for span in spans)
    assert len({row['record_id'] for row in records}) == 6
    for current in records[1::2]:
        selected = select_baseline(current, list(reversed(records)))
        assert selected['run_id'] == current['run_id']
        assert selected['step'] == 127


def test_live_exporter_keeps_native_engine_counters_and_application_identity(tmp_path, monkeypatch):
    path = tmp_path / 'multi-job.json'
    schedule = make_schedule(start=1000, node='gpu-node-0')
    path.write_text(json.dumps(schedule))
    demo = Demo(ROOT / 'examples/live-demo', multi_job_state=path)
    monkeypatch.setattr('xlayer_telemetry.demos.live.time.time', lambda: 1120)
    app = demo.metrics('gpu-node-0')
    durations = [s for s in app if s.name == 'training_step_time_seconds']
    assert {s.labels['run_id'] for s in durations} == {'demo-qwen', 'demo-llama', 'demo-deepseek'}
    assert all('model' not in s.labels and 'model_name' not in s.labels for s in durations)
    resource = [s for s in app if s.name.startswith(('node_', 'telemetry_gpu_'))]
    assert resource and all('run_id' not in s.labels for s in resource)
    native = {job['instance']: demo.metrics(job['instance']) for job in schedule['jobs']}
    assert all('run_id' not in s.labels for rows in native.values() for s in rows)
    assert all(len({s.labels['model_name'] for s in rows}) == 1 for rows in native.values())
    assert not any(s.name == 'vllm:num_preemptions_total' for s in native['synthetic-job-deepseek'])
    assert not any(s.name == 'reward_mean' and s.labels.get('run_id') == 'demo-llama' for s in app)
    stale = next(s for s in app if s.name == 'training_sample_timestamp_seconds' and s.labels.get('run_id') == 'demo-deepseek' and s.labels.get('role') == 'trainer')
    assert stale.value < 1000
    config = prometheus_config(demo, '127.0.0.1:12345', 'scenes-demo')
    for job in schedule['jobs']:
        assert '/metrics/' + job['instance'] in config
    assert '__metrics_path__: /metrics/vllm\n' not in config
    # A new scheduled pair does not erase completed application observations.
    path.write_text(json.dumps(make_schedule(start=2000, node='gpu-node-0', cycle=1)))
    retained=demo.metrics('gpu-node-0')
    assert {s.labels['run_id'] for s in retained if s.name=='training_step_time_seconds'} == {'demo-qwen','demo-llama','demo-deepseek'}
    old_stamp=next(s.value for s in retained if s.name=='training_sample_timestamp_seconds' and s.labels.get('run_id')=='demo-deepseek' and s.labels.get('role')=='trainer')
    assert old_stamp == stale.value


def test_multi_job_state_validation_precedes_artifact_writes(tmp_path):
    schedule = make_schedule(start=1000, node='gpu-node-0')
    schedule['jobs'][1]['scenario']['run_id'] = schedule['jobs'][0]['scenario']['run_id']
    with pytest.raises(ValueError):
        validate_schedule(schedule)
    with pytest.raises(ValueError):
        Demo(ROOT / 'examples/live-demo', scenario_state=tmp_path/'single', multi_job_state=tmp_path/'multi')


def test_shared_pressure_diagnosis_keeps_entity_without_claiming_the_selected_run():
    class SharedMetrics:
        def query_range_detail(self, query, start, end, step):
            if query.startswith('timestamp('):
                stats = {'max': end - 1}
                return {'aggregate': stats, 'series': [{'stats': stats, 'source_timestamps': [start + 1, end - 1]}]}
            if 'num_requests_waiting' in query: value = 12
            elif 'kv_cache_usage' in query: value = .99
            elif 'num_preemptions' in query: value = 3
            else: return {'aggregate': None, 'series': []}
            stats = {'max': value, 'mean': value, 'sample_count': 5, 'max_series_delta': value}
            return {'aggregate': stats, 'series': [{'labels': {'node': 'gpu-node-0', 'instance': 'other-job-engine'}, 'stats': stats}]}
    current = {'run_id': 'selected-job', 'node': 'gpu-node-0', 'worker_id': 'driver', 'step': 128,
               'analysis_window': {'start': 100, 'end': 120, 'accuracy': 'approximate'}, 'step_duration_seconds': 20}
    report = DiagnosticEngine({'prometheus': {'url': 'http://unused'}, 'sampling': {'check_source_freshness': True}},
        prometheus=SharedMetrics(), clock=lambda: 125).analyze(current, [])
    candidate = next(c for c in report['candidates'] if c['id'] == 'kv_cache_pressure')
    assert candidate['state'] == 'strong_signal', 'Strong observed pressure remains useful without Run ownership'
    assert candidate['resource_attribution'] == 'not_established'
    assert candidate['run_relation'] == 'shared_unverified'
    assert candidate['signal_strength'] == candidate['state']
    assert 'run_resource_attribution_unverified' in candidate['missing_evidence']
    assert {e['labels']['instance'] for e in candidate['evidence']} == {'other-job-engine'}
    assert report['run_id'] == 'selected-job'


def test_same_model_on_different_endpoints_keeps_counter_history_separate():
    demo = Demo(ROOT / 'examples/live-demo')
    labels = {'model_name': 'same-model', 'engine': '0'}
    hot = {'busy': .9, 'tokens': 100, 'waiting': 14}
    normal = {'busy': .1, 'tokens': 100, 'waiting': 0}
    demo._vllm(100, hot, identity='one', labels=labels)
    demo._vllm(100, normal, identity='two', labels=labels)
    a = demo._vllm(110, hot, identity='one', labels=labels)
    b = demo._vllm(110, normal, identity='two', labels=labels)
    assert next(s.value for s in a if s.name == 'vllm:num_preemptions_total') == 2
    assert next(s.value for s in b if s.name == 'vllm:num_preemptions_total') == 0


def test_waiting_reason_is_producer_context_and_not_inferred_for_unsupported_version():
    demo=Demo(ROOT/'examples/live-demo')
    raw=demo._vllm(100,{'busy':.9,'tokens':1,'waiting':14})
    assert not any(s.name=='vllm:num_requests_waiting_by_reason' for s in raw)
    explicit=demo._vllm(102,{'busy':.9,'tokens':1,'waiting':14,'waiting_capacity':10,'waiting_deferred':4})
    reasons=[s for s in explicit if s.name=='vllm:num_requests_waiting_by_reason']
    assert {s.labels['reason']:s.value for s in reasons}=={'capacity':10,'deferred':4}
    assert sum(s.value for s in reasons)==14


def test_configured_endpoint_filters_core_and_profile_queries_without_inventing_unknown_binding():
    jobs = make_schedule(start=1000, node='gpu-node-0')['jobs']
    for job in jobs:
        config = diagnosis_config(job, 'http://unused')
        for query in DiagnosticEngine(config)._queries('scenes-demo').values():
            if 'telemetry_source="vllm",' in query:
                assert ('instance="' + job['instance'] + '"' in query) is job['engine_mapping']
        assert not config.get('threefs')


def test_existing_sdk_shared_directories_preserve_runs_with_identical_worker_identity(tmp_path):
    snapshots, events = tmp_path/'snapshots', tmp_path/'events'
    for run, reward in (('demo-qwen', .7), ('demo-llama', .6), ('demo-deepseek', .5)):
        MetricEmitter(snapshots, run_id=run, node='node', producer='verl', role='trainer', worker_id='driver').emit(
            step=128, samples=[Metric('reward_mean', reward)])
        recorder=EventRecorder(events, CorrelationContext(run_id=run,node='node',producer='verl',role='trainer',worker_id='driver'))
        with recorder.span('rollout.generate', phase='rollout', step=128): pass
    assert len(list(snapshots.glob('*.json'))) == len(list(events.glob('*.jsonl'))) == 3
    rewards=[sample for sample in build_metrics(_iter_snapshots(snapshots)) if sample.name=='reward_mean']
    assert {sample.labels['run_id']:sample.value for sample in rewards} == {'demo-qwen':.7,'demo-llama':.6,'demo-deepseek':.5}


def test_run_engine_relation_uses_explicit_configuration_without_resource_ownership():
    config = {'run_id':'one','cluster':'lab','rollout_node':'node','run_engine_instances':['engine-one']}
    evidence = [{'signal':'vllm_requests_waiting','source':'prometheus','observation_scope':'service',
                 'labels':{'cluster':'lab','node':'node','instance':'engine-one'}}]
    assert resource_run_relation(evidence, config, 'one') == 'configured'
    assert resource_run_relation(evidence, config, 'other') == 'shared_unverified'
    assert resource_run_relation(evidence, {}, 'one') == 'shared_unverified'
    evidence[0]['labels']['instance'] = 'other-engine'
    assert resource_run_relation(evidence, config, 'one') == 'unlinked'
    evidence[0]['signal'] = 'host_cpu_pressure_ratio'
    assert resource_run_relation(evidence, config, 'one') == 'shared_unverified'


@pytest.mark.parametrize('instances', ['engine', [None], ['one','one'], ['x']*17])
def test_invalid_engine_relation_config_is_rejected_before_backend_queries(instances):
    with pytest.raises(ValueError, match='run_engine_instances'):
        DiagnosticEngine({'prometheus':{'url':'http://unused'}, 'run_engine_instances':instances})


def test_reported_model_projection_is_optional_and_keeps_metadata_provenance():
    source={'run_id':'one','analysis_window':{'start':1,'end':2},'run_model':{'identifier':'same-model','source':'run_manifest'}}
    summary=_investigation_rows(source)[0]
    assert summary['run_model_identifier']=='same-model' and summary['run_model_source']=='run_manifest'
    source['run_model']='legacy'
    assert _investigation_rows(source)[0]['run_model_identifier'] is None
