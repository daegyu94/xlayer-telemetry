import type {PanelData,DataFrame} from '@grafana/data';
export type ResourceKind='gpu'|'disk'|'network';
export type Reduction='latest'|'mean'|'max';
export type ResourceDimension='gpu'|'device'|'port';
export type MatrixColor={mode:'relative'}|{mode:'fixed-percent';unit:'percent'|'percentunit';min:0;max:100};
export type ResourceMatrixProfile={kind:ResourceKind;dimension:ResourceDimension;color:MatrixColor};
export type ResourceCell={key:string;row:string;column:string;metric:string;labels:Record<string,string>;value?:number;time?:number;count:number;dimensionValid:boolean;status:'observed'|'no-data'|'stale'|'unknown';frame:DataFrame;fieldIndex:number};

const RELATIVE:MatrixColor={mode:'relative'};
const FIXED_PERCENT:MatrixColor={mode:'fixed-percent',unit:'percent',min:0,max:100};
const FIXED_RATIO:MatrixColor={mode:'fixed-percent',unit:'percentunit',min:0,max:100};

// Opt in canonical panels with a real, stable device dimension. Optional or
// aggregated signals do not become matrices because an unrelated query matches.
const PROFILES:Record<string,ResourceMatrixProfile>={
 'compute:2':{kind:'gpu',dimension:'gpu',color:FIXED_PERCENT},
 'compute:6':{kind:'gpu',dimension:'gpu',color:RELATIVE},
 'compute:8':{kind:'network',dimension:'device',color:RELATIVE},
 'compute:9':{kind:'network',dimension:'port',color:RELATIVE},
 'storage:1':{kind:'disk',dimension:'device',color:RELATIVE},
 'storage:2':{kind:'disk',dimension:'device',color:RELATIVE},
 'storage:3':{kind:'disk',dimension:'device',color:FIXED_RATIO},
 'storage:30':{kind:'disk',dimension:'device',color:RELATIVE},
 'storage:31':{kind:'disk',dimension:'device',color:RELATIVE},
 'storage:114':{kind:'network',dimension:'device',color:RELATIVE},
 'storage:115':{kind:'network',dimension:'port',color:RELATIVE},
};
export function resourceMatrixProfile(destination:string,panelId:number):ResourceMatrixProfile|undefined{return PROFILES[`${destination}:${panelId}`];}

const resourcePanelKeys=new WeakMap<object,string>();
let nextResourcePanelKey=0;
export function resourcePanelKey(panel:object){
 let key=resourcePanelKeys.get(panel);
 if(!key){key=`resource-panel-${++nextResourcePanelKey}`;resourcePanelKeys.set(panel,key);}
 return key;
}

export type ResourceMetricAvailability='loading'|'error'|'empty'|'missing'|'present';
export function resourceMetricAvailability(result:{state:string;metrics:string[]},refId:string):ResourceMetricAvailability{
 if(result.state==='loading')return 'loading';
 if(result.state==='error')return 'error';
 if(result.metrics.some(key=>{
  if(key===refId)return true;
  try{return JSON.parse(key)?.[0]===refId;}catch{return false;}
 }))return 'present';
 return result.state==='no-data'&&!result.metrics.length?'empty':'missing';
}

export function metricLabel(format:string|undefined,refId:string){
 const label=(format||'').replace(/\{\{[^}]+\}\}/g,' ').replace(/[\/·]+/g,' ').replace(/\s+/g,' ').trim();
 return label||refId;
}

export function matrixColorAlpha(value:number|undefined,color:MatrixColor,max:number,unit:string):number|undefined{
 if(value===undefined||!Number.isFinite(value))return undefined;
 if(color.mode==='fixed-percent'){
  if(unit!==color.unit)return undefined;
  const percent=unit==='percentunit'?value*100:value;
  if(percent<color.min||percent>color.max)return undefined;
  return .06+.30*(percent-color.min)/(color.max-color.min);
 }
 if(value<0)return undefined;
 return .06+.30*(max>0?value/max:0);
}
function identity(frame:DataFrame,index:number){
 const field=frame.fields[index];
 return JSON.stringify([frame.refId||'',field.name,Object.entries(field.labels||{}).sort(([a],[b])=>a.localeCompare(b))]);
}
/** Reduce each exact source series independently. No device, query or cluster pooling. */
export function resourceMatrix(data:PanelData|undefined,profile:ResourceMatrixProfile,reduction:Reduction,expectedUnit=''){
 const cells:ResourceCell[]=[];
 const state=data?.state==='Error'?'error':!data||!['Done','Streaming'].includes(data.state)?'loading':'ready';
 if(state==='ready')for(const frame of data!.series){
  const timestamps=frame.fields.find(f=>f.type==='time');
  frame.fields.forEach((field,fieldIndex)=>{
   if(field.type!=='number')return;
   const labels=field.labels||{},node=labels.node||labels.nodename;
   const device=profile.dimension==='gpu'?labels.gpu:labels.device;
   const port=profile.dimension==='port'?labels.port:undefined;
   const dimensionValue=device&&(profile.dimension!=='port'||port)?device+(port?' / '+port:''):undefined;
   const column=dimensionValue||`Unknown ${profile.dimension}`;
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
   const unit=expectedUnit||(profile.color.mode==='fixed-percent'?profile.color.unit:field.config.unit||'');
   const identityUnknown=!labels.cluster||!node||!dimensionValue||(labels.node&&labels.nodename&&labels.node!==labels.nodename);
   const status=identityUnknown||!Number.isFinite(age)||age<0?'unknown':age>Math.max(15000,3*(data!.request?.intervalMs||0))?'stale':value===undefined?'no-data':matrixColorAlpha(value,profile.color,1,unit)===undefined?'unknown':'observed';
   cells.push({key:identity(frame,fieldIndex),row,column,metric,labels,value,time:tail?.time,count:valid.length,dimensionValid:!!dimensionValue,status,frame,fieldIndex});
  });
 }
 const hasDimension=cells.some(cell=>cell.dimensionValid);
 return {state:state==='ready'&&!cells.length?'no-data':state==='ready'&&!hasDimension?'unsupported':state,cells,hasDimension,
  rows:[...new Set(cells.map(c=>c.row))].sort(),columns:[...new Set(cells.map(c=>c.column))].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true})),
  metrics:[...new Set(cells.map(c=>c.metric))]};
}
