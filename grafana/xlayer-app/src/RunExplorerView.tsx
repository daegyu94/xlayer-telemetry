import React,{useEffect,useState} from 'react';
import {getBackendSrv,locationService} from '@grafana/runtime';
import {Icon} from '@grafana/ui';
import {useLocation} from 'react-router-dom';
import {Context,RecordRow,appLink,readContext} from './context';
import {RunCatalog,parseRunCatalog,liveRuns,mergeRuns,compareRuns,comparisonReason,runContext,comparisonLink,readRunFilters,withRunFilters,RunFilters,readComparisonSelection,toggleComparisonSelection,ComparisonSelection,hasSavedStep} from './run-explorer';
import {formatContextTime} from './time-context';

import {RunComparisonMetric,metricValue as value} from './RunComparisonMetric';
export function RunExplorerView({context,steps,liveState,timeWindow}:{context:Context;steps:RecordRow[];liveState?:string;timeWindow:{from:number;to:number}}){
 const [catalog,setCatalog]=useState<RunCatalog>(),[state,setState]=useState('loading'),[refresh,setRefresh]=useState(0);
 // Native Router location is authoritative, including Back and reload.
 context=readContext(useLocation().search);
 const {search,model,status,source,limitTime}=readRunFilters(context);
 const selected=readComparisonSelection(context);
 const [copyStatus,setCopyStatus]=useState<'idle'|'copied'|'failed'>('idle');
 const updateFilters=(patch:Partial<RunFilters>)=>{setCopyStatus('idle');const current=readContext(locationService.getLocation().search);
  locationService.replace(appLink('runs',withRunFilters(current,{...readRunFilters(current),...patch})));};
 useEffect(()=>{let active=true;setState('loading');getBackendSrv().get('/api/dashboards/uid/xlayer-run-catalog',undefined,undefined,{showErrorAlert:false})
   .then(result=>{if(active){setCatalog(parseRunCatalog(result.dashboard?.xlayerRunCatalog));setState('ready');}})
   .catch(error=>{if(active){setCatalog(undefined);setState(error.status===404?'unavailable':'error');}});return()=>{active=false};},[refresh]);
 useEffect(()=>setCopyStatus('idle'),[selected[0],selected[1]]);
 const all=mergeRuns(catalog?.runs||[],liveRuns(steps));
 const cluster=context.variables.cluster;
 const rows=all.filter(row=>!cluster?.length||cluster.some(v=>v==='.*'||v==='$__all'||v===row.cluster))
  .filter(row=>(row.run_id+' '+(row.model||'')).toLowerCase().includes(search.toLowerCase())&&(!model||row.model===model)&&(!status||row.status===status))
  .filter(row=>(!source||row.source===source)&&(!limitTime||row.from!==undefined&&row.to!==undefined&&row.to>=timeWindow.from&&row.from<=timeWindow.to))
  .sort((a,b)=>(b.to||b.from||0)-(a.to||a.from||0));
 const a=all.find(row=>row.key===selected[0]),b=all.find(row=>row.key===selected[1]);
 const comparison=a&&b?compareRuns(a,b):undefined;
 const writeSelection=(next:ComparisonSelection)=>{setCopyStatus('idle');const current=readContext(locationService.getLocation().search);
  locationService.push(appLink('runs',{...current,variables:{...current.variables,compare_run_a:next[0]?[next[0]]:[],compare_run_b:next[1]?[next[1]]:[]}}));};
 const choose=(key:string)=>{const current=readComparisonSelection(readContext(locationService.getLocation().search)),next=toggleComparisonSelection(current,key);if(next!==current)writeSelection(next);};
 const share=async()=>{if(!a||!b)return;const link=new URL(comparisonLink(a,b,context),window.location.origin).href;
  try{await navigator.clipboard.writeText(link);setCopyStatus('copied');}catch{setCopyStatus('failed');}};
 return <div className="xlt-run-explorer">
  <section><div className="xlt-section"><h3><Icon name="search"/> Experiments</h3><button onClick={()=>setRefresh(value=>value+1)}>Refresh saved catalog</button></div>
   <p className="xlt-muted">Live observations는 현재 Cluster / Run / Time filter의 Loki 응답입니다. 저장 catalog는 게시 시점의 artifact snapshot이며 retention 이후에도 읽을 수 있습니다. Recorded running은 현재 process의 생존 확인이 아닙니다.</p>
   <div className="xlt-run-search xlt-actions"><label>Search<input aria-label="Search Runs" placeholder="Run ID or Model" value={search} onChange={e=>updateFilters({search:e.target.value})} maxLength={256}/></label>
    <label>Model<select aria-label="Filter Model" value={model} onChange={e=>updateFilters({model:e.target.value})}><option value="">All</option>{model&&!all.some(row=>row.model===model)&&<option value={model}>{model} · catalog에서 아직 확인되지 않음</option>}{[...new Set(all.map(row=>row.model).filter(Boolean))].map(name=><option key={name}>{name}</option>)}</select></label>
    <label>Recorded status<select aria-label="Filter Run status" value={status} onChange={e=>updateFilters({status:e.target.value})}><option value="">All</option>{status&&!all.some(row=>row.status===status)&&<option value={status}>{status} · catalog에서 아직 확인되지 않음</option>}{[...new Set(all.map(row=>row.status))].map(name=><option key={name}>{name}</option>)}</select></label>
    <label>Source<select aria-label="Filter Run source" value={source} onChange={e=>updateFilters({source:e.target.value})}><option value="">All</option><option value="stored_artifact">Stored artifacts</option><option value="loki_window">Live Loki window</option></select></label>
    <label><span>Saved time filter</span><span><input type="checkbox" checked={limitTime} onChange={e=>updateFilters({limitTime:e.target.checked})} aria-label="Limit saved Runs to selected time range"/> Selected time range</span></label>
    <a href={appLink('runs',{...context,variables:{...context.variables,run_id:['.*'],record_id:[],candidate_id:[]}})}>All Runs in this live window →</a>
   </div>
   {state==='loading'&&<p className="xlt-muted">저장 catalog를 불러오는 중입니다…</p>}
   {state==='error'&&<p className="xlt-error" role="alert">Saved catalog query failure. 데이터 부재나 측정값 0을 뜻하지 않습니다. Grafana 권한·provisioning 상태를 확인하세요.</p>}
   {state==='unavailable'&&<p className="xlt-notice">저장 catalog가 게시되지 않았습니다. Monitoring host에서 <code>xltel runs publish</code>를 실행하세요. Live Loki 관측은 계속 사용할 수 있습니다.</p>}
   {catalog&&<p className="xlt-muted">Stored snapshot: {formatContextTime(typeof catalog.generated_at==='number'?catalog.generated_at*1000:undefined,context.timezone)} · {catalog.runs.length} Runs {catalog.truncated&&'· limit reached; 일부 Run이 제외됐습니다'}</p>}
   <div className="xlt-actions" aria-label="Comparison selection">{selected.map((key,index)=><div key={index}><b>Run {index===0?'A':'B'}: </b>{all.find(row=>row.key===key)?.run_id||(key?'현재 catalog에 없음':'선택하세요')}<button disabled={!key} onClick={()=>{const next:ComparisonSelection=[...selected];next[index]=undefined;writeSelection(next);}}>Clear Run {index===0?'A':'B'}</button></div>)}</div>
   {selected.every(Boolean)&&<p className="xlt-muted">다른 Run을 비교하려면 A 또는 B를 먼저 해제하세요. Filter로 숨겨진 선택도 유지됩니다.</p>}
   {rows.some(row=>row.data_origin==='synthetic')&&<p className="xlt-notice">Synthetic Run의 Model은 fixture metadata입니다. 실제 model weights의 학습이나 GPU·Storage 성능을 검증한 결과가 아닙니다.</p>}
   {liveState==='Error'&&<p className="xlt-error">Live Loki query failure. 저장 artifact는 별도 source로 유지됩니다.</p>}
   <div className="xlt-scroll"><table><thead><tr><th>Select</th><th>Run / Model</th><th>Recorded observations</th><th>Avg Step</th><th>Time / Status</th><th>Source / Quality</th><th>Open</th></tr></thead>
    <tbody>{rows.map(row=><tr key={row.key}>
     <td><input type="checkbox" aria-label={`Compare ${row.run_id}`} checked={selected.includes(row.key)} disabled={selected.every(Boolean)&&!selected.includes(row.key)} onChange={()=>choose(row.key)}/></td>
     <td><b>{row.run_id}</b><small>{row.model||'Model not reported'} · {row.cluster||'Cluster unknown'}</small></td>
     <td>{row.steps}<small>Retained records · 전체 학습 Step 수가 아닙니다{row.live_records!==undefined&&` · live window ${row.live_records}`}</small></td>
     <td>{value(row.average_step)} s<small>{row.source==='loki_window'?'Query-window mean':'Saved same-entity mean'} · {row.execution_mode||'Mode unknown'}</small></td>
     <td>{formatContextTime(row.from,context.timezone)}<small>{row.status}</small></td>
     <td><span className="xlt-badge">{row.source}</span><small>{row.quality} · {row.data_origin==='synthetic'?'Synthetic':row.data_origin}</small>{!!row.identity_conflicts&&<small role="status">{row.identity_conflicts} conflicting record IDs · 집계에서 제외했습니다</small>}</td>
     <td><a href={appLink('overview',runContext(row,context))}>Overview →</a><small>{hasSavedStep(row)?<a href={appLink('investigate',runContext(row,context,true))}>Saved Step →</a>:'Saved Step unavailable · 유효한 관측 범위가 없습니다'}</small></td>
    </tr>)}</tbody></table></div>
   {!rows.length&&state!=='loading'&&<p className="xlt-empty">검색 조건에 맞는 Run이 없습니다. Cluster·검색어·retention·catalog 게시 상태를 확인하세요.</p>}
  </section>
  <section aria-label="Run Comparison"><div className="xlt-section"><h3><Icon name="exchange-alt"/> Run Comparison</h3>{a&&b&&<button onClick={share}>Share{copyStatus==='copied'?' · Copied':''}</button>}</div>
   {copyStatus==='failed'&&<p role="alert" className="xlt-error">링크를 복사하지 못했습니다. 아래 Share link를 직접 복사하세요.</p>}
   {!comparison&&<p className="xlt-empty">비교할 Run 두 개를 선택하세요. 기록이 없거나 관계가 확인되지 않은 metric은 추정하지 않습니다.</p>}
   {selected.every(Boolean)&&(!a||!b)&&state!=='loading'&&<p className="xlt-notice">공유 링크의 Run이 현재 catalog에 없습니다. 같은 catalog snapshot을 게시하거나 Run을 다시 선택하세요.</p>}
   {comparison&&a&&b&&<><p><b>Comparability: {comparison.comparability}</b> · {a.run_id} → {b.run_id}</p>
    <p className="xlt-muted">Verified는 명시한 fingerprint와 기록된 comparison field의 일치입니다. 전체 workload 동등성이나 causality를 보장하지 않습니다. Resource 값은 마지막 저장 window의 공유 관측이며 Run 평균이 아닙니다.</p>
    {comparison.reasons.map(reason=><p className="xlt-muted" key={reason}>{comparisonReason(reason)}</p>)}
    <div className="xlt-scroll"><table><thead><tr><th>Metric / Entity</th><th>Run A</th><th>Run B</th><th>Change</th><th>Scope / Statistic / Evidence quality</th></tr></thead><tbody>{comparison.metrics.map((row,i)=><RunComparisonMetric key={i} row={row} a={a} b={b} context={context}/>)}</tbody></table></div>
    {!comparison.metrics.length&&<p className="xlt-empty">비교할 저장 metric이 없습니다. Live window의 기록만으로 Run 전체의 수치를 만들지 않습니다.</p>}
    <div className="xlt-actions">{[a,b].map(run=>hasSavedStep(run)?<React.Fragment key={run.key}><a href={appLink('investigate',runContext(run,context,true))}>{run.run_id}: Saved Step Evidence →</a><a href={appLink('timeline',runContext(run,context,true))}>Saved Step Timeline →</a></React.Fragment>:<small key={run.key}>{run.run_id}: Saved Step unavailable</small>)}</div>
    <p className="xlt-muted">원본 Loki/Prometheus가 retention으로 사라졌으면 상세 Timeline/Metric은 unavailable일 수 있습니다. 이 화면의 저장된 관측과 품질 설명은 유지됩니다.</p>
    <details open={copyStatus==='failed'?true:undefined}><summary>Share link</summary><a href={comparisonLink(a,b,context)}>{new URL(comparisonLink(a,b,context),window.location.origin).href}</a></details>
   </>}
  </section>
 </div>;
}
