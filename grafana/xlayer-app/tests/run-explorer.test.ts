import test from 'node:test';
import assert from 'node:assert/strict';
import {compareRuns,RunEntry,runContext,resourceContext,comparisonLink,liveRuns,mergeRuns,readRunFilters,withRunFilters} from '../src/run-explorer';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {RunComparisonMetric} from '../src/RunComparisonMetric';
import {readContext} from '../src/context';
import {readFileSync} from 'node:fs';
const run=(key:string,value=10):RunEntry=>({key,run_id:key,model:'Qwen',cluster:'lab',execution_mode:'sync',data_origin:'synthetic',fingerprint:'same',quality:'complete',steps:2,status:'reported_completed',source:'stored_artifact',from:100000,to:120000,observer_node:'gpu-a',
 metrics:[{metric:'step_duration_seconds',value,unit:'s',scope:'application',entity:{node:'gpu-a',worker_id:'driver',boundary_scope:'rl_step'},statistic:'mean_recorded_duration',accuracy:'approximate',quality:'complete',workload_observed:true,workload_shapes:['tokens=100']}],selected_step:{record_id:key+'-step',window_start_ms:110000,window_end_ms:120000}});
test('same recorded cohort compares; unknown fingerprint, entity, sampling, clock and model withhold deltas',()=>{
 const a=run('a'),b=run('b',15);assert.equal(compareRuns(a,b).metrics[0].delta_percent,50);
 assert.equal(compareRuns(a,{...b,fingerprint:undefined}).comparability,'Partial');
 assert.equal(compareRuns(a,{...b,model:'Llama'}).comparability,'Incomparable');
 for(const quality of ['clock_unknown','sampling_unknown','partial'])assert.equal(compareRuns(a,{...b,metrics:[{...b.metrics[0],quality}]}).metrics[0].delta_percent,undefined);
 assert.equal(compareRuns(a,{...b,metrics:[{...b.metrics[0],entity:{node:'other'}}]}).metrics[0].delta_percent,undefined);
 assert.equal(compareRuns(run('a',0),b).metrics[0].delta_percent,undefined);
});
test('share and drill-down preserve time/node/theme and exact run identity while resetting previous step context',()=>{
 const context=readContext('?var-run_id=previous&var-node=resource&var-candidate_id=old&theme=dark&from=100&to=200');
 const a=run('a');const selected=runContext(a,context,true);
 assert.deepEqual(selected.variables.run_id,['a']);assert.deepEqual(selected.variables.source_node,['gpu-a']);assert.equal(selected.from,'110000');
 assert.equal(selected.theme,'dark');assert.deepEqual(selected.variables.candidate_id,[]);
 const url=comparisonLink(a,run('b'),context);assert.ok(url.includes('var-compare_run_a=a'));assert.ok(url.includes('theme=dark'));
});
test('Live retained observations do not invent model/status or mix workers into a run average',()=>{
 const rows=liveRuns([{run_id:'a',cluster:'lab',node:'gpu-a',worker_id:'0',record_id:'1',step_duration_seconds:0},{run_id:'a',cluster:'lab',node:'gpu-a',worker_id:'1',record_id:'2',step_duration_seconds:10}]);
 assert.equal(rows[0].average_step,undefined);assert.equal(rows[0].status,'unknown');assert.equal(rows[0].model,undefined);
 const stored=run('a');assert.equal(mergeRuns([stored],rows).length,1);assert.equal(mergeRuns([stored],rows)[0].live_records,2);
});
test('Python and frontend consume the same recorded comparison contract',()=>{
 const fixture=JSON.parse(readFileSync('tests/fixtures/run-comparison.json','utf8'));
 const result=compareRuns(fixture.a,fixture.b);
 const metrics=result.metrics.map(row=>({metric:row.metric,a:row.a??null,b:row.b??null,delta:row.delta??null,delta_percent:row.delta_percent??null,reasons:row.reasons}));
 assert.deepEqual({comparability:result.comparability,reasons:result.reasons,metrics},fixture.expected);
});
test('rendered comparison values retain individual units, including B-only and missing units',()=>{
 const a=run('a',1),b=run('b',1000);
 b.metrics[0].unit='ms';
 const context=readContext('');
 const row=compareRuns(a,b).metrics[0];
 const render=()=>renderToStaticMarkup(React.createElement('table',null,React.createElement('tbody',null,
  React.createElement(RunComparisonMetric,{row,a,b,context}))));
 assert.ok(render().includes('1 s'));assert.ok(render().includes('1,000 ms'));
 assert.equal(row.delta,undefined);assert.ok(row.reasons.includes('unit_mismatch'));
 a.metrics=[];
 const only=compareRuns(a,b).metrics[0];assert.equal(only.a_unit,undefined);assert.equal(only.b_unit,'ms');
 b.metrics[0].unit=undefined;assert.equal(compareRuns(run('a'),b).metrics[0].b_unit,undefined);
});
test('resource links use recorded periodic window/entity, never saved Step or stale filters',()=>{
 const a=run('a'),b=run('b');
 const observation={analysis_window:{start:200,end:260,accuracy:'sampled'},trigger:'periodic',node:'monitor',generated_at:'2026-10-10T00:05:00Z'};
 const context=readContext('?theme=dark&var-record_id=old&var-candidate_id=old&var-node=old&var-device=old&var-worker=old&var-engine=old');
 const resource={metric:'gpu_utilization',value:0,scope:'node/device',unit:'%',quality:'clock_unknown',entity:{node:'gpu-b',gpu:'0'},observation_context:observation};
 const selected=resourceContext(a,observation,resource.entity,context)!;
 assert.equal(selected.from,'200000');assert.equal(selected.to,'260000');assert.equal(selected.theme,'dark');
 assert.deepEqual(selected.variables.record_id,[]);assert.deepEqual(selected.variables.source_node,['monitor']);
 assert.deepEqual(selected.variables.node,['gpu-b']);assert.deepEqual(selected.variables.gpu,['0']);
 assert.deepEqual(selected.variables.worker,[]);assert.deepEqual(selected.variables.device,[]);
 assert.equal(resourceContext(a,undefined,resource.entity,context),undefined);
 assert.equal(resourceContext(a,{analysis_window:{start:260,end:200}},resource.entity,context),undefined);
 a.metrics=[resource];b.metrics=[{...resource,value:10}];
 const row=compareRuns(a,b).metrics[0];
 const html=renderToStaticMarkup(React.createElement('table',null,React.createElement('tbody',null,
  React.createElement(RunComparisonMetric,{row,a,b,context}))));
 assert.ok(html.includes('from=200000'));assert.ok(html.includes('to=260000'));assert.ok(html.includes('periodic'));
 assert.ok(!html.includes('a-step'));
});

test('Explorer filters survive share, Evidence context and URL reload without altering comparison selection',()=>{
 const context=readContext('?var-cluster=lab&var-compare_run_a=a&var-compare_run_b=b&theme=dark&from=100&to=200');
 const filtered=withRunFilters(context,{search:'Qwen / run',model:'Qwen',status:'reported_completed',source:'stored_artifact',limitTime:true});
 const restored=readContext(new URL(comparisonLink(run('a'),run('b'),filtered),'http://local').search);
 assert.deepEqual(readRunFilters(restored),{search:'Qwen / run',model:'Qwen',status:'reported_completed',source:'stored_artifact',limitTime:true});
 assert.deepEqual(restored.variables.compare_run_a,['a']);assert.deepEqual(restored.variables.compare_run_b,['b']);
 assert.equal(restored.theme,'dark');assert.equal(restored.from,'100');
 assert.deepEqual(readRunFilters(runContext(run('a'),restored,true)),readRunFilters(restored));
 const cleared=withRunFilters(restored,{search:'',model:'',status:'',source:'',limitTime:false});
 assert.equal(readRunFilters(cleared).limitTime,false);assert.equal(readRunFilters(cleared).source,'');
});
