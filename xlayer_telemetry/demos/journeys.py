"""Bounded demo scenario catalog. Presentation metadata, never diagnosis input."""
from __future__ import annotations

from pathlib import Path
import json
import math

from ..fileio import atomic_write_text
from .validation import EXPECTED

SCENARIOS = (
    {'id':'normal','title':'Normal operation','category':'기본','mode':'single',
     'question':'기록된 실행 진행과 Baseline이 일치하는가?',
     'expected':'같은 경계의 duration과 measured zero를 확인합니다. 후보 부재는 전체 health 보장이 아닙니다.',
     'workflow':['Run','Baseline','Timeline']},
    {'id':'slow-rollout','title':'Slow rollout','category':'성능','mode':'single',
     'question':'Rollout이 느려졌을 때 어디부터 조사할까?',
     'expected':'Rollout duration과 vLLM queue 증가. Storage pressure를 함께 만들지 않습니다.',
     'workflow':['Run','Candidate','Evidence','vLLM']},
    {'id':'storage-regression','title':'Storage correlation','category':'성능','mode':'single',
     'question':'지연과 shared Storage 관측은 어떤 관계인가?',
     'expected':'저장된 service latency와 device pressure의 동시 변화. 경로와 Run 소유 관계는 미확인입니다.',
     'workflow':['Run','Candidate','Evidence','Storage']},
    {'id':'recorded-error','title':'Recorded tool error','category':'오류','mode':'single',
     'question':'실패한 tool 호출이 관측됐는가?',
     'expected':'실제 SDK의 error span 한 개와 정상 baseline. 반환된 span의 오류 수이며 전체 실패율은 아닙니다.',
     'workflow':['Run','Recorded errors','Timeline']},
    {'id':'multi-job','title':'Multi-job shared resources','category':'분산','mode':'multi',
     'question':'같은 자원을 보는 세 Job을 혼동하지 않는가?',
     'expected':'세 Run의 별도 app identity, shared endpoint, replica 및 실제 Prometheus diagnosis.',
     'workflow':['Runs','Run','Phases & workers','Evidence']},
    {'id':'partial-telemetry','title':'Partial telemetry & recovery','category':'복구','mode':'multi',
     'question':'보이지 않는 것과 실제 0을 구분하고 복구를 확인할 수 있는가?',
     'expected':'홀수 번째 replay는 missing/stale, 다음 replay는 source 복구. 과거 diagnosis는 당시 evidence를 유지합니다.',
     'workflow':['Runs','Run','Source coverage','Recovery']},
    {'id':'async-replicas','title':'Async trainer & replicas','category':'분산','mode':'multi',
     'question':'Trainer update와 replica의 관측을 구분하는가?',
     'expected':'명시적 async trainer_update, configured replica·policy/serving 상태. Rollout 호출은 독립 boundary입니다.',
     'workflow':['Runs','Run','Phases & workers','Replica coverage']},
)
for _scenario in SCENARIOS:
    _scenario['expected_results']=EXPECTED[_scenario['id']]
SCENARIO_IDS = tuple(item['id'] for item in SCENARIOS)
BOUNDARY_TESTS = [
    {'id':'query-recovery','title':'Query failure / recovery','runner':'context_recovery_validate.py','layer':'browser response fixture'},
    {'id':'clock-quality','title':'Clock unsafe / unknown','runner':'clock_quality_validate.py','layer':'browser response fixture'},
    {'id':'metric-contract','title':'Missing / stale / entity ambiguity','runner':'metric_contract_validate.py','layer':'browser response fixture + live metrics'},
    {'id':'product-journey','title':'Theme / viewport / navigation / share','runner':'redesign_validate.py','layer':'live SDK + Grafana'},
]


def scenario_definition(name):
    return next(item for item in SCENARIOS if item['id']==name)


def publish_explorer(directory: Path, *, selected: str, cycle: int, status: str, observations=()):
    """Provision public synthetic navigation through Grafana, without another API."""
    if selected not in (*SCENARIO_IDS, 'alternating') or status not in {'collecting','ready'} or not 0<=cycle<=1000000:
        raise ValueError('invalid demo scenario state')
    directory=Path(directory)
    path=directory/'xlayer-demo-scenarios.json'
    old=[]
    if path.exists():
        raw=json.loads(path.read_text())
        old=raw.get('xlayerDemoScenarios',{}).get('observations',[])
    rows=list(observations)
    for row in rows:
        if row.get('scenario') not in SCENARIO_IDS or not isinstance(row.get('context'),dict):
            raise ValueError('invalid demo observation link')
    # Store only source identities and windows, never argv, credentials or host paths.
    catalog={'schema_version':1,'data_origin':'synthetic','selected':selected,'cycle':cycle,'status':status,
             'scenarios':list(SCENARIOS),'boundary_tests':BOUNDARY_TESTS,'observations':(rows+old)[:28],
             'limitations':'Synthetic SDK / native queries. No real weights, GPU, physical multi-node, RDMA or 3FS validation.'}
    dashboard={'uid':'xlayer-demo-scenarios','title':'XLayer Synthetic Scenario Explorer','schemaVersion':39,
               'version':cycle+1,'tags':['xlayer','synthetic'],'panels':[],'links':[],
               'xlayerDemoScenarios':catalog}
    atomic_write_text(path,json.dumps(dashboard,ensure_ascii=False,indent=2)+'\n')
    return catalog


def observation_link(scenario, report):
    window=report['analysis_window']
    return {'scenario':scenario,'step':report['step'],'boundary_scope':report.get('boundary_scope','unknown'),
            'context':{'cluster':'scenes-demo','run_id':report['run_id'],'source_node':report['node'],
                       'record_id':report['trigger_record_id'],'from':str(math.floor(window['start']*1000)),
                       'to':str(math.ceil(window['end']*1000))}}
