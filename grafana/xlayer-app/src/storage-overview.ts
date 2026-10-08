import type {DEEP_DIVE_SPECS} from './presentation';
import type {Context} from './context';
import {resourceContext} from './distributed';

export type StorageSignal={signal:string;current:number|null;baseline:number|null;delta_percent:number|null;
 scope:string;unit:string;statistic:string;entity:Record<string,string>;status:string;quality_issues:string[];layer:string};
export type StorageOverview={backend:{status:'not_reported';adapter:null};signals:StorageSignal[];
 threefs:{status:string;scope:string;relationship_to_mooncake:'not_established'}};
const states=new Set(['observed','not_configured','no_data','query_failed','stale','timestamp_invalid','clock_unverified','insufficient_sampling']);
const finite=(v:unknown)=>v===null||(typeof v==='number'&&Number.isFinite(v));
export function parseStorageOverview(raw:unknown):StorageOverview|undefined{
 if(typeof raw!=='string'||raw.length>16384)return;
 try{
  const value=JSON.parse(raw);
  if(!value||value.backend?.status!=='not_reported'||value.backend.adapter!==null||
    value.operation_attribution!=='not_established'||!Array.isArray(value.signals)||value.signals.length>14||
    !['not_configured','observed','no_data','query_failed'].includes(value.threefs?.status)||
    value.threefs.relationship_to_mooncake!=='not_established')return;
  for(const row of value.signals){
   if(!row||typeof row.signal!=='string'||!row.signal.startsWith('mooncake_')||!states.has(row.status)||
    !['connector','dfs_client','master_memory'].includes(row.layer)||!finite(row.current)||!finite(row.baseline)||!finite(row.delta_percent)||
    row.status==='observed'&&row.current===null||typeof row.scope!=='string'||typeof row.unit!=='string'||typeof row.statistic!=='string'||
    !row.entity||Array.isArray(row.entity)||!Object.values(row.entity).every(v=>typeof v==='string')||
    !Array.isArray(row.quality_issues)||!row.quality_issues.every((v:unknown)=>typeof v==='string'))return;
  }
  return value;
 }catch{return;}
}
export function storageDetailGroups(specs:typeof DEEP_DIVE_SPECS){
 return {common:specs.filter(s=>['Connector RPC','DFS batch','DFS bytes','Failures'].includes(s.label)),
  backend:specs.filter(s=>s.label==='3FS evidence'),
  context:specs.filter(s=>!['Connector RPC','DFS batch','DFS bytes','Failures','3FS evidence'].includes(s.label))};
}
export function storageSourceContext(context:Context,signal:StorageSignal):Context{
 // The canonical engine variable filters the endpoint's instance label,
 // not its numeric engine/engine_id dimension.
 return resourceContext(context,{...signal.entity,
  ...(signal.layer==='connector'&&signal.entity.instance?{engine:signal.entity.instance}:{})});
}
