import React from 'react';
import {numeric,scalar,type RecordRow} from './context';
import {PHASE_COLORS} from './matrix-presentation';

export function reportedDurations(row:RecordRow){
 let value=row.stage_durations_seconds;
 if(typeof value==='string'){try{value=JSON.parse(value);}catch{return [];}}
 if(!value||typeof value!=='object'||Array.isArray(value)||row.identity_conflict)return [];
 return Object.entries(value).flatMap(([phase,raw])=>{
  const seconds=numeric(raw);return seconds!==undefined&&seconds>=0?[{phase,seconds}]:[];
 }).sort((a,b)=>b.seconds-a.seconds);
}
export function ReportedDurations({row}:{row?:RecordRow}){
 const values=row?reportedDurations(row):[];if(!row||!values.length)return null;
 const max=Math.max(...values.map(value=>value.seconds));
 return <section className="xlt-reported-durations"><h3>Selected observation · reported stage durations</h3>
 <p className="xlt-muted">Step {scalar(row.step)} · {scalar(row.boundary_scope,'Scope unknown')} · {scalar(row.observer_node||row.node)} / {scalar(row.worker_id)}. 저장된 logger duration이며 execution 순서·정확한 span·전체 Step의 분할을 뜻하지 않습니다.</p>
 <dl>{values.map(value=><div key={value.phase}><dt>{value.phase.replace(/_/g,' ')}</dt><dd><span className="xlt-duration-track" aria-hidden="true"><i style={{width:`${max>0?100*value.seconds/max:0}%`,background:PHASE_COLORS[value.phase]||'var(--xl-primary)'}}/></span><b>{new Intl.NumberFormat('en',{maximumFractionDigits:3}).format(value.seconds)} s</b></dd></div>)}</dl></section>;
}
