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

export function nativeHighlightHint(entities:{labels:Record<string,string>}[]){
 const keys=['cluster','node','nodename','gpu','device','port','instance','engine','engine_id','model_name','producer','role','worker_id'];
 const resources=new Set(entities.map(entity=>JSON.stringify(keys.map(key=>entity.labels[key]||''))));
 return resources.size===1?`${entities.length} metric signals · 같은 resource의 field / query를 아래 panel에서 비교하세요`:
  entities.some(entity=>entity.labels.worker_id||entity.labels.phase)?`${entities.length} worker / phase signals · Worker / Phase를 선택해 개별 관측을 확인하세요`:
  `${resources.size} resource entities · Node / Device / Engine 필터 또는 Matrix에서 선택하세요`;
}
