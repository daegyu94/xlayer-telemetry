import {KpiCard,PrimaryAction} from './design-system/components';
import React from 'react';
import {Context,RecordRow,numeric,scalar,appLink,selectStep} from './context';
import {boundaryPresentation} from './presentation';
import {formatContextTime} from './time-context';
import {investigationStatus,slowSteps} from './run-health';

export function ProgressCard({selected,queryState,timezone}:{selected?:RecordRow;queryState?:string;timezone:string}){
 const boundary=boundaryPresentation(selected);
 return <KpiCard className="xlt-progress"><span className="xlt-eyebrow">Recorded progress</span>
  <strong className={selected?'':'xlt-kpi-state'}>{selected?`${boundary.label} ${scalar(selected.step)}`:queryState==='Error'?'Query error':queryState==='Loading'?'Loading…':'Not reported'}</strong>
  <small>Selected completed observation</small><small>{formatContextTime(numeric(selected?.window_end_ms),timezone)}</small>
  <small className="xlt-kpi-scope">{selected?`${scalar(selected.observer_node||selected.node)} · ${scalar(selected.worker_id)}`:'Run / completed observation을 선택하세요'}</small>
  <small>현재 process의 생존 상태는 별도입니다.</small>
 </KpiCard>;
}
export function RunBrief({selected,context,candidates,queryState,clock,onInvestigate,actionLabel,diagnosed,available}:{selected?:RecordRow;context:Context;candidates:RecordRow[];queryState?:string;clock:string;diagnosed?:boolean;available?:boolean;actionLabel?:string;onInvestigate:()=>void}){
 const status=investigationStatus(candidates,queryState,clock,diagnosed,available);
 return <section className={`xlt-run-brief xlt-brief-${status.tone}`} aria-label="Investigation starting point">
  <div><span className="xlt-eyebrow">NEXT INVESTIGATION</span><h3>{status.label}</h3><p>{status.note}</p>
   <small>저장된 구간의 signal · Correlation ≠ attribution</small>
  </div><div className="xlt-brief-action">{selected?<PrimaryAction onClick={onInvestigate}>{actionLabel||`Investigate ${boundaryPresentation(selected).label} ${scalar(selected.step)} →`}</PrimaryAction>:<a href={appLink('runs',context)}>Choose a Run →</a>}
   <small>{selected?`${scalar(selected.run_id)} · ${scalar(selected.worker_id,'Worker not reported')}`:'서로 다른 Job의 성능을 합산하지 않습니다.'}</small></div>
 </section>;
}
export function SlowSteps({rows,selected,context,onSelect}:{rows:RecordRow[];selected?:RecordRow;context:Context;onSelect:(context:Context)=>void}){
 const ranked=slowSteps(rows,selected),maximum=Math.max(...ranked.map(r=>Number(r.step_duration_seconds)),0);
 return <section className="xlt-slow-steps" aria-label="Slow observations"><div className="xlt-section"><h3>Where did time go?</h3><span className="xlt-muted">같은 실행 단위 · 느린 순서</span></div>
 <p className="xlt-muted">{selected?`${scalar(selected.run_id)} / ${scalar(selected.observer_node||selected.node)} / ${scalar(selected.worker_id)}`:'하나의 Run / Worker를 선택하세요.'} · 기록된 duration이며 anomaly 판정이나 Phase attribution이 아닙니다.</p>
 {ranked.length?<ol>{ranked.slice(0,5).map(row=><li key={String(row.record_id)+String(row.window_end_ms)}><button onClick={()=>onSelect(selectStep(row,context))}><span>{boundaryPresentation(row).label} {scalar(row.step)}</span><span className="xlt-duration-track" aria-hidden="true"><i style={{width:`${maximum?100*Number(row.step_duration_seconds)/maximum:0}%`}}/></span><b>{Number(row.step_duration_seconds).toLocaleString('en',{maximumFractionDigits:2})} s</b><span>Inspect →</span></button></li>)}</ol>:<p className="xlt-empty">비교 가능한 완료 이력이 없습니다. Boundary와 Worker가 확인된 기록이 필요합니다.</p>}
 </section>;
}
