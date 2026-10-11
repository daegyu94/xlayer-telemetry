import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {reportedDurations,ReportedDurations} from '../src/ReportedDurations';
import {durationComparable,orderCompletedSteps} from '../src/completed-steps';

test('saved durations retain zero and withhold invalid / conflicting records without constructing spans',()=>{
 const row={step:128,worker_id:'worker-a',boundary_scope:'trainer_update',stage_durations_seconds:{rollout:0,actor_update:4,bad:-1,invalid:Infinity}};
 assert.deepEqual(reportedDurations(row),[{phase:'actor_update',seconds:4},{phase:'rollout',seconds:0}]);
 for(const value of ['invalid','[]',null])assert.deepEqual(reportedDurations({...row,stage_durations_seconds:value}),[]);
 assert.deepEqual(reportedDurations({...row,identity_conflict:true}),[]);
 const html=renderToStaticMarkup(React.createElement(ReportedDurations,{row}));
 for(const text of ['0 s','4 s','worker-a','trainer_update','execution'])assert.ok(html.includes(text));
 assert.ok(!html.includes('NaN'));assert.ok(!html.includes('Infinity'));
});

test('duration order requires one declared boundary accuracy and producer / role population',()=>{
 const row={cluster:'c',run_id:'r',record_id:'a',window_start_ms:1000,window_end_ms:2000,boundary_scope:'rl_step',boundary_accuracy:'approximate',producer:'verl',role:'actor',step_duration_seconds:8};
 const recent={...row,record_id:'b',window_start_ms:2000,window_end_ms:3000,step_duration_seconds:2};
 assert.equal(orderCompletedSteps([row,recent],true)[0].record_id,'a');
 assert.equal(orderCompletedSteps([row,recent])[0].record_id,'b');
 for(const change of [{boundary_scope:'trainer_update'},{boundary_accuracy:'exact'},{producer:'wrapper'},{role:'rollout'},{boundary_scope:undefined}]){
  const mixed=[row,{...recent,...change}];assert.equal(durationComparable(mixed),false);assert.equal(orderCompletedSteps(mixed,true)[0].record_id,'b');
 }
});
