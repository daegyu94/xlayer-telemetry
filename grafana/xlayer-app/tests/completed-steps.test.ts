import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {completedStepKey,completedStepChoices,requestedStep,CompletedStepSelect,CompletedStepsTable} from '../src/completed-steps';
import {readContext,selectStep} from '../src/context';
const a={cluster:'cluster-a',run_id:'same-run',observer_node:'observer',node:'node',worker_id:'0',
 record_id:'same-record',step:128,window_start_ms:1000,window_end_ms:2000,step_duration_seconds:1,boundary_accuracy:'approximate'};
const b={...a,cluster:'cluster-b',window_start_ms:3000,window_end_ms:4000};

test('completed observations cannot collapse across clusters, observers or windows',()=>{
 const rows=completedStepChoices([a,b,a]);assert.equal(rows.length,2);
 for(const other of [b,{...a,observer_node:'other'},{...a,window_end_ms:2500},{...a,run_id:'other'}]){
  assert.notEqual(completedStepKey(a),completedStepKey(other));
  assert.equal(completedStepChoices([a,other]).length,2);
 }
 assert.equal(completedStepKey({...a,observer_node:undefined}),completedStepKey({...a,observer_node:'node'}));
});
test('each selection pins its cluster/observer/window; ambiguous legacy links stay unselected',()=>{
 const context=readContext('?var-cluster=.*&var-run_id=same-run&var-record_id=same-record&from=now-1h&to=now');
 assert.equal(requestedStep([a,b],context),undefined);
 for(const row of [a,b]){
  const selected=selectStep(row,context);
  assert.equal(requestedStep([a,b],selected),row);
  assert.deepEqual(selected.variables.cluster,[row.cluster]);assert.deepEqual(selected.variables.source_node,['observer']);
  assert.equal(selected.from,String(row.window_start_ms));assert.equal(selected.to,String(row.window_end_ms));
 }
 const repeated={...a,window_start_ms:3000,window_end_ms:4000};
 assert.equal(requestedStep([a,repeated],selectStep(repeated,context)),repeated);
 const iso=selectStep(repeated,context);iso.from=new Date(3000).toISOString();iso.to=new Date(4000).toISOString();
 assert.equal(requestedStep([a,repeated],iso),repeated);
});
test('actual table and dropdown render both clusters and select only the full identity',()=>{
 const context=readContext('');const label=(row:any)=>`Step ${row.step} · 1 s`;
 const select=renderToStaticMarkup(React.createElement(CompletedStepSelect,{rows:[a,b],selected:a,label:'Step',optionLabel:label,onStep:()=>{}}));
 assert.ok(select.includes('cluster-a'));assert.ok(select.includes('cluster-b'));
 assert.equal((select.match(/<option /g)||[]).length,3);assert.equal((select.match(/selected=""/g)||[]).length,1);
 const table=renderToStaticMarkup(React.createElement(CompletedStepsTable,{rows:[a,b],selected:a,context,onSelect:()=>{},format:()=> '1 s'}));
 assert.ok(table.includes('cluster-a'));assert.ok(table.includes('cluster-b'));
 assert.equal((table.match(/aria-selected="true"/g)||[]).length,1);
});

test('malformed windows are withheld and observer-qualified legacy URLs remain usable',()=>{
 for(const change of [{window_start_ms:undefined},{window_end_ms:Infinity},{window_end_ms:1e24},{window_end_ms:1000},{identity_conflict:true}]){
  assert.deepEqual(completedStepChoices([{...a,...change}]),[]);
 }
 const other={...a,observer_node:'other'};
 assert.equal(requestedStep([a,other],readContext('?var-record_id=same-record&var-cluster=cluster-a&var-run_id=same-run&var-source_node=other')),other);
 assert.equal(requestedStep([a,other],readContext('?var-record_id=same-record&var-cluster=cluster-a&var-run_id=same-run')),undefined);
});
