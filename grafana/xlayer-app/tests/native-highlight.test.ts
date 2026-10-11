import {test} from 'node:test';
import assert from 'node:assert/strict';
import type {PanelData} from '@grafana/data';
import {nativeHighlight} from '../src/native-highlight';
const frame=(values:any[])=>({name:'source',refId:'A',length:1,fields:[{name:'Time',type:'time',config:{},values:[1000]},...values.map(([name,value,labels])=>({name,type:'number',config:{unit:'short'},values:[value],labels}))]});
const data=(frames:any[],state='Done')=>({state,series:frames,timeRange:{to:{valueOf:()=>1000}}} as unknown as PanelData);
test('native highlights keep measured zero, query error and unavailable distinct',()=>{
 assert.equal(nativeHighlight(data([frame([['queue',0,{node:'n'}]])])).sample?.value,0);
 assert.equal(nativeHighlight(data([])).state,'no-data');assert.equal(nativeHighlight(undefined).state,'loading');
 assert.equal(nativeHighlight(data([frame([['queue',8,{node:'n'}]])],'Error')).state,'error');
});
test('fields and query populations sharing node labels are never one representative KPI',()=>{
 const result=nativeHighlight(data([frame([['read',10,{node:'n'}],['write',2,{node:'n'}]])]));assert.equal(result.state,'multiple');assert.equal(result.entities.length,2);assert.equal(result.sample,undefined);
 const second={...frame([['read',4,{node:'n'}]]),refId:'B'};
 assert.equal(nativeHighlight(data([frame([['read',10,{node:'n'}]]),second])).state,'multiple');
});

test('multi-signal cards split read/write queries and preserve entity selection inside each',async()=>{
 const {nativeHighlightSignals}=await import('../src/native-highlight');
 const a=frame([['Value',0,{node:'n',device:'a'}]]),b={...frame([['Value',2,{node:'n',device:'a'}]]),refId:'B'};
 const signals=nativeHighlightSignals(data([a,b]));assert.equal(signals.length,2);assert.equal(signals[0].result.sample?.value,0);assert.equal(signals[1].result.sample?.value,2);
 const multi=nativeHighlightSignals(data([a,frame([['Value',4,{node:'n',device:'b'}]])]));assert.equal(multi[0].result.state,'multiple');assert.equal(multi[0].result.sample,undefined);
});

test('an absent local query is not replaced by the external query value or zero',async()=>{
 const {nativeHighlightSignals}=await import('../src/native-highlight');
 const external={...frame([['Value',0,{node:'n',engine:'e'}]]),refId:'B'};
 const signals=nativeHighlightSignals(data([external]),['A','B']);
 assert.equal(signals.length,2);assert.equal(signals[0].ref,'A');assert.equal(signals[0].result.state,'no-data');assert.equal(signals[0].result.sample,undefined);
 assert.equal(signals[1].ref,'B');assert.equal(signals[1].result.sample?.value,0);
});
