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
 const requestEpoch=useRef(0);
 const [controllable,setControllable]=useState(false),[actionError,setActionError]=useState('');
 useEffect(()=>{let active=true;const epoch=++requestEpoch.current;const current=()=>active&&epoch===requestEpoch.current;if(!catalog)setStatus('loading');
  const backend=getBackendSrv();
  backend.get('/api/plugin-proxy/xlayer-telemetry-app/demo-control/status',undefined,undefined,{showErrorAlert:false})
   .then(raw=>{if(current()){setCatalog(parseDemoCatalog(raw));setControllable(true);setStatus('ready');}})
   .catch(()=>backend.get('/api/dashboards/uid/xlayer-demo-scenarios',undefined,undefined,{showErrorAlert:false})
     .then(result=>{if(current()){setCatalog(parseDemoCatalog(result.dashboard?.xlayerDemoScenarios));setControllable(false);setStatus('ready');}})
     .catch(error=>{if(current()){setCatalog(undefined);setStatus(error.status===404?'unavailable':'error');}}));
  return()=>{active=false};},[refresh]);
 useEffect(()=>{if(!controllable||refresh>=120)return;const timer=window.setTimeout(()=>setRefresh(n=>n+1),2500);return()=>window.clearTimeout(timer);},[catalog,refresh,controllable]);
 const command=async(action:'start'|'replay'|'stop'|'reset',scenario?:string)=>{requestEpoch.current++;setActionError('');setCatalog(previous=>previous?{...previous,status:'requesting',observations:action==='stop'?previous.observations:[],validation:action==='stop'?previous.validation:{}}:previous);try{
  const result=await getBackendSrv().post(`/api/plugin-proxy/xlayer-telemetry-app/demo-control/${action}`,{scenario,generation:catalog?.generation});
  setCatalog(parseDemoCatalog(result));setStatus('ready');setRefresh(0);
 }catch{setActionError('시나리오 요청을 적용하지 못했습니다. 현재 상태를 새로 확인한 뒤 다시 시도하세요.');setRefresh(n=>n+1);}};
 const scenario=catalog?.scenarios.find(row=>row.id===selected)||(!selected?catalog?.scenarios.find(row=>row.id===catalog.selected)||catalog?.scenarios[0]:undefined);
 const observations=catalog?.observations.filter(row=>row.scenario===scenario?.id)||[];
 return <div className="xlt-scenario-explorer">
  <header className="xlt-header"><div><span className="xlt-badge">Synthetic · no model weights</span><h2>Scenario Explorer</h2><p>상황을 선택하고, 기록된 실행에서 조사를 시작하세요.</p></div><button onClick={()=>setRefresh(n=>n+1)} disabled={status==='loading'}>Refresh scenarios</button></header>
  {status==='loading'&&<p role="status">시나리오 기록을 불러오는 중입니다…</p>}
  {status==='unavailable'&&<section className="xlt-empty">Live Demo의 scenario catalog가 게시되지 않았습니다. 운영 Run과 metrics는 그대로 사용할 수 있습니다.</section>}
  {status==='error'&&<section role="alert" className="xlt-error">Scenario catalog를 읽지 못했습니다. Grafana 권한과 provisioning을 확인한 뒤 다시 조회하세요.</section>}
  {catalog&&<><div className="xlt-scenario-layout"><nav aria-label="Demo scenarios" className="xlt-scenario-list">{catalog.scenarios.map(row=><a key={row.id} href={appLink('demo',{...context,variables:{...context.variables,demo_scenario:[row.id]}})} aria-current={scenario?.id===row.id?'page':undefined} onClick={e=>{e.preventDefault();locationService.push(appLink('demo',{...context,variables:{...context.variables,demo_scenario:[row.id]}}));}}><small>{row.category}</small><b>{row.title}</b><span>{row.question}</span></a>)}</nav>
   <section className="xlt-scenario-selected" aria-label="Selected scenario">{scenario?<><span className="xlt-eyebrow">선택한 시나리오</span><h3>{scenario.title}</h3><p>{scenario.expected}</p><ol className="xlt-workflow-steps">{scenario.workflow.map((step,index)=><li key={step}><span>{index+1}</span>{step}</li>)}</ol>
    {controllable&&<div className="xlt-scenario-controls"><Button disabled={catalog.status==='requesting'} onClick={()=>command(catalog.selected===scenario.id?'replay':'start',scenario.id)}>{catalog.selected===scenario.id?'Replay scenario':'Run scenario'}</Button><Button variant="secondary" onClick={()=>command('stop')} disabled={!['collecting','ready','starting'].includes(catalog.status)}>Stop scenario</Button><Button variant="secondary" onClick={()=>command('reset')} disabled={catalog.status==='idle'}>Reset demo</Button><span role="status">{catalog.status} · generation {catalog.generation}</span></div>}
    {actionError&&<p role="alert">{actionError}</p>}{catalog.error&&<p role="alert">{catalog.error}</p>}
    {controllable&&<div className="xlt-validation-stages" aria-label="Validation stages">{(['generation','collection','query','diagnosis','ui'] as const).map(stage=>{const result=catalog.validation?.[stage];return <details key={stage} data-stage={stage} data-state={result?.state||'pending'}><summary>{({generation:'Data generation',collection:'Collection',query:'Query',diagnosis:'Diagnosis',ui:'UI workflow'})[stage]} <b>{result?.state||'pending'}</b></summary><p>{result?.message||'아직 검증하지 않았습니다.'}</p>{result?.checks?.map(check=><small key={check}>{check}</small>)}</details>;})}</div>}
    <details className="xlt-expected-results"><summary>Expected results & composition</summary><p>{scenario.expected_results?.duration!==undefined&&`Recorded duration: ${scenario.expected_results.duration} s. `}{scenario.expected_results?.runs&&`${scenario.expected_results.runs} isolated Runs. `}{scenario.expected_results?.boundary&&`Boundary: ${scenario.expected_results.boundary}. `}</p><p>Candidate: {scenario.expected_results?.candidate===null?'none for controlled input':scenario.expected_results?.candidate||'actual query/diagnosis; source-dependent'}</p><p>{scenario.expected_results?.composition?.join(' + ')}</p><small>검증된 조합입니다. Shared signal을 단순 합산하거나 인과관계를 생성하지 않습니다.</small></details><h4>Recorded replays</h4>{observations.length?<ul className="xlt-replays">{observations.map((row,i)=><li key={row.context.record_id+i}><b>{row.context.run_id} · {row.boundary_scope==='trainer_update'?'Trainer update':'Step'} {row.step}</b><small>{formatContextRange(Number(row.context.from),Number(row.context.to),context.timezone)}</small><div className="xlt-actions">{catalog.status==='stopped'?<span>Stopped · 기록 요약 보존, live query 종료</span>:<><a href={appLink('overview',demoObservationContext(row,context))}>Open Run →</a><a href={appLink('investigate',demoObservationContext(row,context))}>Investigate →</a></>}</div></li>)}</ul>:<p className="xlt-empty">아직 게시된 replay가 없습니다. Run scenario를 누르면 새로운 격리 실행을 시작합니다. 실제 scrape와 SDK 기록이 완료되면 이곳에 나타납니다.</p>}
    <details><summary>Replay & validation details</summary><p>같은 scenario는 같은 입력·phase duration을 반복합니다. 실제 scrape를 기다린 뒤 완료 이력을 게시하며 timestamp와 record ID는 매번 새로 생성됩니다.</p><p>{controllable?'Run / Replay는 새로운 generation과 Run ID를 생성하고, Reset은 이 demo가 만든 backend만 종료합니다. 이전 replay URL은 만료됩니다.':`Launcher option: --scenario ${scenario.id}`}</p><p>Partial telemetry는 missing/stale pair 다음에 복구 pair를 생성합니다. 시나리오 변경 시 이전 query 응답과 선택 문맥은 새 실행에 사용하지 않습니다.</p></details>
   </>:<p className="xlt-empty">공유 URL의 시나리오가 현재 catalog에 없습니다. 다른 시나리오를 선택하세요.</p>}</section></div>
   <details className="xlt-secondary-signals"><summary>Coverage & limits · synthetic / browser fixture / hardware</summary><p>{catalog.limitations}</p><p>현재 launcher: {catalog.selected} · replay {catalog.cycle+1} · {catalog.status}. 아래 fault는 browser response fixture이며 실제 backend 장애와 구분합니다.</p><ul>{catalog.boundary_tests.map(row=><li key={row.id}>{row.title} · {row.layer}<small>{row.runner}</small></li>)}</ul></details>
  </>}
 </div>;
}
