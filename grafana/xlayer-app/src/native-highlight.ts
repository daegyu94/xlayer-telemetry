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
