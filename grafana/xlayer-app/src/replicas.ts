import { RecordRow } from './context';

export function replicaRows(summary?: RecordRow) {
 if(!summary||summary.identity_conflict||typeof summary.cluster!=='string'||typeof summary.rollout_replicas!=='string'||summary.rollout_replicas.length>2_000_000)return [];
 let values:unknown;try{values=JSON.parse(summary.rollout_replicas);}catch{return [];}
 if(!Array.isArray(values)||values.length>16)return [];
 return values.flatMap((r:RecordRow)=>{
  if(!r||typeof r.id!=='string'||typeof r.instance!=='string'||typeof r.endpoint_node!=='string'||
   !Array.isArray(r.nodes)||r.nodes.some(n=>typeof n!=='string')||!r.nodes.includes(r.endpoint_node)||!Array.isArray(r.entities)||r.entities.length>8)return [];
  const nodes=r.nodes.map(String), clock=String(r.clock_status||'unknown'), baselineClock=String(r.baseline_clock_status||'unavailable');
  const pair=r.serving_context&&typeof r.serving_context==='object'?r.serving_context as RecordRow:{};
  const source=pair.current&&typeof pair.current==='object'?pair.current as RecordRow:{};
  const serving={state:String(source.serving_state||'unknown'),registered:typeof source.router_registered==='boolean'?source.router_registered:undefined,
   inflight:source.inflight_requests,applied:source.applied_policy_version,generation:source.generation,
   workload:source.workload&&typeof source.workload==='object'?source.workload as RecordRow:{},issues:Array.isArray(source.quality_issues)?source.quality_issues.map(String):[]};
  return (r.entities.length?r.entities:[{identity:null,signals:{},missing_sources:r.missing_sources,candidates:[]}]).flatMap((e:RecordRow)=>{
   if(!e||typeof e!=='object'||Array.isArray(e))return [];
   const identity=e.identity as Record<string,string>|null;
   if(identity&&(identity.cluster!==summary.cluster||identity.node!==r.endpoint_node||identity.instance!==r.instance))return [];
   const signals=e.signals&&typeof e.signals==='object'?e.signals as Record<string,RecordRow>:{};
   return [{id:String(r.id),instance:String(r.instance),endpoint_node:String(r.endpoint_node),nodes,clock,baselineClock,status:String(r.status||'unknown'),identity,serving,
    signals:Object.fromEntries(Object.entries(signals).filter(([,value])=>value&&typeof value==='object'&&!Array.isArray(value)).map(([name,value])=>[name,clock==='aligned'&&baselineClock==='aligned'?value:{...value,delta:null,delta_percent:null}])),
    missing:Array.isArray(e.missing_sources)?e.missing_sources.map(String):[],
    candidates:clock==='aligned'&&Array.isArray(e.candidates)?e.candidates.filter(c=>c&&typeof c==='object'&&typeof c.id==='string') as RecordRow[]:[]}];
  });
 });
}
