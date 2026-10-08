import type {Sample} from './semantics';
import type {Context} from './context';
import {resourceContext} from './distributed';

export type ResourceNode={key:string;cluster:string;kind:string;component:string;role:string;resourceNode?:string;device?:string;gpu?:string;system?:string;configured:boolean;status:'observed'|'scrape_down'|'unknown';issues:string[];targets:string[]};
export type ResourceEdge={key:string;cluster:string;kind:string;source:string;destination:string;relation:string;status:'configured'|'unknown'};
export function topologyEndpoint(nodes:ResourceNode[],edge:ResourceEdge,id:string):ResourceNode|undefined{
 const sameKind=nodes.filter(node=>node.cluster===edge.cluster&&node.kind===edge.kind&&node.component===id);
 const matches=sameKind.length?sameKind:nodes.filter(node=>node.cluster===edge.cluster&&node.component===id);
 return matches.length===1?matches[0]:undefined;
}
const key=(cluster:string,kind:string,id:string)=>JSON.stringify([cluster,kind,id]);
export function infrastructureModel(components:Sample[],relationships:Sample[],availability:Sample[],at:number,maxAge=30000,measurements:Sample[]=[]){
 const latest=(rows:Sample[])=>[...new Map(rows.sort((a,b)=>a.time-b.time).map(row=>[JSON.stringify(Object.entries(row.labels).sort()),row])).values()];
 const groups=new Map<string,Sample[]>();
 for(const row of latest([...components])){
  const l=row.labels;if(!l.cluster||!l.component||!l.kind)continue;
  const id=key(l.cluster,l.kind,l.component);groups.set(id,[...(groups.get(id)||[]),row]);
 }
 const nodes:ResourceNode[]=[];
 const registered=latest([...availability]);
 for(const [id,rows] of groups){
  const l=rows[0].labels;
  const mapping=new Set(rows.map(row=>JSON.stringify([row.labels.resource_node,row.labels.device,row.labels.gpu,row.labels.role,row.labels.storage_system])));
  const resourceNode=mapping.size===1?l.resource_node:undefined;
  const matches=resourceNode?registered.filter(row=>row.labels.cluster===l.cluster&&(row.labels.nodename||row.labels.node)===resourceNode):[];
  const fresh=matches.filter(row=>at>=row.time&&at-row.time<=maxAge);
  let status:ResourceNode['status']=mapping.size!==1||!resourceNode||matches.length!==1||fresh.length!==1?'unknown':fresh[0].value===1?'observed':fresh[0].value===0?'scrape_down':'unknown';
  const deviceVerified=measurements.some(sample=>sample.labels.cluster===l.cluster&&(sample.labels.node||sample.labels.nodename)===resourceNode&&
   (!l.device||sample.labels.device===l.device)&&(!l.gpu||sample.labels.gpu===l.gpu)&&at>=sample.time&&at-sample.time<=maxAge);
  if((l.device||l.gpu!==undefined)&&status==='observed'&&!deviceVerified)status='unknown';
  nodes.push({key:id,cluster:l.cluster,kind:l.kind,component:l.component,role:l.role||'not reported',resourceNode,
   device:l.device,gpu:l.gpu,system:l.storage_system,configured:true,status,
   issues:!resourceNode?[mapping.size>1?'Conflicting configured identity':'Resource owner not configured']:matches.length>1?['Multiple exporter identities']:!matches.length?['Exporter not registered']:!fresh.length?['Stale/unknown scrape observation']:(l.device||l.gpu!==undefined)&&!deviceVerified?['Device/GPU observation not verified by exporter reachability']:[],
   targets:matches.map(row=>row.labels.instance||'not reported')});
 }
 for(const row of registered){
  const l=row.labels,node=l.nodename||l.node;if(!l.cluster||!node||nodes.some(n=>n.cluster===l.cluster&&n.resourceNode===node))continue;
  nodes.push({key:key(l.cluster,'unknown',`${node}/${l.instance||''}`),cluster:l.cluster,kind:'unknown',component:node,role:'not configured',resourceNode:node,configured:false,
   status:at>=row.time&&at-row.time<=maxAge?(row.value===1?'observed':row.value===0?'scrape_down':'unknown'):'unknown',issues:['Cluster role/topology not configured'],targets:[l.instance||'not reported']});
 }
 const edges=latest([...relationships]).filter(row=>row.labels.cluster&&row.labels.source&&row.labels.destination).map(row=>{
  const l=row.labels;return {key:JSON.stringify([l.cluster,l.kind,l.source,l.destination,l.relation]),cluster:l.cluster,kind:l.kind||'unknown',source:l.source,destination:l.destination,relation:l.relation||'unspecified',status:'configured' as const};
 });
 return {nodes,edges,observedEdges:0,configuredNodes:nodes.filter(n=>n.configured).length,observedNodes:nodes.filter(n=>n.status==='observed').length,unknownNodes:nodes.filter(n=>n.status==='unknown').length};
}
export function selectInfrastructure(context:Context,node:ResourceNode):Context{
 const labels={...(node.resourceNode?{node:node.resourceNode}:{}),...(node.device?{device:node.device}:{}),...(node.gpu!==undefined?{gpu:node.gpu}:{})};
 const storage=node.kind==='storage'&&node.resourceNode?{storage_node:[node.resourceNode],...(node.system?{storage_system:[node.system]}:{})}:{};
 return {...resourceContext(context,labels),variables:{...resourceContext(context,labels).variables,...storage,cluster:[node.cluster],infra_component:[node.key]}};
}
