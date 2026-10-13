import {test} from 'node:test';
import assert from 'node:assert/strict';
import {slowSteps,investigationStatus} from '../src/run-health';
import {PRIMARY_PAGES,primaryRoute} from '../src/navigation';
const row={cluster:'c',run_id:'run',observer_node:'observer',node:'node',producer:'p',role:'trainer',worker_id:'w',boundary_scope:'rl_step',record_id:'r',window_start_ms:1000,window_end_ms:2000,step_duration_seconds:2};
test('slow observation ranking never compares different jobs, workers, scopes or unknown boundaries',()=>{
 const slower={...row,record_id:'slow',step_duration_seconds:8};
 const foreign=['cluster','run_id','observer_node','node','producer','role','worker_id','boundary_scope','execution_mode'].map(key=>({...row,[key]:'other',record_id:key,step_duration_seconds:100}));
 const values=[row,slower,...foreign];
 assert.deepEqual(slowSteps(values,row),[slower,row]);
 assert.deepEqual(slowSteps(values),[]);
 assert.deepEqual(slowSteps([{...row,boundary_scope:undefined}]),[]);
 assert.deepEqual(slowSteps([{...row,identity_conflict:true}]),[]);
 assert.deepEqual(slowSteps([{...row,step_duration_seconds:0}]).map(r=>r.step_duration_seconds),[0]);
 assert.deepEqual(slowSteps([{...row,step_duration_seconds:-1}]),[]);
});
test('diagnosis status distinguishes error, missing, withheld and recorded candidates from health',()=>{
 const strong=[{state:'strong_signal'}];
 assert.equal(investigationStatus(strong,'Error','aligned').label,'Diagnosis query failed');
 assert.equal(investigationStatus(strong,'Done','unsafe').label,'Correlation withheld');
 assert.equal(investigationStatus(strong,'Loading','aligned').label,'Loading evidence');
 assert.equal(investigationStatus(strong,'Done','aligned').tone,'attention');
 assert.equal(investigationStatus([{state:'missing_evidence'}],'Done','aligned').label,'Evidence incomplete');
 assert.equal(investigationStatus([],'Done','aligned').label,'No diagnosis recorded');
 assert.equal(investigationStatus([{state:'supporting_signal'}],'Done','aligned').label,'Review recorded signals');
});
test('three workspace areas assign every compatible detail route to a single sidebar entry',()=>{
 assert.deepEqual(PRIMARY_PAGES.map(page=>page.title),['Runs','Infrastructure','Dashboards']);
 for(const route of ['runs','overview','investigate','analyze','timeline','deep-dive','logs'])assert.equal(primaryRoute(route),'runs');
 for(const route of ['dashboards','start-here','storage','compute','stage-correlation','bottleneck-summary','signals'])assert.equal(primaryRoute(route),'dashboards');
 assert.equal(primaryRoute('infrastructure'),'infrastructure');
 assert.equal(primaryRoute('demo'),'demo');
});

test('a completed diagnosis without candidates differs from missing diagnosis or an optional source',()=>{
 assert.equal(investigationStatus([],'Done','aligned',true).label,'No candidates reported');
 assert.equal(investigationStatus([],'Done','aligned',false).label,'No diagnosis recorded');
 assert.equal(investigationStatus([],undefined,undefined,false,false).label,'Diagnosis unavailable');
});
