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
