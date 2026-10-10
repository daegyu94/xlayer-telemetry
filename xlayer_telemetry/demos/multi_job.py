"""Bounded concurrent producer inputs; no scenario answers enter diagnosis."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

from .scenario import frame_at, make_scenario, phase_values, validate_scenario
from .diagnosis import _record_scenario_spans
from ..events import CorrelationContext, EventRecorder
from ..fileio import atomic_write_text
from ..manifest import make_agent_rl_manifest, write_manifest
from ..measurements import finite_number
from ..step_history import StepHistoryWriter


MODELS = (
    ('qwen', 'Qwen/Qwen2.5-7B-Instruct'),
    ('llama', 'meta-llama/Llama-3.1-8B-Instruct'),
    ('deepseek', 'deepseek-ai/DeepSeek-R1-Distill-Qwen-7B'),
)


def make_schedule(*, start, node, cycle=0, replica_nodes=None):
    jobs = []
    for index, (name, model) in enumerate(MODELS):
        scenario = make_scenario(start=start + 3 * index, run_id='demo-' + name, node=node,
                                 step=127 + cycle * 2, current='normal')
        for offset, frame in enumerate(scenario['frames']):
            frame['policy_version'] = 128 + index * 16 + cycle * 2 + offset
        frame = scenario['frames'][1]
        cursor = frame['start']
        for phase in frame['phases']:
            seconds = phase['end'] - phase['start']
            if name == 'llama' and phase['phase'] == 'rollout': seconds += 12
            if name == 'deepseek' and phase['phase'] == 'actor_update': seconds += 12
            if name == 'deepseek' and phase['phase'] == 'checkpoint_save': seconds += 6
            phase.update(start=cursor, end=cursor + seconds)
            cursor += seconds
        frame['end'] = cursor
        frame['signals']['step_duration_seconds'] = cursor - frame['start']
        frame['signals']['rollout_duration_seconds'] = frame['phases'][0]['end'] - frame['start']
        jobs.append({'scenario': scenario, 'model': model, 'instance': 'synthetic-job-' + name,
                     'missing_reward': name == 'llama', 'missing_preemptions': name == 'deepseek',
                     'stale_application': name == 'deepseek', 'engine_mapping': name != 'deepseek',
                     'reward': (.732, .614, .681)[index]})
    if replica_nodes:
        jobs[1]['rollout_replicas'] = [
            {'id': 'llama-0', 'instance': jobs[1]['instance'], 'endpoint_node': node, 'nodes': [node], 'server_id':'api-llama-0'},
            {'id': 'llama-1', 'instance': 'synthetic-job-llama-peer', 'endpoint_node': replica_nodes[0], 'nodes': list(replica_nodes), 'server_id':'api-llama-1'},
        ]
    return validate_schedule({'schema_version': 1, 'data_origin': 'synthetic', 'jobs': jobs})


def validate_schedule(schedule):
    if not isinstance(schedule, dict) or schedule.get('schema_version') != 1 or schedule.get('data_origin') != 'synthetic':
        raise ValueError('invalid multi-job schedule')
    jobs = schedule.get('jobs')
    if not isinstance(jobs, list) or len(jobs) != 3:
        raise ValueError('multi-job demo requires three jobs')
    runs, instances = set(), set()
    for job in jobs:
        if not isinstance(job, dict): raise ValueError('invalid demo job')
        scenario = validate_scenario(job.get('scenario'))
        run = scenario['run_id']
        instance = job.get('instance')
        if (not isinstance(instance, str) or run in runs or instance in instances
                or not instance.startswith('synthetic-job-') or not instance.replace('-', '').isalnum()
                or len(instance) > 64 or not isinstance(job.get('model'), str) or not 1 <= len(job['model']) <= 128):
            raise ValueError('invalid or duplicate job identity')
        if any(type(job.get(key)) is not bool for key in ('missing_reward', 'missing_preemptions', 'stale_application', 'engine_mapping')):
            raise ValueError('invalid job source quality')
        if finite_number(job.get('reward')) is None or not 0 <= job['reward'] <= 1:
            raise ValueError('invalid synthetic reward')
        runs.add(run); instances.add(instance)
        if job.get('rollout_replicas'):
            from ..analysis.rollout_replicas import validate_rollout_replicas
            validate_rollout_replicas({'run_id': run, 'cluster': 'scenes-demo', 'rollout_replicas': job['rollout_replicas']})
    return schedule


def load_schedule(path):
    with Path(path).open('rb') as stream: raw = stream.read(65537)
    if len(raw) > 65536: raise ValueError('multi-job state exceeds size limit')
    return validate_schedule(json.loads(raw))


def resource_values(schedule, when):
    """Node/service input pressure is shared, with no run_id or ownership edge."""
    value = {'gpu': 91, 'tokens': 7600, 'step': 0, 'read': .5, 'write': .2, 'rx': 180, 'tx': 150,
             'busy': .1, 'waiting': 0, 'kv_hit': .78, 'kv_slow': 0, 'host_pressure': .02}
    for job in schedule['jobs']:
        active = frame_at(job['scenario'], when)
        if active is None or active[0] is not job['scenario']['frames'][1]: continue
        phase = active[1]['phase']
        if job['instance'].endswith('llama') and phase == 'rollout':
            value.update(busy=.96, read=6, rx=320, kv_slow=1, kv_hit=.54)
        if job['instance'].endswith('deepseek') and phase == 'actor_update':
            value.update(gpu=98, host_pressure=.95)
        if phase == 'checkpoint_save': value.update(write=5, busy=.96, tx=330)
    return value


def native_values(job, when, *, replica_instance=None):
    active = frame_at(job['scenario'], when)
    if active is None:
        return {'gpu': 8, 'tokens': 0, 'busy': .05, 'waiting': 0, 'kv_hit': .78, 'kv_slow': 0}
    frame, phase = active
    value = phase_values(frame, phase)
    value.update(waiting=0, waiting_capacity=0, waiting_deferred=0)
    if frame is job['scenario']['frames'][1] and job['instance'].endswith('llama') and phase['phase'] == 'rollout':
        value.update(waiting=14, waiting_capacity=10, waiting_deferred=4, kv_hit=.54, kv_slow=1)
    if job.get('rollout_replicas'):
        if replica_instance == 'synthetic-job-llama-peer':
            value['kv_util'] = .98 if value['waiting'] else .45
        else:
            value.update(waiting=0, waiting_capacity=0, waiting_deferred=0, kv_hit=.78, kv_slow=0)
    return value


def diagnosis_config(job, prometheus_url, *, events_dir=None):
    """Use canonical queries; a configured endpoint is not operation ownership."""
    from ..analysis.diagnostics import DiagnosticEngine
    node = job['scenario']['node']
    config = {'cluster': 'scenes-demo', 'run_id': job['scenario']['run_id'], 'node': node, 'rollout_node': node,
        'clock': {'monitoring_node': node}, 'sampling': {'check_source_freshness': True},
        'baseline': {'match_fields': ['perf/total_num_tokens']},
        'prometheus': {'url': prometheus_url, 'metric_profiles': ['host', 'vllm', 'vllm_waiting', 'mooncake', 'mooncake_storage']}}
    if job.get('rollout_replicas'):
        config['rollout_replicas'] = job['rollout_replicas']
        if events_dir is not None:
            config['rollout_observations'] = {'events_dir':str(events_dir),'router_id':'demo-router','max_age_seconds':90}
    elif job['engine_mapping']:
        config['run_engine_instances'] = [job['instance']]
        source = 'telemetry_source="vllm",'
        config['prometheus']['queries'] = {name: query.replace(source, source + 'instance="' + job['instance'] + '",')
            for name, query in DiagnosticEngine(config)._queries('scenes-demo').items() if source in query}
    return config


def record_job(root, job):
    """Record completed SDK/VERL inputs; caller must query/analyze them separately."""
    scenario = validate_scenario(job['scenario'])
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    writer = StepHistoryWriter(root / 'telemetry-events/verl-steps.jsonl', run_id=scenario['run_id'],
        node=scenario['node'], worker_id='driver', clock=iter(f['end'] for f in scenario['frames']).__next__)
    rows = []
    for frame in scenario['frames']:
        data = {'perf/time_per_step': frame['end'] - frame['start'], 'policy_version': frame['policy_version'],
                'perf/total_num_tokens': scenario['workload']['batch_size'] * scenario['workload']['response_tokens']}
        data.update({'timing_s/' + ('gen' if p['phase'] == 'rollout' else p['operation']): p['end'] - p['start'] for p in frame['phases']})
        if job['instance'].endswith('deepseek'):
            data.update({'separate_async/decision/sampleable_count':12.0,'separate_async/decision/remaining':20.0,
                         'separate_async/decision/should_switch_to_rollout':1.0,
                         'separate_async/decision/effective_switch_cost_seconds':.3})
        rows.append(writer.append({'step': frame['step'], 'data': data}))
    if job.get('rollout_replicas'):
        record_serving_inputs(root / 'telemetry-events', job)
    _record_scenario_spans(root, scenario, step_record_ids={row['step']: row['record_id'] for row in rows})
    roles = [('trainer', scenario['node'])]
    if job.get('rollout_replicas'):
        roles += [('rollout', node) for node in sorted({node for row in job['rollout_replicas'] for node in row['nodes']})]
    mode = rows[0]['execution_mode']
    fingerprint = hashlib.sha256(json.dumps([job['model'], mode, scenario['workload']],
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    manifest = make_agent_rl_manifest(run_id=scenario['run_id'], roles=roles, configuration={
        'cluster': 'scenes-demo', 'observer_node': scenario['node'], 'model_identifier': job['model'],
        'execution_mode': mode, 'workload_fingerprint': 'synthetic-scenario-' + fingerprint,
        'data_origin': 'synthetic'})
    if job.get('rollout_replicas'):
        manifest['deployment']['rollout_replicas'] = job['rollout_replicas']
    manifest.update(data_origin='synthetic', model={'identifier': job['model'], 'weights_loaded': False},
                    resource_attribution='not_established', native_endpoint_relation='configured' if job['engine_mapping'] else 'unverified')
    write_manifest(root / 'telemetry-manifest.json', manifest)
    recorder = EventRecorder(root / 'telemetry-events', CorrelationContext(run_id=scenario['run_id'],
        node=scenario['node'], producer='multi_job_demo', role='trainer', worker_id='driver'),
        clock_ns=lambda: int(scenario['frames'][1]['end'] * 1e9))
    recorder.event('run.metadata', phase='metadata', step=rows[-1]['step'], attributes={'model': job['model'], 'data_origin': 'synthetic',
        'resource_attribution': 'not_established', 'step_record_id': rows[-1]['record_id']})
    atomic_write_text(root / 'logs/agent.log', json.dumps({'run_id': scenario['run_id'], 'model': job['model'],
        'message': 'Synthetic completed workload; shared resource ownership is not established.'}) + '\n')
    return rows


def record_serving_inputs(directory, job):
    """Mock control-plane facts only; diagnosis consumes them through SDK JSONL."""
    import asyncio
    from ..adapters.rollout import RolloutObserver
    stamp = [0]
    recorder = EventRecorder(directory, CorrelationContext(run_id=job['scenario']['run_id'],
        node=job['scenario']['node'], producer='demo_router', role='rollout', worker_id='observer'),
        clock_ns=lambda: int(stamp[0]*1e9))
    observer = RolloutObserver(recorder, cluster='scenes-demo', router_id='demo-router')
    class MockRouter:
        def __init__(self, servers): self.servers = servers
        async def get_status(self):
            return {'servers':self.servers,'total_inflight':sum(self.servers.values()),
                    'active_servers':len(self.servers),'registered_handles':list(self.servers)}
    for index,frame in enumerate(job['scenario']['frames']):
        stamp[0] = frame['start'] - 1
        router = MockRouter({'api-llama-1':10} if index else {'api-llama-0':0,'api-llama-1':0})
        asyncio.run(observer.poll_router(router.get_status))
        for replica in job['rollout_replicas']:
            primary = replica['id']=='llama-0'; generation='demo-generation-0'
            observer.replica_state(replica['id'], replica['instance'], 'weight_update' if primary and index else 'serving', generation=generation)
            stamp[0] += .01
            if primary and index:
                observer.replica_state(replica['id'], replica['instance'], 'sleeping', generation=generation)
            observer.replica_workload(replica['id'], replica['instance'],
                {'prompt_tokens':256 if primary else 4096,'turns':1 if primary else 4,'tool_calls':0 if primary else 3,
                 'concurrency':2 if primary else 10}, generation=generation)
            if not primary:
                worker_recorder = EventRecorder(directory, CorrelationContext(run_id=job['scenario']['run_id'],
                    node=replica['endpoint_node'],producer='demo_rollout',role='rollout',worker_id=replica['id']),
                    clock_ns=lambda:int(stamp[0]*1e9))
                worker = RolloutObserver(worker_recorder,cluster='scenes-demo',router_id='demo-router')
                worker.policy_applied(replica['id'], replica['instance'], frame['policy_version']-1, generation=generation)
