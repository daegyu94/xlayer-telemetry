import {test} from 'node:test';
import assert from 'node:assert/strict';
import type {PanelData} from '@grafana/data';
import {resourceMatrix,resourceMatrixProfile,resourceMetricAvailability,matrixColorAlpha,resourcePanelKey} from '../src/resource-matrix';
const frame=(labels:any,values:any[],refId='A',times=[1000,2000])=>({refId,length:times.length,fields:[{name:'Time',type:'time',values:times,config:{}},{name:'Value',type:'number',labels,values,config:{}}]});
const data=(series:any[],state='Done')=>({state,series,timeRange:{from:{valueOf:()=>1000},to:{valueOf:()=>2000}},request:{intervalMs:1000}} as unknown as PanelData);
test('resource matrix keeps cluster/node/device/query identity and measured zero',()=>{
 const m=resourceMatrix(data([frame({cluster:'a',nodename:'n',gpu:'0'},[1,0]),frame({cluster:'b',nodename:'n',gpu:'0'},[2,3]),frame({cluster:'a',nodename:'n',gpu:'1'},[4,5]),frame({cluster:'a',nodename:'n',gpu:'0'},[6,7],'B')]),resourceMatrixProfile('compute',2)!, 'latest');
 assert.equal(m.rows.length,2);assert.deepEqual(m.columns,['0','1']);assert.equal(m.metrics.length,2);assert.equal(m.cells.length,4);assert.equal(m.cells[0].value,0);assert.equal(m.cells[0].status,'observed');assert.equal(new Set(m.cells.map(c=>c.key)).size,4);
});
test('latest, mean and max are explicit reductions of original finite samples',()=>{
 const d=data([frame({cluster:'c',node:'n',device:'nvme0n1'},[0,4])]);
 assert.equal(resourceMatrix(d,resourceMatrixProfile('storage',1)!, 'latest').cells[0].value,4);
 assert.equal(resourceMatrix(d,resourceMatrixProfile('storage',1)!, 'mean').cells[0].value,2);
 assert.equal(resourceMatrix(d,resourceMatrixProfile('storage',1)!, 'max').cells[0].value,4);
 assert.equal(resourceMatrix(d,resourceMatrixProfile('storage',1)!, 'mean').cells[0].count,2);
});
test('missing tail, stale, future evaluation and query failure never become zero',()=>{
 const profile=resourceMatrixProfile('compute',2)!;
 assert.equal(resourceMatrix(data([frame({cluster:'c',node:'n',gpu:'0'},[7,null])]),profile,'latest').cells[0].status,'no-data');
 assert.equal(resourceMatrix(data([frame({cluster:'c',node:'n',gpu:'0'},[1,8],'A',[-30000,-20000])]),profile,'latest').cells[0].status,'stale');
 assert.equal(resourceMatrix(data([frame({cluster:'c',node:'n',gpu:'0'},[1,8],'A',[1000,3000])]),profile,'latest').cells[0].status,'unknown');
 assert.equal(resourceMatrix(data([],'Error'),profile,'latest').state,'error');
 assert.equal(resourceMatrix(undefined,profile,'latest').state,'loading');
 assert.equal(resourceMatrix(data([]),profile,'latest').state,'no-data');
});
test('missing/conflicting resource identity is retained as unknown, never guessed',()=>{
 const profile=resourceMatrixProfile('compute',2)!;
 const m=resourceMatrix(data([frame({cluster:'c',gpu:'0'},[2,3]),frame({cluster:'c',node:'a',nodename:'b',gpu:'1'},[2,3])]),profile,'latest');
 assert.ok(m.cells.every(c=>c.status==='unknown'));
 const n=resourceMatrix(data([frame({cluster:'c',node:'n',device:'mlx5_0',port:'1'},[1,2]),frame({cluster:'c',node:'n',device:'mlx5_0',port:'2'},[3,4])]),resourceMatrixProfile('compute',9)!,'latest');
 assert.deepEqual(n.columns,['mlx5_0 / 1','mlx5_0 / 2']);
});
test('Matrix eligibility is declared for exact panels, not inferred from a mixed query string',()=>{
 assert.equal(resourceMatrixProfile('compute',2)?.dimension,'gpu');
 assert.equal(resourceMatrixProfile('storage',1)?.dimension,'device');
 assert.equal(resourceMatrixProfile('compute',9)?.dimension,'port');
 assert.equal(resourceMatrixProfile('stage',12),undefined);
 assert.equal(resourceMatrixProfile('timeline',24),undefined);
 const unsupported=resourceMatrix(data([frame({cluster:'c',node:'n'},[2,3])]),resourceMatrixProfile('storage',1)!,'latest');
 assert.equal(unsupported.state,'unsupported');
});
test('display names that vary per device do not split one canonical query into metrics',()=>{
 const a=frame({cluster:'c',node:'n',gpu:'0'},[1,2]);
 const b=frame({cluster:'c',node:'n',gpu:'1'},[3,4]);
 a.fields[1].name='GPU 0';b.fields[1].name='GPU 1';
 assert.equal(resourceMatrix(data([a,b]),resourceMatrixProfile('compute',2)!,'latest').metrics.length,1);
});

test('fixed usage colors express absolute percentage while arbitrary resource values stay relative',()=>{
 const gpu=resourceMatrixProfile('compute',2)!;
 const diskBusy=resourceMatrixProfile('storage',3)!;
 assert.equal(matrixColorAlpha(5,gpu.color,10,'percent'),matrixColorAlpha(5,gpu.color,100,'percent'));
 assert.equal(matrixColorAlpha(10,gpu.color,100,'percent'),0.09);
 assert.equal(matrixColorAlpha(10,gpu.color,1,'percent'),0.09);
 assert.equal(matrixColorAlpha(0.1,diskBusy.color,100,'percentunit'),0.09);
 assert.equal(matrixColorAlpha(1000,gpu.color,100,'percent'),undefined);
 const disk=resourceMatrixProfile('storage',1)!;
 assert.ok(matrixColorAlpha(5,disk.color,10,'Bps')!>matrixColorAlpha(5,disk.color,100,'Bps')!);
});

test('a selected metric stays selected through A+B, B-only, recovery and measured zero',()=>{
 const profile=resourceMatrixProfile('storage',1)!;
 const read=frame({cluster:'c',node:'n',device:'nvme0n1'},[2,0],'A');
 const write=frame({cluster:'c',node:'n',device:'nvme0n1'},[8,9],'B');
 const selected='A';
 const both=resourceMatrix(data([read,write]),profile,'latest','Bps');
 assert.equal(resourceMetricAvailability(both,selected),'present');
 assert.equal(both.cells.find(c=>c.metric==='["A"]')?.value,0);
 assert.equal(both.cells.find(c=>c.metric==='["A"]')?.status,'observed');
 const writeOnly=resourceMatrix(data([write]),profile,'latest','Bps');
 assert.deepEqual(writeOnly.metrics,['["B"]']);
 assert.equal(resourceMetricAvailability(writeOnly,selected),'missing');
 assert.notEqual(resourceMetricAvailability(writeOnly,selected),'present','do not substitute write values for read');
 const recovered=resourceMatrix(data([read,write]),profile,'latest','Bps');
 assert.equal(resourceMetricAvailability(recovered,selected),'present');
 assert.equal(recovered.cells.find(c=>c.metric==='["A"]')?.value,0);
 assert.equal(resourceMetricAvailability(resourceMatrix(data([]),profile,'latest'),'["A"]'),'empty');
 assert.equal(resourceMetricAvailability(resourceMatrix(undefined,profile,'latest'),'["A"]'),'loading');
 assert.equal(resourceMetricAvailability(resourceMatrix(data([],'Error'),profile,'latest'),'["A"]'),'error');
});


test('Deep Dive panel identity resets local units/selection when the selected canonical panel changes',()=>{
 const gpuPanel={},storagePanel={},gpuAgain=gpuPanel;
 assert.equal(resourcePanelKey(gpuPanel),resourcePanelKey(gpuAgain));
 assert.notEqual(resourcePanelKey(gpuPanel),resourcePanelKey(storagePanel));
});
