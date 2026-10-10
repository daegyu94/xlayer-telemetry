import React,{useEffect,useState} from 'react';
import {getBackendSrv,locationService} from '@grafana/runtime';
import {Icon} from '@grafana/ui';
import {Context,RecordRow,appLink} from './context';
import {RunEntry,RunCatalog,parseRunCatalog,liveRuns,mergeRuns,compareRuns,comparisonReason,runContext,comparisonLink} from './run-explorer';

const value=(v:unknown)=>typeof v==='number'&&Number.isFinite(v)?v!==0&&Math.abs(v)<0.001?v.toPrecision(3):v.toLocaleString(undefined,{maximumFractionDigits:3}):'N/A';
export function RunExplorerView({context,steps,liveState,timeWindow}:{context:Context;steps:RecordRow[];liveState?:string;timeWindow:{from:number;to:number}}){
 const [catalog,setCatalog]=useState<RunCatalog>(),[state,setState]=useState('loading'),[refresh,setRefresh]=useState(0);
 const [search,setSearch]=useState(context.variables.run_search?.[0]||''),[model,setModel]=useState(''),[status,setStatus]=useState('');
 const [selected,setSelected]=useState<string[]>([context.variables.compare_run_a?.[0],context.variables.compare_run_b?.[0]].filter((v):v is string=>!!v));
 const [copied,setCopied]=useState(false);
 const [source,setSource]=useState(''),[limitTime,setLimitTime]=useState(false);
 useEffect(()=>{let active=true;setState('loading');getBackendSrv().get('/api/dashboards/uid/xlayer-run-catalog',undefined,undefined,{showErrorAlert:false})
   .then(result=>{if(active){setCatalog(parseRunCatalog(result.dashboard?.xlayerRunCatalog));setState('ready');}})
   .catch(error=>{if(active){setCatalog(undefined);setState(error.status===404?'unavailable':'error');}});return()=>{active=false};},[refresh]);
 useEffect(()=>setSelected([context.variables.compare_run_a?.[0],context.variables.compare_run_b?.[0]].filter((v):v is string=>!!v)),[context.variables.compare_run_a?.[0],context.variables.compare_run_b?.[0]]);
 const all=mergeRuns(catalog?.runs||[],liveRuns(steps));
 const cluster=context.variables.cluster;
 const rows=all.filter(row=>!cluster?.length||cluster.some(v=>v==='.*'||v==='$__all'||v===row.cluster))
  .filter(row=>(row.run_id+' '+(row.model||'')).toLowerCase().includes(search.toLowerCase())&&(!model||row.model===model)&&(!status||row.status===status))
  .filter(row=>(!source||row.source===source)&&(!limitTime||row.from!==undefined&&row.to!==undefined&&row.to>=timeWindow.from&&row.from<=timeWindow.to))
  .sort((a,b)=>(b.to||b.from||0)-(a.to||a.from||0));
 const a=all.find(row=>row.key===selected[0]),b=all.find(row=>row.key===selected[1]);
 const comparison=a&&b?compareRuns(a,b):undefined;
 const choose=(key:string)=>{setCopied(false);const next=selected.includes(key)?selected.filter(v=>v!==key):selected.length<2?[...selected,key]:[selected[0],key];setSelected(next);
  locationService.push(appLink('runs',{...context,variables:{...context.variables,compare_run_a:next[0]?[next[0]]:[],compare_run_b:next[1]?[next[1]]:[]}}));};
 const share=async()=>{if(!a||!b)return;const link=new URL(comparisonLink(a,b,context),window.location.origin).href;
  try{await navigator.clipboard.writeText(link);setCopied(true);}catch{setCopied(false);}};
 return <div className="xlt-run-explorer">
  <section><div className="xlt-section"><h3><Icon name="search"/> Experiments</h3><button onClick={()=>setRefresh(value=>value+1)}>Refresh saved catalog</button></div>
   <p className="xlt-muted">Live observations는 현재 Cluster / Run / Time filter의 Loki 응답입니다. 저장 catalog는 게시 시점의 artifact snapshot이며 retention 이후에도 읽을 수 있습니다. Recorded running은 현재 process의 생존 확인이 아닙니다.</p>
   <div className="xlt-run-search xlt-actions"><label>Search<input aria-label="Search Runs" placeholder="Run ID or Model" value={search} onChange={e=>setSearch(e.target.value)} maxLength={256}/></label>
    <label>Model<select aria-label="Filter Model" value={model} onChange={e=>setModel(e.target.value)}><option value="">All</option>{[...new Set(all.map(row=>row.model).filter(Boolean))].map(name=><option key={name}>{name}</option>)}</select></label>
    <label>Recorded status<select aria-label="Filter Run status" value={status} onChange={e=>setStatus(e.target.value)}><option value="">All</option>{[...new Set(all.map(row=>row.status))].map(name=><option key={name}>{name}</option>)}</select></label>
    <label>Source<select aria-label="Filter Run source" value={source} onChange={e=>setSource(e.target.value)}><option value="">All</option><option value="stored_artifact">Stored artifacts</option><option value="loki_window">Live Loki window</option></select></label>
    <label><span>Saved time filter</span><span><input type="checkbox" checked={limitTime} onChange={e=>setLimitTime(e.target.checked)} aria-label="Limit saved Runs to selected time range"/> Selected time range</span></label>
    <a href={appLink('runs',{...context,variables:{...context.variables,run_id:['.*'],record_id:[],candidate_id:[]}})}>All Runs in this live window →</a>
   </div>
   {state==='loading'&&<p className="xlt-muted">저장 catalog를 불러오는 중입니다…</p>}
   {state==='error'&&<p className="xlt-error" role="alert">Saved catalog query failure. 데이터 부재나 측정값 0을 뜻하지 않습니다. Grafana 권한·provisioning 상태를 확인하세요.</p>}
   {state==='unavailable'&&<p className="xlt-notice">저장 catalog가 게시되지 않았습니다. Monitoring host에서 <code>xltel runs publish</code>를 실행하세요. Live Loki 관측은 계속 사용할 수 있습니다.</p>}
   {catalog&&<p className="xlt-muted">Stored snapshot: {catalog.generated_at?new Date(catalog.generated_at*1000).toLocaleString():'Unknown'} · {catalog.runs.length} Runs {catalog.truncated&&'· limit reached; 일부 Run이 제외됐습니다'}</p>}
   {liveState==='Error'&&<p className="xlt-error">Live Loki query failure. 저장 artifact는 별도 source로 유지됩니다.</p>}
   <div className="xlt-scroll"><table><thead><tr><th>Select</th><th>Run / Model</th><th>Recorded observations</th><th>Avg Step</th><th>Time / Status</th><th>Source / Quality</th><th>Open</th></tr></thead>
    <tbody>{rows.map(row=><tr key={row.key}>
     <td><input type="checkbox" aria-label={`Compare ${row.run_id}`} checked={selected.includes(row.key)} onChange={()=>choose(row.key)}/></td>
     <td><b>{row.run_id}</b><small>{row.model||'Model not reported'} · {row.cluster||'Cluster unknown'}</small></td>
     <td>{row.steps}<small>Retained records · 전체 학습 Step 수가 아닙니다{row.live_records!==undefined&&` · live window ${row.live_records}`}</small></td>
     <td>{value(row.average_step)} s<small>{row.source==='loki_window'?'Query-window mean':'Saved same-entity mean'} · {row.execution_mode||'Mode unknown'}</small></td>
     <td>{row.from?new Date(row.from).toLocaleString():'Time unknown'}<small>{row.status}</small></td>
     <td><span className="xlt-badge">{row.source}</span><small>{row.quality} · {row.data_origin==='synthetic'?'Synthetic':row.data_origin}</small></td>
     <td><a href={appLink('overview',runContext(row,context))}>Overview →</a><small><a href={appLink('investigate',runContext(row,context,true))}>Saved Step →</a></small></td>
    </tr>)}</tbody></table></div>
   {!rows.length&&state!=='loading'&&<p className="xlt-empty">검색 조건에 맞는 Run이 없습니다. Cluster·검색어·retention·catalog 게시 상태를 확인하세요.</p>}
  </section>
  <section aria-label="Run Comparison"><div className="xlt-section"><h3><Icon name="exchange-alt"/> Run Comparison</h3>{a&&b&&<button onClick={share}>Share{copied?' · Copied':''}</button>}</div>
   {!comparison&&<p className="xlt-empty">비교할 Run 두 개를 선택하세요. 기록이 없거나 관계가 확인되지 않은 metric은 추정하지 않습니다.</p>}
   {selected.length===2&&(!a||!b)&&state!=='loading'&&<p className="xlt-notice">공유 링크의 Run이 현재 catalog에 없습니다. 같은 catalog snapshot을 게시하거나 Run을 다시 선택하세요.</p>}
   {comparison&&a&&b&&<><p><b>Comparability: {comparison.comparability}</b> · {a.run_id} → {b.run_id}</p>
    <p className="xlt-muted">Verified는 명시한 fingerprint와 기록된 comparison field의 일치입니다. 전체 workload 동등성이나 causality를 보장하지 않습니다. Resource 값은 마지막 저장 window의 공유 관측이며 Run 평균이 아닙니다.</p>
    {comparison.reasons.map(reason=><p className="xlt-muted" key={reason}>{comparisonReason(reason)}</p>)}
    <div className="xlt-scroll"><table><thead><tr><th>Metric / Entity</th><th>Run A</th><th>Run B</th><th>Change</th><th>Scope / Statistic / Evidence quality</th></tr></thead><tbody>{comparison.metrics.map((row,i)=><tr key={i}>
     <td>{row.metric}<small className="xlt-entity" title={JSON.stringify(row.entity)}>{Object.entries(row.entity||{}).filter(([,v])=>v!==null&&v!==undefined).map(([key,v])=>`${key}=${v}`).join(' · ')}</small></td>
     <td>{value(row.a)} {row.unit||''}</td><td>{value(row.b)} {row.unit||''}</td>
     <td>{row.unit==='%'&&row.delta!==undefined?`${value(row.delta)} pp`:row.delta_percent===undefined?'N/A':`${row.delta_percent>=0?'+':''}${value(row.delta_percent)}%`}</td>
     <td>{row.scope} · {row.statistic||'Unknown statistic'}<small>{row.reasons.map(comparisonReason).join(' · ')||'기록된 조건이 일치합니다. 원인 판정이 아닙니다.'}</small></td>
    </tr>)}</tbody></table></div>
    {!comparison.metrics.length&&<p className="xlt-empty">비교할 저장 metric이 없습니다. Live window의 기록만으로 Run 전체의 수치를 만들지 않습니다.</p>}
    <div className="xlt-actions">{[a,b].map(run=><React.Fragment key={run.key}><a href={appLink('investigate',runContext(run,context,true))}>{run.run_id}: View Evidence →</a><a href={appLink('timeline',runContext(run,context,true))}>Open Timeline →</a></React.Fragment>)}</div>
    <p className="xlt-muted">원본 Loki/Prometheus가 retention으로 사라졌으면 상세 Timeline/Metric은 unavailable일 수 있습니다. 이 화면의 저장된 관측과 품질 설명은 유지됩니다.</p>
    <details><summary>Share link</summary><a href={comparisonLink(a,b,context)}>{new URL(comparisonLink(a,b,context),window.location.origin).href}</a></details>
   </>}
  </section>
 </div>;
}
