import {test} from 'node:test';
import assert from 'node:assert/strict';
import {infrastructureModel,selectInfrastructure,topologyEndpoint} from '../src/infrastructure';
import {readContext} from '../src/context';

const sample=(labels:Record<string,string>,value=1,time=100000)=>({labels,value,time,unit:''});
test('configured topology never uses publisher identity as resource owner or claims observed links',()=>{
 const components=[sample({cluster:'c',kind:'storage',component:'ds',role:'data',resource_node:'data',nodename:'trainer',instance:'trainer'})];
 const edges=[sample({cluster:'c',kind:'storage',source:'client',destination:'ds',relation:'declared'})];
 const model=infrastructureModel(components,edges,[sample({cluster:'c',nodename:'data',instance:'data'})],100000);
 const node=model.nodes.find(n=>n.component==='ds')!;
 assert.equal(node.resourceNode,'data');assert.equal(node.status,'observed');
 assert.equal(model.edges[0].status,'configured');assert.equal(model.observedEdges,0);
});
test('conflicting owners, duplicate exporters, stale sources and missing nodes remain unknown',()=>{
 const model=infrastructureModel([
  sample({cluster:'c',kind:'storage',component:'ds',role:'data',resource_node:'a'}),
  sample({cluster:'c',kind:'storage',component:'ds',role:'data',resource_node:'b'}),
  sample({cluster:'c',kind:'compute',component:'legacy',role:'gpu-node',nodename:'publisher'}),
 ],[],[sample({cluster:'c',nodename:'a',instance:'x'}),sample({cluster:'c',nodename:'a',instance:'y'})],100000);
 assert.equal(model.nodes.find(n=>n.component==='ds')?.status,'unknown');
 assert.equal(model.nodes.find(n=>n.component==='legacy')?.resourceNode,undefined);
 const stale=infrastructureModel([sample({cluster:'c',kind:'compute',component:'gpu',resource_node:'gpu'})],[],[sample({cluster:'c',nodename:'gpu',instance:'gpu'},1,1)],100000);
 assert.equal(stale.nodes[0].status,'unknown');
});
test('observed zero is scrape down, not missing; cluster/device identity survives drill down',()=>{
 const model=infrastructureModel([sample({cluster:'c',kind:'compute',component:'gpu/0',resource_node:'gpu',gpu:'0'})],[],[sample({cluster:'c',nodename:'gpu',instance:'gpu'},0)],100000);
 const node=model.nodes.find(n=>n.component==='gpu/0')!;assert.equal(node.status,'scrape_down');
 const context={...readContext('?from=1&to=2&var-run_id=r&var-record_id=s&var-source_node=trainer'),uiVersion:'workspace' as const};
 const selected=selectInfrastructure(context,node);
 assert.equal(selected.uiVersion,'workspace');assert.deepEqual(selected.variables.node,['gpu']);assert.deepEqual(selected.variables.gpu,['0']);
 assert.deepEqual(selected.variables.source_node,['trainer']);assert.deepEqual(selected.variables.record_id,['s']);
});

test('exporter reachability does not verify device or GPU observations',()=>{
 const components=[sample({cluster:'c',kind:'compute',component:'gpu/0',resource_node:'gpu',gpu:'0'})];
 const up=[sample({cluster:'c',nodename:'gpu',instance:'gpu'})];
 assert.equal(infrastructureModel(components,[],up,100000).nodes[0].status,'unknown');
 assert.equal(infrastructureModel(components,[],up,100000,30000,[sample({cluster:'other',node:'gpu',gpu:'0'},90)]).nodes[0].status,'unknown');
 assert.equal(infrastructureModel(components,[],up,100000,30000,[sample({cluster:'c',node:'gpu',gpu:'0'},90,1)]).nodes[0].status,'unknown');
 assert.equal(infrastructureModel(components,[],up,100000,30000,[sample({cluster:'c',node:'gpu',gpu:'0'},90)]).nodes[0].status,'observed');
});

test('ambiguous configured relationship never selects the first component from another kind',()=>{
 const components=[sample({cluster:'c',kind:'storage',component:'same',resource_node:'s'}),sample({cluster:'c',kind:'compute',component:'same',resource_node:'g'})];
 const {nodes}=infrastructureModel(components,[],[],100000);
 const edge={key:'e',cluster:'c',kind:'network',source:'same',destination:'x',relation:'declared',status:'configured' as const};
 assert.equal(topologyEndpoint(nodes,edge,'same'),undefined);
 assert.equal(topologyEndpoint(nodes,{...edge,kind:'storage'},'same')?.resourceNode,'s');
});

test('storage inspection selects the declared storage owner without replacing the Step observer',()=>{
 const node=infrastructureModel([sample({cluster:'c',kind:'storage',component:'ds',role:'data',resource_node:'data-host',storage_system:'3fs'})],[],[],100000).nodes[0];
 const selected=selectInfrastructure(readContext('?var-source_node=trainer&var-node=gpu&var-run_id=r&var-record_id=s'),node);
 assert.deepEqual(selected.variables.storage_node,['data-host']);assert.deepEqual(selected.variables.storage_system,['3fs']);
 assert.deepEqual(selected.variables.source_node,['trainer']);assert.deepEqual(selected.variables.record_id,['s']);
});
