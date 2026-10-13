import type {DataFrame} from '@grafana/data';

// Shorten display names only; keep the original labels and series identities.
export function compactRunLegends(frames:DataFrame[],targets:ReadonlyArray<{refId?:string;legendFormat?:string}>):DataFrame[]{
 const fields=frames.flatMap(frame=>frame.fields.filter(field=>field.type==='number'));
 const redundant=new Set(['cluster','node','instance','run_id','producer','model_name','SessionName','component','engine_id'].filter(key=>
  fields.length>0&&!!fields[0].labels?.[key]&&fields.every(field=>field.labels?.[key]===fields[0].labels?.[key])));
 return frames.map(frame=>({...frame,fields:frame.fields.map(field=>{
  const format=targets.find(target=>target.refId===frame.refId)?.legendFormat;
  if(field.type!=='number'||!format)return field;
  const name=format.replace(/\{\{([^}]+)\}\}/g,(_token,key:string)=>redundant.has(key)?'':field.labels?.[key]??'')
   .replace(/engine=/g,'').replace(/external_\b/g,'external').replace(/\bworker\s+/g,'')
   .replace(/\s*[/·]\s*(?=[/·]|$)/g,'').replace(/^\s*[/·]\s*/,'').replace(/\s+/g,' ').trim();
  return name?{...field,config:{...field.config,displayName:name},state:undefined}:field;
 })}));
}
