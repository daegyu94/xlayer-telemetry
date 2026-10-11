import type {PanelData,DataFrame} from '@grafana/data';
export type ResourceKind='gpu'|'disk'|'network';
export type Reduction='latest'|'mean'|'max';
export type ResourceCell={key:string;row:string;column:string;metric:string;labels:Record<string,string>;value?:number;time?:number;count:number;status:'observed'|'no-data'|'stale'|'unknown';frame:DataFrame;fieldIndex:number};
export function resourceKind(targets: Array<{expr?:string}>):ResourceKind|undefined {
 const expressions=targets.map(t=>t.expr||'').join(' ');
 if(/telemetry_gpu_|DCGM_/.test(expressions))return 'gpu';
 if(/node_disk_/.test(expressions))return 'disk';
 if(/node_network_|node_infiniband_/.test(expressions))return 'network';
 return undefined;
}
function identity(frame:DataFrame,index:number){
 const field=frame.fields[index];
 return JSON.stringify([frame.refId||'',field.name,Object.entries(field.labels||{}).sort(([a],[b])=>a.localeCompare(b))]);
}
/** Reduce each exact source series independently. No device, query or cluster pooling. */
export function resourceMatrix(data:PanelData|undefined,kind:ResourceKind,reduction:Reduction){
 const cells:ResourceCell[]=[];
 const state=data?.state==='Error'?'error':!data||!['Done','Streaming'].includes(data.state)?'loading':'ready';
 if(state==='ready')for(const frame of data!.series){
  const timestamps=frame.fields.find(f=>f.type==='time');
  frame.fields.forEach((field,fieldIndex)=>{
   if(field.type!=='number')return;
   const labels=field.labels||{},node=labels.node||labels.nodename;
   const device=kind==='gpu'?labels.gpu:labels.device;
   const column=device?device+(kind==='network'&&labels.port?' / '+labels.port:''):'Unknown device';
   const row=(labels.cluster||'Unknown cluster')+' / '+(node||'Unknown node');
   // Prometheus display names may vary by device; canonical query identifies the metric.
   const metric=JSON.stringify([frame.refId||field.name]);
   const end=Number(data!.timeRange?.to.valueOf()),start=Number(data!.timeRange?.from.valueOf());
   const points=Array.from({length:frame.length},(_,i)=>({time:timestamps?Number(timestamps.values[i]):NaN,value:field.values[i]}));
   const tail=points[points.length-1];
   const valid=points.filter(p=>Number.isFinite(p.time)&&(!Number.isFinite(start)||p.time>=start)&&(!Number.isFinite(end)||p.time<=end)&&typeof p.value==='number'&&Number.isFinite(p.value));
   const current=tail&&typeof tail.value==='number'&&Number.isFinite(tail.value)?tail.value:undefined;
   const value=reduction==='latest'?current:valid.length?reduction==='max'?Math.max(...valid.map(p=>p.value as number)):valid.reduce((sum,p)=>sum+(p.value as number),0)/valid.length:undefined;
   const age=tail?end-tail.time:NaN;
   const status=!node||!device||(labels.node&&labels.nodename&&labels.node!==labels.nodename)||!Number.isFinite(age)||age<0?'unknown':age>Math.max(15000,3*(data!.request?.intervalMs||0))?'stale':value===undefined?'no-data':'observed';
   cells.push({key:identity(frame,fieldIndex),row,column,metric,labels,value,time:tail?.time,count:valid.length,status,frame,fieldIndex});
  });
 }
 return {state:state==='ready'&&!cells.length?'no-data':state,cells,
  rows:[...new Set(cells.map(c=>c.row))].sort(),columns:[...new Set(cells.map(c=>c.column))].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true})),
  metrics:[...new Set(cells.map(c=>c.metric))]};
}
