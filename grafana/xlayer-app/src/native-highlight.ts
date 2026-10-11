import type {PanelData} from '@grafana/data';
import {samples} from './data';
import {resolveKpiEntity} from './selection';

/** Native frame fields are distinct signals, even when they share labels. */
export function nativeHighlight(data:PanelData|undefined){
 if(data?.state==='Error')return {state:'error' as const,entities:[],sample:undefined};
 if(!data||!['Done','Streaming'].includes(data.state))return {state:'loading' as const,entities:[],sample:undefined};
 const values=(data.series||[]).flatMap(frame=>frame.fields.filter(f=>f.type==='number').flatMap(field=>samples({...data,series:[{...frame,fields:frame.fields.filter(f=>f.type==='time'||f===field)}]}).map(sample=>({...sample,labels:{...sample.labels,_field:field.name,_query:frame.refId||''}}))));
 return resolveKpiEntity(values,{scope:'resource',requireFreshness:false});
}

/** Split query/field populations before resolving an entity; never sum them. */
export function nativeHighlightSignals(data:PanelData|undefined,refs:string[]=[]){
 const groups=new Map<string,{ref:string;field:string;unit:string;series:PanelData['series']}>();
 for(const frame of data?.series||[])for(const field of frame.fields.filter(f=>f.type==='number')){
  const ref=frame.refId||'',key=JSON.stringify([ref,field.name]);
  const group=groups.get(key)||{ref,field:field.name,unit:field.config.unit||'',series:[]};
  group.series.push({...frame,fields:frame.fields.filter(f=>f.type==='time'||f===field)});groups.set(key,group);
 }
 for(const ref of new Set(refs))if(![...groups.values()].some(group=>group.ref===ref))groups.set(JSON.stringify([ref,'']),{ref,field:'',unit:'',series:[]});
 return [...groups.values()].sort((a,b)=>refs.indexOf(a.ref)-refs.indexOf(b.ref)).map(group=>({...group,result:nativeHighlight(data?{...data,series:group.series}:undefined)}));
}
