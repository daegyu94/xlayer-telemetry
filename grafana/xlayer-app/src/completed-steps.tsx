import React from 'react';
import {type Context,type RecordRow,numeric,scalar,selectStep} from './context';

export function completedStepKey(row?:RecordRow):string|undefined {
  if(!row)return undefined;
  return JSON.stringify([row.cluster??null,row.run_id??null,row.observer_node||row.node||null,
    row.record_id??null,numeric(row.window_start_ms)??null,numeric(row.window_end_ms)??null]);
}
export function completedStepChoices(rows:RecordRow[]):RecordRow[] {
  const valid=rows.filter(row=>!row.identity_conflict&&typeof row.record_id==='string'&&row.record_id&&
    typeof row.run_id==='string'&&row.run_id&&numeric(row.window_start_ms)!==undefined&&
    numeric(row.window_end_ms)!>numeric(row.window_start_ms)!&&
    Math.abs(numeric(row.window_start_ms)!)<=8.64e15&&Math.abs(numeric(row.window_end_ms)!)<=8.64e15);
  return [...new Map(valid.map(row=>[completedStepKey(row),row])).values()]
    .sort((a,b)=>numeric(b.window_end_ms)!-numeric(a.window_end_ms)!);
}
export function requestedStep(rows:RecordRow[],context:Context):RecordRow|undefined {
  const record=context.variables.record_id?.[0];
  if(!record||record==='.*'||record==='$__all')return undefined;
  const matches=(values:string[]|undefined,value:unknown)=>!values?.length||values.some(v=>v==='.*'||v==='$__all')||values.includes(String(value));
  const requested=rows.filter(row=>!row.identity_conflict&&row.record_id===record&&
    matches(context.variables.cluster,row.cluster)&&matches(context.variables.run_id,row.run_id)&&
    matches(context.variables.source_node,row.observer_node||row.node));
  const unique=(values:RecordRow[])=>new Set(values.map(completedStepKey)).size===1?values[0]:undefined;
  const selected=unique(requested);
  if(selected)return selected;
  const absolute=(value:string)=>numeric(value)??(/^\d{4}-\d{2}-\d{2}T/.test(value)&&Number.isFinite(Date.parse(value))?Date.parse(value):undefined);
  const start=absolute(context.from),end=absolute(context.to);
  if(start===undefined||end===undefined)return undefined;
  return unique(requested.filter(row=>numeric(row.window_start_ms)===start&&numeric(row.window_end_ms)===end));
}

export function CompletedStepSelect({rows,selected,label,optionLabel,onStep}:{rows:RecordRow[];selected?:RecordRow;
 label:string;optionLabel:(row:RecordRow)=>string;onStep:(row:RecordRow)=>void}) {
  const choices=completedStepChoices([...rows,...(selected?[selected]:[])]).slice(0,40);
  return <label className="xlt-context-step">{label}<select aria-label="Completed Step" value={completedStepKey(selected)||''}
    onChange={event=>{const row=choices.find(row=>completedStepKey(row)===event.target.value);if(row)onStep(row);}}>
    <option value="">완료된 Observation을 선택하세요</option>
    {choices.map(row=><option key={completedStepKey(row)} value={completedStepKey(row)} data-record-id={String(row.record_id)}>
      {scalar(row.cluster,'Cluster unknown')} / {scalar(row.run_id)} · {optionLabel(row)} · {scalar(row.observer_node||row.node)} · {new Date(numeric(row.window_end_ms)!).toISOString()}
    </option>)}
  </select></label>;
}

export function CompletedStepsTable({rows,selected,context,onSelect,limit=8,format}:{rows:RecordRow[];selected?:RecordRow;
 context:Context;onSelect:(context:Context)=>void;limit?:number;format:(value:unknown,unit:string)=>string}) {
  const unique=completedStepChoices(rows);
  const multipleRuns=new Set(unique.map(row=>row.run_id)).size>1;
  const multipleClusters=new Set(unique.map(row=>row.cluster)).size>1;
  return <div className="xlt-scroll"><table><thead><tr>
    {multipleClusters&&<th scope="col">Cluster</th>}{multipleRuns&&<th scope="col">Run</th>}
    <th scope="col">Step</th><th scope="col">Duration</th><th scope="col">Boundary</th>
    <th scope="col">Observer / worker</th><th scope="col">Next</th>
  </tr></thead><tbody>{unique.slice(0,limit).map(row=><tr key={completedStepKey(row)} aria-selected={completedStepKey(row)===completedStepKey(selected)}>
    {multipleClusters&&<td>{scalar(row.cluster,'Cluster unknown')}</td>}{multipleRuns&&<td>{scalar(row.run_id)}</td>}
    <td>{scalar(row.step)}</td><td>{format(row.step_duration_seconds,'s')}</td><td>{scalar(row.boundary_accuracy)}</td>
    <td>{scalar(row.observer_node||row.node)} / {scalar(row.worker_id)}</td>
    <td><button onClick={()=>onSelect(selectStep(row,context))}>Analyze Step {scalar(row.step)} →</button></td>
  </tr>)}</tbody></table>{!unique.length&&<p className="xlt-empty">이 구간에 완료된 Step이 없습니다. Run / Time range를 선택하세요. Loki Step history는 선택적 source입니다.</p>}</div>;
}
