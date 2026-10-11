import React,{useEffect,useState,useRef} from 'react';
import {getBackendSrv,locationService} from '@grafana/runtime';
import {Button} from '@grafana/ui';
import {useLocation} from 'react-router-dom';
import {appLink,readContext} from './context';
import {DemoCatalog,parseDemoCatalog,demoObservationContext} from './demo-scenarios';
import {formatContextRange} from './time-context';

export function ScenarioExplorer(){
 const context=readContext(useLocation().search),selected=context.variables.demo_scenario?.[0];
 const[catalog,setCatalog]=useState<DemoCatalog>(),[status,setStatus]=useState('loading'),[refresh,setRefresh]=useState(0);
 const requestEpoch=useRef(0),controlKnown=useRef(false);
 const [controllable,setControllable]=useState(false),[actionError,setActionError]=useState(''),[pending,setPending]=useState(false);
 useEffect(()=>{
  let active=true,timer:ReturnType<typeof setTimeout>;
  const epoch=++requestEpoch.current,current=()=>active&&epoch===requestEpoch.current;
  const backend=getBackendSrv();
  const read=async()=>{
   try{
    let result:DemoCatalog;
    try{
     result=parseDemoCatalog(await backend.get('/api/plugin-proxy/xlayer-telemetry-app/demo-control/status',undefined,undefined,{showErrorAlert:false}));
     if(current()){controlKnown.current=true;setControllable(true);}
    }catch(error){
     // Once a live controller is known, a cached legacy catalog cannot replace it.
     if(controlKnown.current)throw error;
     const legacy=await backend.get('/api/dashboards/uid/xlayer-demo-scenarios',undefined,undefined,{showErrorAlert:false});
     result=parseDemoCatalog(legacy.dashboard?.xlayerDemoScenarios);
     if(current())setControllable(false);
    }
    if(current()){setCatalog(result);setStatus('ready');}
   }catch(error:any){
    if(current()){
     setStatus(controlKnown.current?'unverified':error?.status===404?'unavailable':'error');
     if(!controlKnown.current)setCatalog(undefined);
    }
   }finally{
    // One outstanding read at a time, including recovery after a temporary failure.
    if(current())timer=setTimeout(read,2500);
   }
  };
  if(!pending)read();
  return()=>{active=false;clearTimeout(timer);};
 },[refresh,pending]);
 const command=async(action:'start'|'replay'|'stop'|'reset',scenario?:string)=>{
  if(pending||status!=='ready')return;
  requestEpoch.current++;setPending(true);setActionError('');
  setCatalog(previous=>previous?{...previous,status:'requesting',observations:action==='stop'?previous.observations:[],validation:action==='stop'?previous.validation:{}}:previous);
  try{
   const result=await getBackendSrv().post(`/api/plugin-proxy/xlayer-telemetry-app/demo-control/${action}`,{scenario,generation:catalog?.generation});
   setCatalog(parseDemoCatalog(result));setStatus('ready');
  }catch{setActionError('시나리오 요청을 적용하지 못했습니다. 현재 상태를 다시 확인하고 있습니다.');setStatus('unverified');}
  finally{setPending(false);setRefresh(n=>n+1);}
 };
 const scenario=catalog?.scenarios.find(row=>row.id===selected)||(!selected?catalog?.scenarios.find(row=>row.id===catalog.selected)||catalog?.scenarios[0]:undefined);
 const selectedActive=!!scenario&&(!controllable||catalog?.selected===scenario.id);
 const observations=catalog?.observations.filter(row=>row.scenario===scenario?.id)||[];
 return <div className="xlt-scenario-explorer">
  <header className="xlt-header"><div><span className="xlt-badge">Synthetic · no model weights</span><h2>Scenario Explorer</h2><p>상황을 선택하고, 기록된 실행에서 조사를 시작하세요.</p></div><button onClick={()=>setRefresh(n=>n+1)} disabled={status==='loading'||pending}>Refresh scenarios</button></header>
  {status==='loading'&&<p role="status">시나리오 기록을 불러오는 중입니다…</p>}
  {status==='unavailable'&&<section className="xlt-empty">Live Demo의 scenario catalog가 게시되지 않았습니다. 운영 Run과 metrics는 그대로 사용할 수 있습니다.</section>}
  {status==='error'&&<section role="alert" className="xlt-error">Scenario catalog를 읽지 못했습니다. Grafana 권한과 provisioning을 확인한 뒤 다시 조회하세요.</section>}
  {status==='unverified'&&<p role="alert">Live Demo 연결을 확인할 수 없습니다. 마지막 기록을 표시하며 자동으로 다시 확인합니다. 검증 상태와 실행 명령은 복구 후 사용할 수 있습니다.</p>}
  {catalog&&<><div className="xlt-scenario-layout"><label className="xlt-scenario-picker">Scenario<select aria-label="Choose scenario" value={scenario?.id||''} onChange={event=>locationService.push(appLink('demo',{...context,variables:{...context.variables,demo_scenario:[event.target.value]}}))}>{!scenario&&<option value="" disabled>Choose a scenario</option>}{catalog.scenarios.map(row=><option key={row.id} value={row.id}>{row.title}</option>)}</select></label><nav aria-label="Demo scenarios" className="xlt-scenario-list">{catalog.scenarios.map(row=><a key={row.id} href={appLink('demo',{...context,variables:{...context.variables,demo_scenario:[row.id]}})} aria-current={scenario?.id===row.id?'page':undefined} onClick={e=>{e.preventDefault();locationService.push(appLink('demo',{...context,variables:{...context.variables,demo_scenario:[row.id]}}));}}><small>{row.category}</small><b>{row.title}</b><span>{row.question}</span></a>)}</nav>
   <section className="xlt-scenario-selected" aria-label="Selected scenario">{scenario?<><span className="xlt-eyebrow">선택한 시나리오</span><h3>{scenario.title}</h3><p>{scenario.expected}</p><ol className="xlt-workflow-steps">{scenario.workflow.map((step,index)=><li key={step}><span>{index+1}</span>{step}</li>)}</ol>
    {controllable&&<div className="xlt-scenario-controls"><Button disabled={pending||status!=='ready'} onClick={()=>command(catalog.selected===scenario.id?'replay':'start',scenario.id)}>{catalog.selected===scenario.id?'Replay scenario':'Run scenario'}</Button><Button variant="secondary" onClick={()=>command('stop')} disabled={pending||status!=='ready'||!selectedActive||!['collecting','ready','starting'].includes(catalog.status)}>Stop scenario</Button><Button variant="secondary" onClick={()=>command('reset')} disabled={pending||status!=='ready'||catalog.status==='idle'}>Reset demo</Button><span role="status">{status==='unverified'?'Unverified':selectedActive||!catalog.selected?catalog.status:'Not started'} · generation {catalog.generation}</span></div>}
    {controllable&&!selectedActive&&catalog.selected&&<p className="xlt-muted">현재 실행: {catalog.scenarios.find(row=>row.id===catalog.selected)?.title||catalog.selected} · 다른 시나리오입니다. Run scenario는 새 격리 실행을 시작합니다.</p>}
    {actionError&&<p role="alert">{actionError}</p>}{selectedActive&&catalog.error&&<p role="alert">{catalog.error}</p>}
    {controllable&&<div className="xlt-validation-stages" aria-label="Validation stages">{(['generation','collection','query','diagnosis','ui'] as const).map(stage=>{const result=selectedActive&&status==='ready'?catalog.validation?.[stage]:undefined;return <details key={stage} data-stage={stage} data-state={result?.state||'pending'}><summary>{({generation:'Data generation',collection:'Collection',query:'Query',diagnosis:'Diagnosis',ui:'UI workflow'})[stage]} <b>{result?.state||'pending'}</b></summary><p>{result?.message||'아직 검증하지 않았습니다.'}</p>{result?.checks?.map(check=><small key={check}>{check}</small>)}</details>;})}</div>}
    <details className="xlt-expected-results"><summary>Expected results & composition</summary><p>{scenario.expected_results?.duration!==undefined&&`Recorded duration: ${scenario.expected_results.duration} s. `}{scenario.expected_results?.runs&&`${scenario.expected_results.runs} isolated Runs. `}{scenario.expected_results?.boundary&&`Boundary: ${scenario.expected_results.boundary}. `}</p><p>Candidate: {scenario.expected_results?.candidate===null?'none for controlled input':scenario.expected_results?.candidate||'actual query/diagnosis; source-dependent'}</p><p>{scenario.expected_results?.composition?.join(' + ')}</p><small>검증된 조합입니다. Shared signal을 단순 합산하거나 인과관계를 생성하지 않습니다.</small></details><h4>Recorded replays</h4>{observations.length?<ul className="xlt-replays">{observations.map((row,i)=><li key={row.context.record_id+i}><b>{row.context.run_id} · {row.boundary_scope==='trainer_update'?'Trainer update':'Step'} {row.step}</b><small>{formatContextRange(Number(row.context.from),Number(row.context.to),context.timezone)}</small><div className="xlt-actions">{(!selectedActive||status!=='ready'||!['ready','collecting'].includes(catalog.status))?<span>기록 요약 보존 · 현재 live query를 열 수 없습니다</span>:<><a href={appLink('overview',demoObservationContext(row,context))}>Open Run →</a><a href={appLink('investigate',demoObservationContext(row,context))}>Investigate →</a></>}</div></li>)}</ul>:<p className="xlt-empty">아직 게시된 replay가 없습니다. Run scenario를 누르면 새로운 격리 실행을 시작합니다. 실제 scrape와 SDK 기록이 완료되면 이곳에 나타납니다.</p>}
    <details><summary>Replay & validation details</summary><p>같은 scenario는 같은 입력·phase duration을 반복합니다. 실제 scrape를 기다린 뒤 완료 이력을 게시하며 timestamp와 record ID는 매번 새로 생성됩니다.</p><p>{controllable?'Run / Replay는 새로운 generation과 Run ID를 생성하고, Reset은 이 demo가 만든 backend만 종료합니다. 이전 replay URL은 만료됩니다.':`Launcher option: --scenario ${scenario.id}`}</p><p>Partial telemetry는 missing/stale pair 다음에 복구 pair를 생성합니다. 시나리오 변경 시 이전 query 응답과 선택 문맥은 새 실행에 사용하지 않습니다.</p></details>
   </>:<p className="xlt-empty">공유 URL의 시나리오가 현재 catalog에 없습니다. 다른 시나리오를 선택하세요.</p>}</section></div>
   <details className="xlt-secondary-signals"><summary>Coverage & limits · synthetic / browser fixture / hardware</summary><p>{catalog.limitations}</p><p>현재 launcher: {catalog.selected} · replay {catalog.cycle+1} · {catalog.status}. 아래 fault는 browser response fixture이며 실제 backend 장애와 구분합니다.</p><ul>{catalog.boundary_tests.map(row=><li key={row.id}>{row.title} · {row.layer}<small>{row.runner}</small></li>)}</ul></details>
  </>}
 </div>;
}
