import React,{useEffect,useRef,useState} from 'react';
import type {DemoCatalog} from './demo-scenarios';

export const EXECUTION_STAGES=[
 {id:'generation',label:'Data generation'}, {id:'collection',label:'Collection'},
 {id:'query',label:'Query'}, {id:'diagnosis',label:'Diagnosis'},
] as const;
export const VALIDATION_STAGES=[...EXECUTION_STAGES,{id:'ui',label:'UI workflow'}] as const;
type StageResult=NonNullable<DemoCatalog['validation']>[string];

export function scenarioIsBusy(status:string|undefined){
 return ['requesting','starting','collecting','stopping','resetting'].includes(status||'');
}

export function ScenarioProgress({message,validation}:{message:string;validation:DemoCatalog['validation']}){
 const completed=EXECUTION_STAGES.filter(stage=>validation?.[stage.id]?.state==='passed').length;
 const hasRunning=EXECUTION_STAGES.some(stage=>validation?.[stage.id]?.state==='running');
 return <div className="xlt-scenario-progress" role="status">
  <div className="xlt-scenario-progress-caption"><span>{message}</span><small>실행 단계 {completed} / {EXECUTION_STAGES.length}</small></div>
  <div className="xlt-scenario-progress-bar" role="progressbar" aria-label="Scenario stage progress" aria-valuemin={0} aria-valuemax={EXECUTION_STAGES.length} aria-valuenow={completed} aria-valuetext={`${completed} of ${EXECUTION_STAGES.length} execution stages passed`} data-waiting={!hasRunning}>
   {EXECUTION_STAGES.map(stage=><span key={stage.id} data-state={validation?.[stage.id]?.state||'pending'} title={stage.label}/>)}
  </div>
 </div>;
}

export function ScenarioStage({stage,generation,result}:{stage:typeof VALIDATION_STAGES[number];generation?:number;result?:StageResult}){
 const state=result?.state||'pending';
 const defaultMessage=({passed:'검증을 통과했습니다.',running:'진행 중입니다.',failed:'검증에 실패했습니다.',pending:'아직 검증하지 않았습니다.'} as Record<string,string>)[state]||'검증 상태를 확인할 수 없습니다.';
 const previous=useRef({generation,state});
 const [flash,setFlash]=useState(false);
 useEffect(()=>{
  setFlash(previous.current.generation===generation&&previous.current.state!==state&&['passed','failed'].includes(state));
  previous.current={generation,state};
 },[generation,state]);
 return <details data-stage={stage.id} data-state={state} data-transition={flash?state:undefined}>
  {flash&&<span key={`${generation}/${state}`} className="xlt-stage-flash" aria-hidden="true"/>}
  <summary><span className="xlt-stage-mark" aria-hidden="true">{state==='passed'?'✓':state==='failed'?'!':state==='running'?'':'·'}</span> {stage.label}<b>{state}</b></summary>
  <p>{result?.message||defaultMessage}</p>
  {result?.checks?.map(check=><small key={check}>{check}</small>)}
 </details>;
}
