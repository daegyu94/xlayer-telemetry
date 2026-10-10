import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {workerPeers} from '../src/mockup';
import {pressureOrder,resourceContext,stepProjection} from '../src/distributed';
import {readContext} from '../src/context';

test('actual veRL stage dimensions join worker Step/freshness without combining stages',()=>{
 const fixture=JSON.parse(readFileSync('tests/fixtures/verl-worker-labels.json','utf8'));
 const base=[0,1,2].map(worker=>({cluster:'c',run_id:'r',node:'n',producer:'verl',role:'rollout',worker_id:String(worker)}));
 const durations=base.map((labels,i)=>({labels:{...labels,...fixture.metric_labels.rl_stage_duration_seconds},time:100,value:i+1,unit:'s'}));
 const steps=base.map(labels=>({labels,time:100,value:10,unit:'short'}));
 const ages=steps.map(s=>({...s,value:0}));
 assert.ok(workerPeers(durations,steps,ages).every(row=>row.peers===3));
 assert.ok(workerPeers([...durations,...durations.map(s=>({...s,labels:{...s.labels,verl_stage:'other'}}))],steps,ages).every(row=>row.peers===3));
 assert.equal(workerPeers(durations,[...steps,{...steps[0],labels:{...steps[0].labels,__name__:'duplicate'}}],ages).find(row=>row.sample.labels.worker_id==='0')?.peers,0);
});

test('old pressure is excluded at range end while measured zero remains',()=>{
 const values=[{labels:{cluster:'c',node:'n',gpu:'0'},time:1000,value:99,unit:'%'},
  {labels:{cluster:'c',node:'n',gpu:'1'},time:99000,value:0,unit:'%'}];
 const options={kind:'higher' as const,endTime:100000,maxAgeMs:5000};
 const rows=pressureOrder(values,options);
 assert.equal(rows.length,1);assert.equal(rows[0].sample.value,0);
 assert.equal(pressureOrder([values[0]],options).length,0);
});

test('resource drill-down pins observed cluster even for identical node/GPU names',()=>{
 const context=readContext('?var-cluster=.*&var-node=n&var-gpu=0');
 for(const cluster of ['a','b'])assert.deepEqual(resourceContext(context,{cluster,node:'n',gpu:'0'}).variables.cluster,[cluster]);
});

test('LLM summary, hypotheses and evidence all come from one invocation',()=>{
 const step={cluster:'c',run_id:'r',record_id:'s',window_start_ms:1000,window_end_ms:2000};
 const a={...step,diagnosis_method:'llm',diagnosis_invocation_id:'a',generated_at:'2026-10-10T00:00:00Z',model:'one'};
 const b={...a,diagnosis_invocation_id:'b',generated_at:'2026-10-10T00:01:00Z',model:'two'};
 const summaries=[{...a,row_kind:'summary'},{...b,row_kind:'summary'}];
 const rows=[{...a,row_kind:'candidate'},{...b,row_kind:'candidate'},{...a,row_kind:'evidence'},{...b,row_kind:'evidence'}];
 assert.deepEqual(stepProjection(rows,step,summaries).map(row=>row.diagnosis_invocation_id),['b','b']);
 const legacy=rows.map(({diagnosis_invocation_id,...row})=>row);
 assert.ok(stepProjection(legacy,step,summaries.map(({diagnosis_invocation_id,...row})=>row)).every(row=>row.model==='two'));
 assert.deepEqual(stepProjection(rows,step,[summaries[0],{...summaries[1],generated_at:a.generated_at}]),[]);
 assert.deepEqual(stepProjection(rows,step,[]),[]);
});
