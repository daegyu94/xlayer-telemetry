import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Resource } from '../src/infrastructure';
import { topologyLayout } from '../src/topology-layout';
const node = (id: string, kind = 'compute', type: Resource['type'] = 'node', role = 'gpu-node', cluster = 'c'): Resource => ({ key: JSON.stringify([cluster,kind,id]), cluster,id,kind,type,role,mapping:'configured',collector:'unknown',resource:id });
const edge = (source: Resource, destination: Resource) => ({source:source.key,destination:destination.key,relation:'operator declared',state:'configured'});

test('compact counts source declarations and keeps selected hidden nodes visible without losing identity',()=>{
 const nodes=Array.from({length:9},(_,i)=>node(`gpu-${i}`));
 const compact=topologyLayout(nodes,[],{selectedKey:nodes[8].key}),group=compact.groups.find(g=>g.id==='compute')!;
 assert.equal(group.total,9);assert.equal(group.visible.length,4);assert.equal(group.hiddenCount,5);assert.ok(group.visible.includes(nodes[8]));
 assert.deepEqual(group.all,nodes);
 for(const value of compact.visibleNodes)assert.strictEqual(value,nodes.find(n=>n.key===value.key));
});
test('same-name fabrics stay separate across namespace and cluster, without physical-link inference',()=>{
 const nodes=[node('roce-fabric','compute','fabric','network'),node('roce-fabric','storage','fabric','network'),node('roce-fabric','compute','fabric','network','other')];
 const model=topologyLayout(nodes,[]),group=model.groups.find(g=>g.id==='network')!;
 assert.equal(group.total,3);assert.equal(group.visible.length,2);assert.equal(group.hiddenCount,1);assert.equal(new Set(group.all.map(n=>n.key)).size,3);assert.deepEqual(model.relations,[]);
});
test('Storage grouping uses declared MDS/DS roles and keeps unclassified storage separate',()=>{
 const mds=node('metadata','storage','node','mds'),ds=node('data','storage','node','data'),unknown=node('generic','storage','node','storage-node');
 const model=topologyLayout([unknown,ds,mds],[]);
 assert.deepEqual(model.groups.find(g=>g.id==='mds')!.visible,[mds]);assert.deepEqual(model.groups.find(g=>g.id==='ds')!.visible,[ds]);assert.deepEqual(model.groups.find(g=>g.id==='storageUnknown')!.visible,[unknown]);
});
test('selected device remains a device and unknown endpoints remain in the declared relationship ledger',()=>{
 const host=node('host'),gpu={...node('gpu','compute','gpu','gpu'),resource:host.resource,gpu:'0'};
 const missing={source:host.key,destination:'missing',relation:'declared',state:'unknown'},declaration=edge(host,gpu);
 const model=topologyLayout([host,gpu],[declaration,missing],{selectedKey:gpu.key});
 assert.strictEqual(model.selectedResource,gpu);assert.equal(model.selectedDevice,true);assert.equal(model.groups.find(g=>g.id==='compute')!.total,1);
 assert.strictEqual(model.relations[0].edge,declaration);assert.strictEqual(model.relations[0].destination,gpu);assert.equal(model.relations[1].state,'unknown');assert.match(model.relations[1].reason,/endpoint/i);
});
test('compact view keeps bounded inventory and preserves a selection beyond the ordinary display bound',()=>{
 const nodes=Array.from({length:300},(_,i)=>node(`gpu-${String(i).padStart(3,'0')}`));
 const before=JSON.stringify(nodes),model=topologyLayout(nodes,[],{selectedKey:nodes[299].key});
 assert.equal(model.visibleNodes.length,4);assert.ok(model.visibleNodes.includes(nodes[299]));assert.equal(model.inventoryLimited,true);assert.equal(model.groups.find(g=>g.id==='compute')!.all.length,256);assert.equal(model.groups.find(g=>g.id==='compute')!.total,300);assert.equal(JSON.stringify(nodes),before);
});
test('no selection does not auto-select a resource and empty / unknown states remain explicit',()=>{
 const resource=node('node'),model=topologyLayout([resource],[]);
 assert.equal(model.selectedResource,undefined);assert.equal(model.selectedDevice,false);assert.equal(resource.collector,'unknown');
 const empty=topologyLayout([],[]);assert.equal(empty.visibleNodes.length,0);assert.equal(empty.totalResources,0);
});
