import test from 'node:test';
import assert from 'node:assert/strict';
import { infrastructure, resourceSelection, logPanel } from '../src/infrastructure';

const context={variables:{cluster:['c'],run_id:['run'],record_id:['step'],source_node:['trainer'],node:['old'],worker:['w'],device:['old-device'],gpu:['old-gpu']},from:'1000',to:'2000',timezone:'browser'};
const rows=[{cluster:'c',kind:'compute',component:'host',role:'gpu-node',resource_node:'compute'},
 {cluster:'c',kind:'compute',component:'gpu',role:'gpu',resource_node:'compute',gpu:'0'},
 {cluster:'c',kind:'storage',component:'disk',role:'ssd',resource_node:'storage',device:'nvme0n1',storage_system:'3fs'},
 {cluster:'c',kind:'compute',component:'fabric',role:'network'}];
test('configured topology never implies healthy connectivity or Run ownership',()=>{
 const model=infrastructure(rows,[{cluster:'c',kind:'compute',source:'host',destination:'fabric',relation:'RoCE'}],
  [{cluster:'c',nodename:'compute',instance:'compute',Value:1,_refId:'A'},{cluster:'c',nodename:'compute',Value:2,_refId:'B'}],2000);
 assert.equal(model.nodes.length,4);assert.equal(model.edges[0].state,'configured');
 assert.equal(model.nodes.find(n=>n.id==='host')?.collector,'available');
 assert.equal(model.nodes.find(n=>n.id==='fabric')?.mapping,'unknown');
 assert.equal(model.nodes.find(n=>n.id==='fabric')?.collector,'unknown');
});
test('publisher identity is not resource identity and conflicting mappings stay unknown',()=>{
 const model=infrastructure([{cluster:'c',kind:'storage',component:'unknown',nodename:'publisher',role:'ds'},
  ...rows,{...rows[2],resource_node:'other'}],[],[],2000);
 assert.equal(model.nodes.find(n=>n.id==='unknown')?.resource,undefined);
 assert.equal(model.nodes.find(n=>n.id==='disk')?.mapping,'ambiguous');
});
test('availability retains zero, stale, unknown and multiple-target states',()=>{
 for(const [values,state] of [[[{_refId:'A',Value:0}], 'down'],[[{_refId:'A',Value:1},{_refId:'B',Value:60}],'stale'],[[],'unknown'],
  [[{_refId:'A',Value:1,instance:'a'},{_refId:'A',Value:1,instance:'b'}],'ambiguous']] as const){
  const model=infrastructure(rows,[],values.map(v=>({...v,cluster:'c',nodename:'compute'})),2000);
  assert.equal(model.nodes.find(n=>n.id==='host')?.collector,state);
 }
});
test('resource navigation keeps investigation identity while setting only applicable resource filters',()=>{
 const model=infrastructure(rows,[],[],2000);
 const disk=resourceSelection(context,model.nodes.find(n=>n.id==='disk')!);
 assert.deepEqual(disk.variables.node,['storage']);assert.deepEqual(disk.variables.device,['nvme0n1']);
 assert.deepEqual(disk.variables.storage_node,['storage']);assert.deepEqual(disk.variables.gpu,['.*']);
 assert.deepEqual(disk.variables.source_node,['trainer']);assert.deepEqual(disk.variables.record_id,['step']);
 assert.deepEqual(disk.variables.run_id,['run']);assert.equal(disk.from,'1000');
 const gpu=resourceSelection(disk,model.nodes.find(n=>n.id==='gpu')!);assert.deepEqual(gpu.variables.device,['.*']);assert.deepEqual(gpu.variables.gpu,['0']);
 assert.throws(()=>resourceSelection(context,model.nodes.find(n=>n.id==='fabric')!));
});
test('canonical log expressions use distinct application and log payload Run filters without rewriting queries',()=>{
 const panel={id:1,type:'logs',title:'Logs',targets:[{expr:'{run_id=~"$telemetry_run_id"} | unpack | run_id=~"$run_id"'}]};
 assert.equal(logPanel(panel)?.targets?.[0].expr,'{run_id=~"$run_id"} | unpack | run_id=~"$log_run_id"');
 assert.equal(panel.targets[0].expr,'{run_id=~"$telemetry_run_id"} | unpack | run_id=~"$run_id"');
});
