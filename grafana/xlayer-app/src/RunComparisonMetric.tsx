import React from 'react';
import {Context,RecordRow,appLink} from './context';
import {RunEntry,compareRuns,comparisonReason,resourceContext} from './run-explorer';

export const metricValue=(v:unknown)=>typeof v==='number'&&Number.isFinite(v)?v!==0&&Math.abs(v)<0.001?v.toPrecision(3):v.toLocaleString(undefined,{maximumFractionDigits:3}):'N/A';
type ComparisonMetric=ReturnType<typeof compareRuns>['metrics'][number];

function ResourceLinks({run,observation,entity,context}:{run:RunEntry;observation?:RecordRow;entity:RecordRow;context:Context}){
 const selected=resourceContext(run,observation,entity,context);
 if(!selected)return <small>관측 시간 범위가 없어 이동할 수 없습니다</small>;
 return <small><span title={`${new Date(Number(selected.from)).toISOString()} → ${new Date(Number(selected.to)).toISOString()}`}>
  {String(observation?.trigger||'Trigger unknown')} · {new Date(Number(selected.from)).toLocaleTimeString()} → {new Date(Number(selected.to)).toLocaleTimeString()}</span>
  <br/><a href={appLink('investigate',selected)}>{run.run_id}: Resource Evidence →</a>
  <br/><a href={appLink('timeline',selected)}>Resource Timeline →</a></small>;
}

export function RunComparisonMetric({row,a,b,context}:{row:ComparisonMetric;a:RunEntry;b:RunEntry;context:Context}){
 const resource=row.scope!=='application';
 return <tr>
  <td>{row.metric}<small className="xlt-entity" title={JSON.stringify(row.entity)}>{Object.entries(row.entity||{}).filter(([,v])=>v!==null&&v!==undefined).map(([key,v])=>`${key}=${v}`).join(' · ')}</small></td>
  <td>{metricValue(row.a)} {row.a_unit||''}{resource&&row.a!==undefined&&<ResourceLinks run={a} observation={row.a_observation} entity={row.entity} context={context}/>}</td>
  <td>{metricValue(row.b)} {row.b_unit||''}{resource&&row.b!==undefined&&<ResourceLinks run={b} observation={row.b_observation} entity={row.entity} context={context}/>}</td>
  <td>{row.unit==='%'&&row.delta!==undefined?`${metricValue(row.delta)} pp`:row.delta_percent===undefined?'N/A':`${row.delta_percent>=0?'+':''}${metricValue(row.delta_percent)}%`}</td>
  <td>{row.scope} · {row.statistic||'Unknown statistic'}<small>{row.reasons.map(comparisonReason).join(' · ')||'기록된 조건이 일치합니다. 원인 판정이 아닙니다.'}</small></td>
 </tr>;
}
