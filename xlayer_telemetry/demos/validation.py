"""Executable, bounded expectations for the product's curated demo scenarios."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from ..fileio import atomic_write_text

EXPECTED = {
 'normal':{'duration':40,'candidate':None,'composition':['sync','baseline/current']},
 'slow-rollout':{'duration':48,'candidate':'rollout_queue_backlog','composition':['rollout delay','vLLM queue','worker straggler']},
 'storage-regression':{'duration':48,'candidate':'storage_queue_saturation','composition':['rollout delay','shared storage pressure','missing path proof','worker straggler']},
 'recorded-error':{'duration':40,'candidate':None,'errors':1,'composition':['normal baseline','one SDK tool failure']},
 'multi-job':{'runs':3,'composition':['shared resources','replica imbalance','missing reward','stale producer']},
 'partial-telemetry':{'runs':3,'composition':['multi-job','missing/stale','source recovery']},
 'async-replicas':{'runs':3,'boundary':'trainer_update','composition':['async updates','independent rollout','replica lifecycle','partial source']},
}


def stage(directory, name, state, checks=(), message=''):
    path=Path(directory)/'validation-state.json'
    value=json.loads(path.read_text()) if path.exists() else {}
    value[name]={'state':state,'checks':list(checks),'message':message}
    atomic_write_text(path,json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def schedule_signature(schedule):
    """Ignore changing timestamps/IDs; preserve workload, relative times and inputs."""
    jobs=schedule.get('jobs') or [{'scenario':schedule}]
    normalized=[]
    for job in jobs:
        scenario=job['scenario'];start=scenario['frames'][0]['start']
        normalized.append({'workload':scenario['workload'],'mode':scenario.get('execution_mode','sync'),
            'source_quality':{k:job[k] for k in ('missing_reward','missing_preemptions','stale_application') if k in job},
            'frames':[{'scenario':f['scenario'],'start':round(f['start']-start,6),'end':round(f['end']-start,6),
                       'phases':[(p['phase'],round(p['end']-p['start'],6)) for p in f['phases']],
                       'signals':f['signals']} for f in scenario['frames']]})
    return hashlib.sha256(json.dumps(normalized,sort_keys=True).encode()).hexdigest()


def check_query(report):
    query=report.get('query_execution',{})
    sources=query.get('sources',{})
    attempted=sum(s.get('attempted',0) for s in sources.values())
    failed=sum(s.get('failed',0) for s in sources.values())
    if not attempted or failed or query.get('budget_exhausted'):
        raise ValueError(f'Native query validation: attempted={attempted}, failed={failed}, budget_exhausted={query.get("budget_exhausted")}')
    return [f'Actual backend queries: {attempted} attempted, {failed} failed; optional missing values are preserved']


def check_diagnosis(name,report):
    expected=EXPECTED[name];checks=[]
    if 'duration' in expected:
        window=report['analysis_window']
        if abs(window['end']-window['start']-expected['duration'])>.002:raise ValueError('Scenario duration differs from expected result')
        candidates=report.get('candidates',[])
        if expected['candidate'] and not any(c['id']==expected['candidate'] for c in candidates):raise ValueError('Expected candidate absent')
        if expected['candidate'] is None and candidates:raise ValueError('Unexpected candidate for normal/error-only input')
        if name=='slow-rollout' and any(c['id']=='storage_queue_saturation' for c in candidates):raise ValueError('Unrelated storage pressure was fabricated')
        checks.append(f"Scenario rule result: duration {expected['duration']}s; candidate {expected['candidate'] or 'none'} (controlled producer inputs)")
    else:
        if report.get('analysis_execution',{}).get('status')!='completed':raise ValueError('Actual diagnosis did not complete')
        if name=='async-replicas' and report.get('boundary_scope')!='trainer_update':raise ValueError('Async boundary changed')
        checks.append('Actual DiagnosticEngine completed; shared resource evidence retains uncertainty')
    return checks


def check_live_queue(schedule,prometheus,request):
    from urllib.parse import urlencode
    frame=schedule['frames'][1];rollout=frame['phases'][0]
    seconds=round(rollout['end']-rollout['start'])
    expression=f'max_over_time(vllm:num_requests_waiting{{instance="synthetic-vllm-0"}}[{seconds}s])'
    raw=json.loads(request(prometheus+'/api/v1/query?'+urlencode({'query':expression,'time':rollout['end']})))
    values=[float(row['value'][1]) for row in raw.get('data',{}).get('result',[])]
    expected=frame['signals']['vllm_requests_waiting']
    if raw.get('status')!='success' or not values or any(value!=expected for value in values):
        raise ValueError(f'Actual vLLM queue query differs from producer input: expected {expected}, observed {values}')
    return [f'Actual scraped vLLM queue max {expected}; queried within the scheduled rollout window']


def check_multi_quality(schedule,prometheus,request):
    """Compare source faults with actual live series, independently of diagnosis."""
    from urllib.parse import urlencode
    checks=[]
    def query(expression):
        result=json.loads(request(prometheus+'/api/v1/query?'+urlencode({'query':expression})))
        if result.get('status')!='success':raise ValueError('Source quality query failed')
        return result.get('data',{}).get('result',[])
    for job in schedule['jobs']:
        run=job['scenario']['run_id'];labels=f'run_id={json.dumps(run)},worker_id="driver"'
        reward=query('reward_mean{'+labels+'}')
        if bool(reward)==job['missing_reward']:raise ValueError(f'{run}: actual reward presence differs from source fault input')
        ages=query('time() - training_sample_timestamp_seconds{'+labels+'}')
        if not ages:raise ValueError(f'{run}: producer age missing')
        stale=all(float(row['value'][1])>300 for row in ages)
        fresh=all(0<=float(row['value'][1])<90 for row in ages)
        if job['stale_application'] and not stale or not job['stale_application'] and not fresh:
            raise ValueError(f'{run}: actual freshness differs from input')
        checks.append(f"{run}: reward {'missing' if job['missing_reward'] else 'observed'}, producer {'stale' if stale else 'fresh'}; actual Prometheus series")
    return checks
