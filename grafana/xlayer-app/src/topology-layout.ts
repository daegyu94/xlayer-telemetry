import type { Resource } from './infrastructure';

export type TopologyRelationship = { source: string; destination: string; relation: string; state: string };
export type TopologyGroupId = 'compute' | 'network' | 'mds' | 'ds' | 'storageUnknown' | 'other';
export type TopologyGroup = { id: TopologyGroupId; title: string; total: number; all: Resource[]; visible: Resource[]; hiddenCount: number };
const GROUPS: Array<{ id: TopologyGroupId; title: string; compact: number }> = [
 {id:'compute',title:'Compute / GPU & Rollout',compact:4},
 {id:'network',title:'Network / Configured identities',compact:2},
 {id:'mds',title:'Metadata Servers',compact:2},
 {id:'ds',title:'Data Servers',compact:4},
 {id:'storageUnknown',title:'Unclassified Storage Roles',compact:2},
 {id:'other',title:'Other Configured Components',compact:2},
];
const isDevice=(node:Resource)=>['gpu','nic','ssd'].includes(node.type);
function groupFor(node:Resource):TopologyGroupId {
 if(node.type==='fabric')return 'network';
 if(node.kind==='compute')return 'compute';
 if(node.kind==='storage') {
  if(/(^|-)(mds|metadata)(-|$)/i.test(node.role))return 'mds';
  if(/(^|-)(ds|data)(-|$)/i.test(node.role))return 'ds';
  return 'storageUnknown';
 }
 return 'other';
}
const compare=(a:Resource,b:Resource)=>a.cluster.localeCompare(b.cluster,'en',{numeric:true})||a.id.localeCompare(b.id,'en',{numeric:true})||a.key.localeCompare(b.key,'en');

/** Read-only layout grouping. It creates neither physical hops nor resource ownership. */
export function topologyLayout(nodes:Resource[],edges:TopologyRelationship[],options:{expanded?:boolean;selectedKey?:string}={}) {
 const selectedResource=nodes.find(node=>node.key===options.selectedKey);
 const eligible=nodes.filter(node=>!isDevice(node));
 const bounded=eligible.slice(0,256);
 if(selectedResource&&!isDevice(selectedResource)&&!bounded.includes(selectedResource))bounded.splice(Math.max(0,bounded.length-1),1,selectedResource);
 const groups:TopologyGroup[]=GROUPS.map(spec=>{
  const all=bounded.filter(node=>groupFor(node)===spec.id).sort(compare);
  let visible=options.expanded?[...all]:all.slice(0,spec.compact);
  if(selectedResource&&all.includes(selectedResource)&&!visible.includes(selectedResource))visible=[...visible.slice(0,-1),selectedResource].sort(compare);
  const total=eligible.filter(node=>groupFor(node)===spec.id).length;
  return {id:spec.id,title:spec.title,total,all,visible,hiddenCount:total-visible.length};
 });
 const known=new Map(nodes.map(node=>[node.key,node]));
 const relations=edges.map((edge,index)=>{
  const source=known.get(edge.source),destination=known.get(edge.destination);
  return {edge,index,source,destination,state:source&&destination?edge.state:'unknown',reason:source&&destination?'Operator declaration · physical connectivity not observed':'Endpoint not present in returned inventory'};
 });
 return {groups,visibleNodes:groups.flatMap(group=>group.visible),selectedResource,selectedDevice:!!selectedResource&&isDevice(selectedResource),relations,
  totalResources:eligible.length,inventoryLimited:eligible.length>256,expanded:!!options.expanded};
}
