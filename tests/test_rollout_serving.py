"""Serving facts require explicit membership, lifecycle and interval coverage."""
import asyncio
import json

import pytest

from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.adapters.rollout import RolloutObserver
from xlayer_telemetry.analysis.rollout_state import load_serving_context
from tests.test_rollout_replicas import INVENTORY, Replicas, records
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine


def observer(root, stamp, *, run='r', node='trainer'):
    recorder=EventRecorder(root, CorrelationContext(run_id=run,node=node,producer='rollout_observer',role='rollout',worker_id='observer'),
                           clock_ns=lambda:int(stamp[0]*1e9))
    return RolloutObserver(recorder,cluster='lab',router_id='router')


def inventory():
    return [{**row,'server_id':'server-'+str(i)} for i,row in enumerate(INVENTORY)]


def config(root):
    return {'run_id':'r','cluster':'lab','node':'trainer','rollout_replicas':inventory(),
        'rollout_observations':{'events_dir':str(root),'router_id':'router','max_age_seconds':60},
        'clock':{'monitoring_node':'monitor'},'prometheus':{'url':'unused'},'sampling':{'check_source_freshness':True}}


def context(root):
    return load_serving_context(config(root),{'start':100,'end':120},
        {'nodes':{'trainer':{'status':'aligned'}}})['replica-0']


def test_router_membership_is_not_serving_and_removed_is_not_down(tmp_path):
    stamp=[99];o=observer(tmp_path,stamp)
    o.router_status({'servers':{'server-1':4},'total_inflight':4,'active_servers':1,'registered_handles':['server-1']})
    row=context(tmp_path)
    assert row['router_registered'] is False and row['inflight_requests'] is None
    assert row['serving_state']=='unknown'
    assert row['status']=='covered'
    assert row['source']=='explicit_sdk_observations'


@pytest.mark.parametrize('failure',['timeout','invalid','exception'])
def test_read_only_router_poll_is_bounded_and_failure_is_unknown(tmp_path,failure):
    o=observer(tmp_path,[99])
    async def getter():
        if failure=='timeout':await asyncio.sleep(1)
        if failure=='exception':raise RuntimeError('private endpoint detail must not leak')
        return {'servers':{'x':-1}}
    assert asyncio.run(o.poll_router(getter,timeout_seconds=.01)) is False
    row=context(tmp_path)
    assert row['router_registered'] is None and row['serving_state']=='unknown'
    assert 'private endpoint' not in next(tmp_path.glob('*.jsonl')).read_text()


def test_sleep_proof_covers_interval_but_mid_step_waking_makes_it_mixed(tmp_path):
    stamp=[99];o=observer(tmp_path,stamp)
    o.replica_state('replica-0','a:8000','sleeping',generation='g1')
    assert context(tmp_path)['serving_state']=='sleeping'
    stamp[0]=110;o.replica_state('replica-0','a:8000','waking',generation='g1')
    assert context(tmp_path)['serving_state']=='unknown'
    assert 'lifecycle_changed_during_interval' in context(tmp_path)['quality_issues']
    assert context(tmp_path)['inactive_observed'] is True


def test_foreign_run_stale_future_and_unknown_clock_cannot_be_lifecycle_proof(tmp_path):
    o=observer(tmp_path,[99],run='other');o.replica_state('replica-0','a:8000','sleeping',generation='g1')
    assert context(tmp_path)['serving_state']=='unknown'
    o=observer(tmp_path,[1]);o.replica_state('replica-0','a:8000','sleeping',generation='g1')
    o=observer(tmp_path,[121]);o.replica_state('replica-0','a:8000','sleeping',generation='g1')
    assert context(tmp_path)['serving_state']=='unknown'
    row=load_serving_context(config(tmp_path),{'start':100,'end':120},{'nodes':{}})['replica-0']
    assert row['serving_state']=='unknown'


def test_observed_inactive_replica_does_not_get_strong_engine_bottleneck(tmp_path):
    now,before=records();stamp=[99];o=observer(tmp_path,stamp)
    o.replica_state('replica-1','b:8000','sleeping',generation='g1')
    result=DiagnosticEngine(config(tmp_path),prometheus=Replicas(missing_preemption=False)).analyze(now,[before])
    candidate=next(c for c in result['candidates'] if c['id']=='rollout_queue_backlog')
    assert candidate['state']=='weak_signal'
    assert candidate['context_status']=='intentional_inactive'
    assert result['rollout_replicas'][1]['signals']['vllm_requests_waiting']['current']==18


def test_generation_and_workload_change_withhold_replica_baseline(tmp_path):
    now,before=records();stamp=[59];o=observer(tmp_path,stamp)
    o.replica_state('replica-1','b:8000','serving',generation='old')
    o.replica_workload('replica-1','b:8000',{'prompt_tokens':256,'tool_calls':0,'concurrency':2},generation='old')
    stamp[0]=99
    o.replica_state('replica-1','b:8000','serving',generation='new')
    o.replica_workload('replica-1','b:8000',{'prompt_tokens':4096,'tool_calls':3,'concurrency':2},generation='new')
    result=DiagnosticEngine(config(tmp_path),prometheus=Replicas()).analyze(now,[before])
    row=result['rollout_replicas'][1]['signals']['vllm_requests_waiting']
    assert row['baseline']==18 and row['delta'] is None
    assert row['comparison_status']=='replica_context_changed'
    assert result['rollout_replicas'][1]['serving_context']['current']['generation']=='new'


def test_native_replica_label_is_part_of_engine_identity():
    from xlayer_telemetry.analysis.diagnosis_analysis import vllm_identity
    a={'labels':{'node':'n','instance':'i','engine':'0','replica':'r0','node_rank':'0'}}
    b={'labels':{**a['labels'],'replica':'r1'}}
    assert vllm_identity(a)!=vllm_identity(b)


def test_old_sleep_or_completed_pre_step_wake_does_not_cap_active_engine(tmp_path):
    stamp=[50];o=observer(tmp_path,stamp)
    o.replica_state('replica-0','a:8000','sleeping',generation='g1')
    assert context(tmp_path)['inactive_observed'] is False
    stamp[0]=99;o.replica_state('replica-0','a:8000','serving',generation='g1')
    assert context(tmp_path)['serving_state']=='serving'
    assert context(tmp_path)['inactive_observed'] is False


def test_worker_applied_policy_is_not_inferred_from_trainer_or_router(tmp_path):
    o=observer(tmp_path,[99])
    assert context(tmp_path)['applied_policy_version'] is None
    o.policy_applied('replica-0','a:8000',0,generation='g1')
    assert context(tmp_path)['applied_policy_version']==0
    assert context(tmp_path)['serving_state']=='unknown'


def test_inactive_replica_on_gpu_node_caps_mixed_starvation_without_hiding_other_engine(tmp_path):
    class GPU(Replicas):
        def query_range_detail(self,query,start,end,step):
            if 'telemetry_gpu_utilization_percent' in query:
                from tests.test_rollout_replicas import stats
                s=stats(20 if start==100 else 90)
                row={'labels':{'cluster':'lab','node':'rollout-a','instance':'rollout-a','gpu':'0'},'stats':s}
                if query.startswith('timestamp('):
                    row.update(stats=stats(end-1),source_timestamps=[start+1,end-1])
                return {'aggregate':s,'series':[row]}
            return super().query_range_detail(query,start,end,step)
    o=observer(tmp_path,[99]);o.replica_state('replica-0','a:8000','sleeping',generation='g1')
    now,before=records();now['step_duration_seconds']=40;now['analysis_window']['end']=140
    settings=config(tmp_path);settings['compute_node']='rollout-a';settings['baseline']={'match_fields':['perf/total_num_tokens']}
    result=DiagnosticEngine(settings,prometheus=GPU()).analyze(now,[before])
    candidate=next(c for c in result['candidates'] if c['id']=='gpu_starvation')
    assert candidate['state']!='strong_signal'
    assert candidate['context_status']=='inactive_replica_on_gpu_node'
    assert any(c['id']=='kv_cache_pressure' for c in result['candidates'])
