"""Scenario replay contracts: use real SDK/exporter paths, never fabricate health."""
import json
from pathlib import Path

from xlayer_telemetry.demos.scenario import make_scenario, phase_values
from xlayer_telemetry.demos.diagnosis import generate
from xlayer_telemetry.demos.multi_job import make_schedule, record_job


def test_rollout_slowdown_is_independent_of_storage_pressure(tmp_path):
    schedule=make_scenario(start=1000,run_id='demo',node='n',current='slow-rollout')
    before,after=schedule['frames']
    assert after['end']-after['start']>before['end']-before['start']
    assert after['signals']['threefs_p99_latency']==before['signals']['threefs_p99_latency']
    values=phase_values(after,after['phases'][0])
    assert values['waiting']>1 and values['busy']==.31 and values['kv_slow']==0
    report=generate(tmp_path/'run',run_id='demo',node='n',scenario=schedule,clock=lambda:1200)
    assert any(c['id']=='rollout_queue_backlog' for c in report['candidates'])
    assert not any(c['id']=='storage_queue_saturation' for c in report['candidates'])


def test_recorded_error_uses_sdk_status_and_preserves_normal_baseline(tmp_path):
    scenario=make_scenario(start=1000,run_id='demo',node='n',current='recorded-error')
    generate(tmp_path/'run',run_id='demo',node='n',scenario=scenario,clock=lambda:1200)
    records=[json.loads(line) for p in (tmp_path/'run/telemetry-events').glob('*.jsonl') for line in p.read_text().splitlines()]
    errors=[r for r in records if r.get('record_type')=='span' and r.get('status')=='error']
    assert len(errors)==1 and errors[0]['step']==128
    assert errors[0]['attributes']['data_origin']=='synthetic'


def test_partial_telemetry_replay_has_explicit_missing_stale_and_recovery():
    before=make_schedule(start=1000,node='n',profile='partial-telemetry',cycle=0)
    after=make_schedule(start=2000,node='n',profile='partial-telemetry',cycle=1)
    assert any(j['missing_reward'] for j in before['jobs'])
    assert any(j['stale_application'] for j in before['jobs'])
    assert all(not j['missing_reward'] and not j['missing_preemptions'] and not j['stale_application'] for j in after['jobs'])
    assert [j['scenario']['run_id'] for j in before['jobs']]==[j['scenario']['run_id'] for j in after['jobs']]


def test_async_replica_replay_uses_trainer_update_without_claiming_rollout_ownership(tmp_path):
    schedule=make_schedule(start=1000,node='n',replica_nodes=['peer'],profile='async-replicas')
    job=schedule['jobs'][1]
    rows=record_job(tmp_path/'job',job)
    assert all(r['execution_mode']=='async' and r['boundary_scope']=='trainer_update' for r in rows)
    for record,frame in zip(rows,job['scenario']['frames']):
        actor=next(p for p in frame['phases'] if p['phase']=='actor_update')
        assert record['analysis_window']['start']==actor['start']
        assert record['analysis_window']['end']==actor['end']
        assert frame['phases'][0]['end']<=record['analysis_window']['start']
    spans=[json.loads(line) for p in (tmp_path/'job/telemetry-events').glob('*.jsonl') for line in p.read_text().splitlines()]
    rollout=[r for r in spans if r.get('record_type')=='span' and r.get('phase')=='rollout']
    assert rollout and all(r['attributes']['boundary_scope']=='instrumented_call' for r in rollout)
    assert job['rollout_replicas']


def test_explorer_keeps_bounded_real_observation_links_without_private_paths(tmp_path):
    from xlayer_telemetry.demos.journeys import publish_explorer,observation_link
    report={'run_id':'r','node':'n','step':1,'trigger_record_id':'record','analysis_window':{'start':1.0005,'end':2.0005}}
    row=observation_link('normal',report)
    assert row['context']['from']=='1000' and row['context']['to']=='2001'
    for i in range(35):
        value=publish_explorer(tmp_path,selected='normal',cycle=i,status='ready',observations=[row])
    assert len(value['observations'])==28
    assert value['data_origin']=='synthetic'
    assert str(tmp_path) not in (tmp_path/'xlayer-demo-scenarios.json').read_text()


def test_replay_signature_ignores_clocks_ids_but_changes_for_source_faults():
    from xlayer_telemetry.demos.validation import schedule_signature
    a=make_scenario(start=1000,run_id='g1-r',node='n',current='slow-rollout')
    b=make_scenario(start=2000,run_id='g2-r',node='n',current='slow-rollout')
    assert schedule_signature(a)==schedule_signature(b)
    b['frames'][1]['signals']['vllm_requests_waiting']=0
    assert schedule_signature(a)!=schedule_signature(b)
