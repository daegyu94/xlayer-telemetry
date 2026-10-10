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
 assert.equal(calls,0);assert.match(markup,/Selected Resource · gpu-node/);assert.match(markup,/Resource mapping이 없거나 서로 충돌합니다/);
 assert.doesNotMatch(markup,/Cluster Resource Metrics · gpu-node/);
});

test('selected native metrics render outside the map / Inspector grid while navigation receives unchanged investigation context',()=>{
 let received:Context|undefined;
 const markup=renderToStaticMarkup(React.createElement(InfrastructureView,{data:model([node]),context:{...context,variables:{...context.variables,infra_component:[node.key]}},onSelect:()=>{},metrics:()=>React.createElement('span',null,'canonical-scope-metric'),links:(_node,next)=>{received=next;return null;}}));
 assert.ok(received);assert.deepEqual(received.variables.run_id,['run']);assert.deepEqual(received.variables.record_id,['step']);assert.equal(received.from,'1000');
 assert.match(markup,/<\/aside><\/div><section class="xlt-infra-resource-metrics"/,'native chart grid is outside the narrow Inspector');
 assert.match(markup,/canonical-scope-metric/);
});
