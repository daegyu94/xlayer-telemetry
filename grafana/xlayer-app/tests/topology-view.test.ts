import assert from 'node:assert/strict';
import { test } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { InfrastructureView } from '../src/InfrastructureView';
import type { InfrastructureModel } from '../src/InfrastructureView';
import type { Resource } from '../src/infrastructure';
import type { Context } from '../src/context';

const node:Resource={key:'configured',cluster:'c',id:'gpu-node',kind:'compute',role:'gpu-node',resource:'gpu-node',type:'node',mapping:'configured',collector:'unknown'};
const context:Context={variables:{cluster:['c'],run_id:['run'],record_id:['step']},from:'1000',to:'2000',timezone:'browser'};
const model=(nodes:Resource[]):InfrastructureModel=>({nodes,edges:[],limited:false});

test('empty selection renders a keyboard-native resource card and does not query or infer an Inspector resource',()=>{
 let calls=0;
 const markup=renderToStaticMarkup(React.createElement(InfrastructureView,{data:model([node]),context,onSelect:()=>{},metrics:()=>{calls++;return null;},links:()=>{calls++;return null;}}));
 assert.equal(calls,0);assert.match(markup,/aria-label="Inspect gpu-node"/);assert.match(markup,/aria-pressed="false"/);
 assert.match(markup,/Select a resource/);assert.match(markup,/data-state="unknown"/);assert.match(markup,/Collector unknown/);assert.match(markup,/관측된 network hop이 아닙니다/);assert.match(markup,/Map이나 Inventory에서/);
 assert.doesNotMatch(markup,/Selected Resource · gpu-node/);
});

test('unknown mapping keeps identity visible but does not generate resource links or call native metric rendering',()=>{
 let calls=0;const unknown={...node,key:'unknown',mapping:'unknown' as const,resource:undefined};
 const markup=renderToStaticMarkup(React.createElement(InfrastructureView,{data:model([unknown]),context:{...context,variables:{...context.variables,infra_component:['unknown']}},onSelect:()=>{},metrics:()=>{calls++;return null;},links:()=>{calls++;return null;}}));
 assert.equal(calls,0);assert.match(markup,/Resource details/);assert.match(markup,/gpu-node/);assert.match(markup,/Resource mapping이 없어 metric query를 만들 수 없습니다/);
 assert.doesNotMatch(markup,/gpu-node metrics/);
});

test('selected node opens resource-first metrics and keeps topology behind an explicit action',()=>{
 let received:Context|undefined;
 const markup=renderToStaticMarkup(React.createElement(InfrastructureView,{data:model([node]),context:{...context,variables:{...context.variables,infra_component:[node.key]}},onSelect:()=>{},metrics:()=>React.createElement('span',null,'canonical-scope-metric'),links:(_node,next)=>{received=next;return null;}}));
 assert.ok(received);assert.deepEqual(received.variables.run_id,['run']);assert.deepEqual(received.variables.record_id,['step']);assert.equal(received.from,'1000');
 assert.match(markup,/Resource details/);assert.match(markup,/Cluster topology 보기/);
 assert.doesNotMatch(markup,/Configured Resource Topology|aria-label="Resource inspector"/);
 assert.match(markup,/canonical-scope-metric/);
});

test('landing preview keeps selectable resource identities without the detail inspector or inventory',()=>{
 let calls=0;
 const markup=renderToStaticMarkup(React.createElement(InfrastructureView,{preview:true,data:model([node]),context:{...context,variables:{...context.variables,infra_component:[node.key]}},onSelect:()=>{},metrics:()=>{calls++;return null;},links:()=>{calls++;return null;}}));
 assert.equal(calls,0);
 assert.match(markup,/aria-label="Inspect gpu-node"/);
 assert.match(markup,/data-state="unknown"/);
 assert.doesNotMatch(markup,/aria-label="Resource inspector"|Component Inventory|Cluster Resource Metrics/);
});

test('compact topology keeps hidden resources reachable through inventory or the full Systems page',()=>{
 const nodes=Array.from({length:6},(_,i)=>({...node,key:`node-${i}`,id:`node-${i}`}));
 for(const preview of [false,true]){
  const markup=renderToStaticMarkup(React.createElement(InfrastructureView,{preview,data:model(nodes),context,onSelect:()=>{},metrics:()=>null,links:()=>null}));
  assert.equal((markup.match(/class="xlt-cluster-map-node"/g)||[]).length,4);
  assert.doesNotMatch(markup,/Topology density|>Expanded<\/button>|>Compact<\/button>/);
  if(preview){
   assert.match(markup,/Systems에서 찾기/);
   const href=markup.match(/class="xlt-cluster-map-more" href="([^"]+)"/)![1].replace(/&amp;/g,'&');
   const url=new URL(href,'http://local');assert.equal(url.pathname,'/a/xlayer-telemetry-app/infrastructure');
   assert.equal(url.searchParams.get('var-run_id'),'run');assert.equal(url.searchParams.get('var-record_id'),'step');
  }else{
   assert.match(markup,/Inventory에서 찾기/);
   assert.equal((markup.match(/Resource 선택 →/g)||[]).length,6);
  }
 }
});
