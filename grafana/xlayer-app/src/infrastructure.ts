import { Context, RecordRow, numeric } from './context';
import { Panel } from './catalog';

export const INFRA_PANELS={components:130,edges:131,availability:132} as const;
const mappingFields=['resource_node','gpu','interface','device','storage_system'] as const;
const text=(v:unknown)=>typeof v==='string'&&v.length>0?v:undefined;
export type Resource = {key:string;cluster:string;kind:string;id:string;role:string;resource?:string;gpu?:string;device?:string;interface?:string;storageSystem?:string;
 mapping:'configured'|'unknown'|'ambiguous';collector:'available'|'down'|'stale'|'unknown'|'ambiguous';type:'node'|'gpu'|'nic'|'ssd'|'fabric'|'unknown'};
export function resourceKey(row:RecordRow){return JSON.stringify([row.cluster,row.kind,row.component]);}
export function infrastructure(components:RecordRow[], edges:RecordRow[], availability:RecordRow[], _at:number){
 const groups=new Map<string,RecordRow[]>();
 for(const row of components){if(!text(row.cluster)||!text(row.kind)||!text(row.component))continue;const key=resourceKey(row);groups.set(key,[...(groups.get(key)||[]),row]);}
 const nodes:Resource[]=[...groups].slice(0,256).map(([key,rows])=>{
  const row=rows[0], coherent=new Set(rows.map(r=>JSON.stringify([r.role,...mappingFields.map(f=>r[f]||null)]))).size===1;
  const resource=coherent?text(row.resource_node):undefined;
  const gpu=coherent?text(row.gpu):undefined, device=coherent?text(row.device):undefined, nic=coherent?text(row.interface):undefined;
  const samples=resource?availability.filter(r=>r.cluster===row.cluster&&r.nodename===resource):[];
  const up=samples.filter(r=>r._refId==='A'), age=samples.filter(r=>r._refId==='B');
  const latest=up.length===1?numeric(up[0].Value):undefined, old=age.length===1?numeric(age[0].Value):undefined;
  const collector=up.length>1||age.length>1?'ambiguous':latest===0?'down':latest!==1?'unknown':old===undefined?'unknown':old>30?'stale':old<0?'unknown':'available';
  const role=String(row.role||'unknown');
  return {key,cluster:String(row.cluster),kind:String(row.kind),id:String(row.component),role,resource,gpu,device,interface:nic,
   storageSystem:coherent?text(row.storage_system):undefined,
   mapping:!coherent?'ambiguous':resource?'configured':'unknown',collector,
   type:gpu?'gpu':nic?'nic':role==='ssd'||device?'ssd':role==='network'?'fabric':/gpu-node|data|ds|metadata|mds|storage-node|compute-node/.test(role)?'node':'unknown'} as Resource;
 });
 const known=new Map(nodes.map(n=>[n.key,n]));
 const links=edges.slice(0,512).filter(row=>text(row.cluster)&&text(row.kind)&&text(row.source)&&text(row.destination)).map(row=>{
  const source=JSON.stringify([row.cluster,row.kind,row.source]),destination=JSON.stringify([row.cluster,row.kind,row.destination]);
  return {source,destination,relation:String(row.relation||'Unspecified'),state:known.has(source)&&known.has(destination)?'configured':'unknown'};
 });
 return {nodes,edges:links,limited:groups.size>256||edges.length>512};
}
export function resourceSelection(context:Context,node:Resource):Context{
 if(node.mapping!=='configured'||!node.resource)throw new Error('Resource mapping is not unambiguous');
 return {...context,variables:{...context.variables,cluster:[node.cluster],infra_component:[node.key],node:[node.resource],
  gpu:[node.gpu||'.*'],device:[node.device||node.interface||'.*'],
  storage_node:[node.kind==='storage'?node.resource:'.*'],storage_system:[node.storageSystem||'.*']}};
}
/** Keep canonical LogQL, renaming only variables with dashboard-specific meaning. */
export function logPanel(panel:Panel|undefined):Panel|undefined{
 if(!panel)return undefined;
 return {...panel,targets:panel.targets?.map(target=>({...target,expr:typeof target.expr==='string'?target.expr.replace(/\$(?:\{run_id(:[^}]+)?\}|run_id\b)/g,
  (_match:string,suffix:string|undefined)=>suffix?'${log_run_id'+suffix+'}':'$log_run_id').replace(/\$(?:\{telemetry_run_id(:[^}]+)?\}|telemetry_run_id\b)/g,
  (_match:string,suffix:string|undefined)=>suffix?'${run_id'+suffix+'}':'$run_id'):target.expr}))};
}
