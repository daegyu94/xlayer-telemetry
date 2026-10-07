import { RecordRow, numeric } from './context';
import type { Sample } from './semantics';

export function matrixEntityKey(labels:Record<string,string>):string {
  return JSON.stringify(Object.entries(labels).sort(([a],[b])=>a.localeCompare(b)));
}
export function matrixEntities(values:Sample[]):Array<{key:string;labels:Record<string,string>}> {
  return [...new Map(values.map(p=>[matrixEntityKey(p.labels),{key:matrixEntityKey(p.labels),labels:p.labels}])).values()];
}
export function filterMatrixEntity(values:Sample[], key?:string):Sample[]{
  return key?values.filter(p=>matrixEntityKey(p.labels)===key):values;
}
export function baselineBounds(selected:RecordRow|undefined,summary:RecordRow|undefined):{start:number;end:number;record:string}|undefined {
  if(!selected||!summary||summary.workload_comparability!=='matched_configured_fields'||
    summary.record_id!==selected.record_id||summary.run_id!==selected.run_id||summary.cluster!==selected.cluster)return;
  const start=numeric(summary.baseline_start_ms),end=numeric(summary.baseline_end_ms),current=numeric(selected.window_start_ms);
  if(start===undefined||end===undefined||current===undefined||end<=start||end-current>1||end-start>3600000||!summary.baseline_record_id)return;
  return {start,end,record:String(summary.baseline_record_id)};
}
/** Prefer the datasource's expanded expression; don't guess $__rate_interval. */
export function matrixLookback(expressions:string[]):number|true {
  const durations=expressions.flatMap(expr=>[...expr.matchAll(/\[(\d+(?:\.\d+)?)(ms|s|m|h)\]/g)].map(m=>Number(m[1])*({ms:1,s:1000,m:60000,h:3600000}[m[2]]||0)));
  return durations.length?Math.max(...durations):true;
}

/** Semantic phase colors; status attention keeps amber/red separate. */
export const PHASE_COLORS:Record<string,string>={rollout:'#d79a2f',reward:'#29a282',actor_update:'#397ecc',weight_sync:'#9667c8',checkpoint_save:'#657caf',environment:'#2d98a6'};

export const SUBSYSTEM_COLORS:Record<string,string>={gpu:'#357dcc',vllm:'#9163c2',kv:'#219b88',ray:'#7270b4',network:'#3c92b4',storage:'#c28a2b',sandbox:'#be7350'};
