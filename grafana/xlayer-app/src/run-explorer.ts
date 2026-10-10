import {Context, RecordRow, appLink, numeric} from './context';

export type RunMetric={metric:string;value:number;unit?:string;scope:string;statistic?:string;entity:RecordRow;quality:string;accuracy?:string;workload_observed?:boolean;workload_shapes?:unknown[];observation?:string;exposure?:RecordRow};
export type RunEntry={key:string;run_id:string;cluster?:string;observer_node?:string;model?:string;execution_mode?:string;fingerprint?:string;steps:number;average_step?:number;from?:number;to?:number;status:string;quality:string;data_origin:string;source:string;metrics:RunMetric[];selected_step?:RecordRow;live_records?:number};
export type RunCatalog={schema_version:number;generated_at?:number;truncated?:boolean;runs:RunEntry[]};
const canonical=(value:unknown):string=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))):v);
export function metricIdentity(row:RunMetric){return canonical([row.metric,row.scope,row.entity]);}

export function compareRuns(a:RunEntry,b:RunEntry){
 const reasons:string[]=[];
 for(const key of ['model','cluster','execution_mode','data_origin'] as const){
  if(!a[key]||!b[key]||a[key]==='unknown'||b[key]==='unknown')reasons.push(`${key}_missing`);
  else if(a[key]!==b[key])reasons.push(`${key}_mismatch`);
 }
 if(!a.fingerprint||!b.fingerprint)reasons.push('workload_fingerprint_missing');else if(a.fingerprint!==b.fingerprint)reasons.push('workload_fingerprint_mismatch');
 if(a.quality!=='complete'||b.quality!=='complete')reasons.push('artifact_coverage_partial');
 if(!a.metrics.length||!b.metrics.length)reasons.push('saved_metric_missing');
 let comparability=reasons.some(r=>r.endsWith('_mismatch'))?'Incomparable':reasons.length?'Partial':'Verified';
 const metrics=a.metrics.map(before=>{
  const after=b.metrics.find(row=>metricIdentity(row)===metricIdentity(before));const blocked=[...reasons];
  if(!after)blocked.push('matching_entity_missing');else{
   for(const key of ['unit','statistic','accuracy'] as const)if(before[key]!==after[key])blocked.push(`${key}_mismatch`);
   if(before.quality!=='complete'||after.quality!=='complete')blocked.push('metric_quality_incomplete');
   if(before.scope!=='application'&&/max|min|p95|p99/.test(before.statistic||'')){
    if(!before.exposure?.interval_seconds||!before.exposure?.evaluation_count||canonical(before.exposure)!==canonical(after.exposure))blocked.push('window_exposure_or_population_mismatch');
   }
   if(before.scope==='application'){
    if(!before.workload_observed||!after.workload_observed)blocked.push('workload_observation_missing');
    else if(canonical(before.workload_shapes)!==canonical(after.workload_shapes))blocked.push('recorded_workload_mismatch');
   }
  }
  const av=numeric(before.value),bv=numeric(after?.value);
  const delta=!blocked.length&&av!==undefined&&bv!==undefined?bv-av:undefined;
  return {...before,a:av,b:bv,delta,delta_percent:delta!==undefined&&av!==0?100*delta/Math.abs(av!):undefined,reasons:blocked.length?blocked:av===0?['baseline_zero']:[]};
 });
 for(const after of b.metrics)if(!a.metrics.some(row=>metricIdentity(row)===metricIdentity(after)))metrics.push({...after,a:undefined,b:numeric(after.value),delta:undefined,delta_percent:undefined,reasons:['matching_entity_missing']});
 if(comparability==='Verified'&&metrics.some(row=>row.reasons.length&&canonical(row.reasons)!==canonical(['baseline_zero'])))comparability='Partial';
 return {comparability,reasons,metrics};
}

export function parseRunCatalog(value:any):RunCatalog{
 if(value?.schema_version!==1||!Array.isArray(value.runs)||value.runs.length>100)throw new Error('Stored Run catalog contract is invalid');
 const rows=value.runs;
 if(rows.some((row:any)=>!row||typeof row.key!=='string'||typeof row.run_id!=='string'||typeof row.status!=='string'||typeof row.source!=='string'||typeof row.quality!=='string'||typeof row.data_origin!=='string'
  ||typeof row.steps!=='number'||!Number.isInteger(row.steps)||row.steps<0||row.model!==undefined&&row.model!==null&&typeof row.model!=='string'
  ||!Array.isArray(row.metrics)||row.metrics.length>100||row.metrics.some((m:any)=>!m||typeof m.metric!=='string'||typeof m.scope!=='string'||typeof m.quality!=='string'||typeof m.value!=='number'||!Number.isFinite(m.value)||!m.entity||typeof m.entity!=='object'||Array.isArray(m.entity))))throw new Error('Stored Run catalog rows are invalid');
 if(new Set(rows.map((row:RunEntry)=>row.key)).size!==rows.length)throw new Error('Stored Run identity is ambiguous');
 return {...value,runs:rows};
}

export function liveRuns(rows:RecordRow[]):RunEntry[]{
 const groups=new Map<string,RecordRow[]>();
 for(const row of rows){if(!row.run_id||row.identity_conflict)continue;const key=canonical([row.cluster,row.run_id,row.observer_node||row.node]);groups.set(key,[...(groups.get(key)||[]),row]);}
 return [...groups].map(([key,items])=>{const unique=[...new Map(items.map(row=>[row.record_id||canonical(row),row])).values()];
  const latest=[...unique].sort((a,b)=>Number(b.window_end_ms||0)-Number(a.window_end_ms||0))[0];
  const entities=new Set(unique.map(row=>canonical([row.node,row.worker_id,row.boundary_scope])));
  const durations=unique.map(row=>numeric(row.step_duration_seconds)).filter((value):value is number=>value!==undefined);
  const starts=unique.map(row=>numeric(row.window_start_ms)).filter((v):v is number=>v!==undefined);
  const ends=unique.map(row=>numeric(row.window_end_ms)).filter((v):v is number=>v!==undefined);
  return {key:'live:'+key,run_id:String(latest.run_id),cluster:latest.cluster?String(latest.cluster):undefined,
   observer_node:String(latest.observer_node||latest.node||''),steps:unique.length,average_step:entities.size===1&&durations.length?durations.reduce((a,b)=>a+b,0)/durations.length:undefined,
   from:starts.length?Math.min(...starts):undefined,to:ends.length?Math.max(...ends):undefined,
   status:'unknown',quality:'partial',data_origin:unique.some(row=>row.data_origin==='synthetic')?'synthetic':'unknown',source:'loki_window',metrics:[],selected_step:latest};});
}
export function mergeRuns(stored:RunEntry[],live:RunEntry[]){
 const same=(a:RunEntry,b:RunEntry)=>a.run_id===b.run_id&&a.cluster===b.cluster&&a.observer_node===b.observer_node;
 return [...stored.map(row=>({...row,live_records:live.find(item=>same(row,item))?.steps})),...live.filter(row=>!stored.some(item=>same(row,item)))];
}
export function runContext(run:RunEntry,context:Context,step=false):Context{
 const selected=run.selected_step||{};const start=numeric(step?selected.window_start_ms:run.from),end=numeric(step?selected.window_end_ms:run.to);
 return {...context,...(start!==undefined&&end!==undefined&&end>start?{from:String(start),to:String(end)}:{}),variables:{...context.variables,
  run_id:[run.run_id],cluster:run.cluster?[run.cluster]:[],source_node:run.observer_node?[run.observer_node]:[],
  record_id:step&&selected.record_id?[String(selected.record_id)]:[],candidate_id:[],trace_id:[],phase_worker:[],
  compare_run_a:[],compare_run_b:[]}};
}
export function comparisonLink(a:RunEntry,b:RunEntry,context:Context){return appLink('runs',{...context,variables:{...context.variables,compare_run_a:[a.key],compare_run_b:[b.key]}});}

export const reasonText:Record<string,string>={workload_fingerprint_missing:'명시한 workload fingerprint가 없습니다',workload_fingerprint_mismatch:'Workload fingerprint가 다릅니다',
 artifact_coverage_partial:'저장 artifact의 coverage가 불완전합니다',matching_entity_missing:'같은 scope/entity의 관측이 없습니다',metric_quality_incomplete:'Sampling·clock·source 품질을 확인할 수 없습니다',
 recorded_workload_mismatch:'기록된 workload 모집단이 다릅니다',workload_observation_missing:'기록된 workload field가 부족합니다',baseline_zero:'Run A가 0이므로 상대 변화율을 계산하지 않습니다'};
export function comparisonReason(reason:string){return reasonText[reason]||`${reason.replace(/_/g,' ')} · 비교 조건을 확인하세요`;}
