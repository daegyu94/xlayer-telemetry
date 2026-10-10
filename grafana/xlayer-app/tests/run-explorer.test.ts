import test from 'node:test';
import assert from 'node:assert/strict';
import {compareRuns,RunEntry,runContext,comparisonLink,liveRuns,mergeRuns} from '../src/run-explorer';
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
