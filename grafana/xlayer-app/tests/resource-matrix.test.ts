import {test} from 'node:test';
import assert from 'node:assert/strict';
import type {PanelData} from '@grafana/data';
import {resourceMatrix,resourceKind} from '../src/resource-matrix';
const frame=(labels:any,values:any[],refId='A',times=[1000,2000])=>({refId,length:times.length,fields:[{name:'Time',type:'time',values:times,config:{}},{name:'Value',type:'number',labels,values,config:{}}]});
const data=(series:any[],state='Done')=>({state,series,timeRange:{from:{valueOf:()=>1000},to:{valueOf:()=>2000}},request:{intervalMs:1000}} as unknown as PanelData);
test('resource matrix keeps cluster/node/device/query identity and measured zero',()=>{
 const m=resourceMatrix(data([frame({cluster:'a',nodename:'n',gpu:'0'},[1,0]),frame({cluster:'b',nodename:'n',gpu:'0'},[2,3]),frame({cluster:'a',nodename:'n',gpu:'1'},[4,5]),frame({cluster:'a',nodename:'n',gpu:'0'},[6,7],'B')]),'gpu','latest');
 assert.equal(m.rows.length,2);assert.deepEqual(m.columns,['0','1']);assert.equal(m.metrics.length,2);assert.equal(m.cells.length,4);assert.equal(m.cells[0].value,0);assert.equal(m.cells[0].status,'observed');assert.equal(new Set(m.cells.map(c=>c.key)).size,4);
});
test('latest, mean and max are explicit reductions of original finite samples',()=>{
 const d=data([frame({node:'n',device:'nvme0n1'},[0,4])]);
 assert.equal(resourceMatrix(d,'disk','latest').cells[0].value,4);
 assert.equal(resourceMatrix(d,'disk','mean').cells[0].value,2);
 assert.equal(resourceMatrix(d,'disk','max').cells[0].value,4);
 assert.equal(resourceMatrix(d,'disk','mean').cells[0].count,2);
});
test('missing tail, stale, future evaluation and query failure never become zero',()=>{
 assert.equal(resourceMatrix(data([frame({node:'n',gpu:'0'},[7,null])]),'gpu','latest').cells[0].status,'no-data');
 assert.equal(resourceMatrix(data([frame({node:'n',gpu:'0'},[1,8],'A',[-30000,-20000])]),'gpu','latest').cells[0].status,'stale');
 assert.equal(resourceMatrix(data([frame({node:'n',gpu:'0'},[1,8],'A',[1000,3000])]),'gpu','latest').cells[0].status,'unknown');
 assert.equal(resourceMatrix(data([],'Error'),'gpu','latest').state,'error');
 assert.equal(resourceMatrix(undefined,'gpu','latest').state,'loading');
 assert.equal(resourceMatrix(data([]),'gpu','latest').state,'no-data');
});
test('missing/conflicting resource identity is retained as unknown, never guessed',()=>{
 const m=resourceMatrix(data([frame({gpu:'0'},[2,3]),frame({node:'a',nodename:'b',gpu:'1'},[2,3])]),'gpu','latest');
 assert.ok(m.cells.every(c=>c.status==='unknown'));
 const n=resourceMatrix(data([frame({node:'n',device:'mlx5_0',port:'1'},[1,2]),frame({node:'n',device:'mlx5_0',port:'2'},[3,4])]),'network','latest');
 assert.deepEqual(n.columns,['mlx5_0 / 1','mlx5_0 / 2']);
});
test('resource view only adapts existing device metric panels',()=>{
 assert.equal(resourceKind([{expr:'telemetry_gpu_utilization_percent{node="n"}'}]),'gpu');
 assert.equal(resourceKind([{expr:'rate(node_disk_reads_completed_total[1m])'}]),'disk');
 assert.equal(resourceKind([{expr:'node_infiniband_port_transmit_wait_total'}]),'network');
 assert.equal(resourceKind([{expr:'vllm:num_requests_waiting'}]),undefined);
});
test('display names that vary per device do not split one canonical query into metrics',()=>{
 const a=frame({cluster:'c',node:'n',gpu:'0'},[1,2]);
 const b=frame({cluster:'c',node:'n',gpu:'1'},[3,4]);
 a.fields[1].name='GPU 0';b.fields[1].name='GPU 1';
 assert.equal(resourceMatrix(data([a,b]),'gpu','latest').metrics.length,1);
});
