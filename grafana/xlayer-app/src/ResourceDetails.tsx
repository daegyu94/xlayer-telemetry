import { useEffect, useState } from 'react';
import { LoadingState } from '@grafana/data';
import { locationService } from '@grafana/runtime';
import { VariableValueSelectWrapper, sceneGraph, TextBoxVariable, SceneDataTransformer, VizPanel } from '@grafana/scenes';
import { Catalog, findPanel, records } from './catalog';
import { Context, RecordRow, readContext, appLink, scalar } from './context';
import { compactEntity, DEEP_DIVE_SPECS, detailTabIndex } from './presentation';
import { storageMetrics, storagePlotSelection } from './storage-series';
import { parseStorageOverview, storageDetailGroups, storageSourceContext } from './storage-overview';
import { appliedPolicies, resourceContext } from './distributed';
import { replicaRows } from './replicas';
import { displaySummary, displayUiNote } from './ui-copy';
import { formatContextTime } from './time-context';
import { eventTime } from './mockup';
import { useSceneData as useData } from './scene-data';
import { format, Link, DataStatus, Native } from './view-components';
import type { Shell } from './app-scene';

export function RolloutReplicas({summary,context,catalog}:{summary?:RecordRow;context:Context;catalog:Catalog}) {
 const rows=replicaRows(summary);
 if(!rows.length)return null;
 return <section><h3>Rollout Replica Coverage</h3><p className="xlt-muted">설정된 배치를 보여줍니다. Request routing이나 resource 소유 관계를 뜻하지 않으며, engine별 sample·baseline·clock 검사를 유지합니다. 누락된 metric은 0이 아닙니다.</p>
  <div className="xlt-scroll"><table><thead><tr><th scope="col">Replica / engine</th><th scope="col">Queue / KV</th><th scope="col">Evidence quality</th><th scope="col">Observations / Next</th></tr></thead><tbody>{rows.map((r,i)=><tr key={`${r.id}-${i}`}>
   <td><b>{r.id}</b><small className="xlt-entity" title={r.instance}>{r.instance} · engine {scalar(r.identity?.engine,'not reported')}</small><small>{r.nodes.join(', ')} · configured</small><small>Serving: {r.serving.state} · sampled context</small><small>Router: {r.serving.registered===undefined?'Unknown':r.serving.registered?'Registered':'Not registered'} · in-flight {format(r.serving.inflight)}</small><small>Applied policy: {r.serving.applied===undefined||r.serving.applied===null?'Unknown':`v${scalar(r.serving.applied)}`} · worker report</small>{Object.keys(r.serving.workload).length>0&&<details><summary>Reported workload / generation</summary><small>{JSON.stringify(r.serving.workload)}</small><small>Generation: {scalar(r.serving.generation,'Unknown')}</small></details>}</td>
   <td>{format(r.signals.vllm_requests_waiting?.current)} requests<br/>{format(typeof r.signals.vllm_kv_cache_usage?.current==='number'?r.signals.vllm_kv_cache_usage.current*100:undefined,'%')} KV<small>per engine · shared service</small></td>
   <td>{r.status.replace(/_/g,' ')}<small>Clock: {r.clock} / baseline {r.baselineClock}</small><details><summary>Coverage · {r.missing.length} issues</summary>{r.missing.length?r.missing.map((issue,j)=><small key={j}>{issue}</small>):<small>응답에 포함된 source coverage입니다. 전체 scrape나 Replica lifecycle을 증명하지 않습니다.</small>}</details></td>
   <td>{r.candidates.length?r.candidates.map((c,j)=><small key={j}>{scalar(c.id)} · {scalar(c.state).replace(/_/g,' ')}</small>):<small>{r.status==='observed'?'관측된 이상이 없습니다':'판단 근거가 부족하거나 correlation이 보류됐습니다'}</small>}<Link to="stage" context={resourceContext(context,{node:r.endpoint_node,engine:r.instance})} catalog={catalog}>Inspect endpoint →</Link></td>
  </tr>)}</tbody></table></div></section>;
}

function CommonStorageOverview({model,summary,context,onThreeFS}:{model:Shell;summary?:RecordRow;context:Context;onThreeFS:()=>void}){
 const state=model.useState(),value=parseStorageOverview(summary?.storage_overview);
 const groups=[['connector','Connector RPC'],['dfs_client','Mooncake DFS client'],['master_memory','Master memory']] as const;
 return <div className="xlt-common-storage"><div className="xlt-section"><h3>Common Storage Overview</h3><span className="xlt-badge">Backend / adapter not reported</span></div>
 <DataStatus provider={state.summary}/><p className="xlt-muted">Step / Phase → KV operation → Connector / DFS client → Backend는 조사 경로입니다. 실제로 관측된 execution dependency가 아닙니다.</p>
 {!value?<p className="xlt-empty">선택한 context에 저장된 Common Storage coverage가 없습니다. Native Metric tab은 사용할 수 있으며, 3FS source가 설정돼 있다는 것만으로 Workload backend를 특정하지 않습니다.</p>:<>
 <div className="xlt-health-grid">{groups.map(([layer,title])=>{const entries=value.signals.filter(row=>row.layer===layer),observed=entries.filter(row=>row.current!==null);return <article className="xlt-card" key={layer}><b>{title}</b><span>{observed.length} / {entries.length} reported signals</span><small>{entries.every(row=>row.status==='not_configured')?'Profile not configured':'Shared service · sampled / rolling'}</small></article>;})}</div>
 <details><summary>Source coverage · values, scope, entity and quality</summary><div className="xlt-scroll"><table><thead><tr><th>Signal</th><th>Current / Baseline</th><th>State / Scope</th><th>Entity / Quality</th></tr></thead><tbody>{value.signals.map(row=><tr key={row.signal}><td>{row.signal}<small>{row.unit} · {row.statistic}</small></td><td>{format(row.current,row.unit==='seconds'?'s':row.unit)} / {format(row.baseline,row.unit==='seconds'?'s':row.unit)}</td><td>{row.status.replace(/_/g,' ')}<small>{row.scope}</small></td><td title={JSON.stringify(row.entity)}>{compactEntity(row.entity)}<small>{row.quality_issues.join(', ')||'추가 품질 안내가 없습니다'}</small>{row.entity.node&&<Link to="stage" context={storageSourceContext(context,row)} catalog={state.catalog}>Source metrics</Link>}</td></tr>)}</tbody></table></div></details>
 <p className="xlt-muted">3FS source: {value.threefs.status.replace(/_/g,' ')} · Shared-service 관측입니다. 이 Mooncake client와의 연결은 확인되지 않았습니다.</p></>}
 <button onClick={onThreeFS}>3FS Deep Dive →</button><p className="xlt-notice">Native Metric 누락은 비활성화, idle, 미지원 또는 source 장애 때문일 수 있습니다. 전달된 DFS keys/bytes에는 checksum failure가 겹칠 수 있으며, rate는 물리 IOPS나 operation 실패 확률이 아닙니다.</p>
 </div>;
}

export function DeepWorkspace({model,summary,candidate,evidence,panels,context,catalog}:{model:Shell;summary?:RecordRow;candidate?:RecordRow;evidence:RecordRow[];panels:(VizPanel|undefined)[];context:Context;catalog:Catalog}) {
 const variable=sceneGraph.lookupVariable('detail_tab',model) as TextBoxVariable,tabState=variable.useState();
 const tab=detailTabIndex(typeof tabState.value==='string'?tabState.value:context.variables.detail_tab?.[0]);
 const setTab=(index:number)=>{const current=readContext(locationService.getLocation().search);locationService.push(appLink('deep-dive',{...current,variables:{...current.variables,detail_tab:[DEEP_DIVE_SPECS[index].label]}}));};
 const proofs=evidence.filter(e=>e.candidate_id===candidate?.candidate_id),spec=DEEP_DIVE_SPECS[tab];
 const[clusterOpen,setClusterOpen]=useState(false),state=model.useState();
 const storageProofs=evidence.filter(e=>String(e.signal||'').startsWith('threefs_'));
 const groups=storageDetailGroups(DEEP_DIVE_SPECS);
 return <section className="xlt-workspace"><div className="xlt-workspace-grid"><div>
  <h3>Key Findings</h3>{candidate?<><span className="xlt-badge xlt-badge-warning">{scalar(candidate.state).replace(/_/g,' ')}</span><p>{displaySummary(findPanel(catalog.summary,3),candidate.summary)}</p>
  {proofs.filter(e=>e.evidence_type==='supporting').slice(0,3).map((e,i)=><p key={i}><b>{i+1}. {scalar(e.signal)}</b><br/>{format(e.baseline,scalar(e.unit,''))} → {format(e.current,scalar(e.unit,''))}<br/><small className="xlt-entity" title={scalar(e.entity,'Entity not reported')}>{scalar(e.observation_scope)} · {scalar(e.entity,'Entity not reported')}</small></p>)}
  <h4>Against / Missing</h4>{proofs.filter(e=>e.evidence_type==='missing'||e.evidence_type==='counter').map((e,i)=><p key={i}>{scalar(e.signal)} · {scalar(e.observation_scope)}</p>)}</>:<p className="xlt-empty">Investigate에서 Candidate를 선택하세요. 이 workspace에서 Supporting / Counter / Missing evidence를 함께 확인할 수 있습니다.</p>}
  <p className="xlt-notice">Shared evidence는 correlation입니다. Run별 소유 관계나 causal path는 확인되지 않았습니다.</p><Link to="timeline" context={context} catalog={catalog}>Detailed Timeline</Link>
 </div><div><h3>Detailed Metrics</h3>{([['Common storage',groups.common],['Backend-specific · implemented',groups.backend],['Related subsystem context',groups.context]] as const).map(([title,items])=><div key={title}><h4>{title}</h4><div className="xlt-chips">{items.map(s=>{const index=detailTabIndex(s.label);return <button key={s.label} aria-pressed={tab===index} onClick={()=>setTab(index)}>{s.label}</button>;})}</div></div>)}
  {spec.label==='3FS evidence'?<div className="xlt-storage-evidence"><StorageCollectionView model={model} context={context}/><details><summary>Saved aggregate evidence</summary><h4>3FS · saved service observations</h4>{storageProofs.length?storageProofs.map((e,i)=><p key={i}><b>{scalar(e.signal)}</b> · {scalar(e.evidence_type)}<br/>{format(e.baseline,scalar(e.unit,''))} → {format(e.current,scalar(e.unit,''))}{!e.unit&&<small>Unit이 보고되지 않았습니다</small>}<small className="xlt-entity" title={scalar(e.entity,'Entity not reported')}>{scalar(e.entity,'Entity not reported')}</small></p>):<p className="xlt-empty">이 구간에 저장된 3FS evidence가 없습니다. RPC p95나 disk mean으로 대체하지 않습니다.</p>}</details></div>:panels[tab]?<Native panel={panels[tab]}/>:<p className="xlt-empty">이 Source의 Canonical panel을 사용할 수 없습니다.</p>}
  <p className="xlt-muted">{displayUiNote(spec.note)}</p><p className="xlt-muted">Connector / DFS client 관측은 Backend에 종속되지 않습니다. 3FS Service evidence는 선택적인 별도 Source이며, 경로가 확인되지 않은 Node/device 관측은 참고 context로 유지합니다.</p>
  <div className="xlt-actions"><Link to="stage" context={context} catalog={catalog}>Full KV / Mooncake</Link><Link to="storage" context={context} catalog={catalog}>Full Storage</Link><Link to="logs" context={context} catalog={catalog}>Logs</Link><Link to="timeline" context={context} catalog={catalog}>Events / Spans</Link></div>
 </div></div><details className="xlt-storage-context" open={candidate?.component==='storage'?true:undefined}><summary>Storage context & source coverage</summary><CommonStorageOverview model={model} summary={summary} context={context} onThreeFS={()=>setTab(detailTabIndex('3FS evidence'))}/><details className="xlt-storage-cluster" onToggle={event=>setClusterOpen(event.currentTarget.open)}><summary>Storage Cluster Resources · declared DS/MDS inventory</summary><p className="xlt-notice">명시적인 Resource node mapping만 사용합니다. Exporter 가용성은 Node health나 service-to-device 경로의 증거가 아닙니다. GPU host의 Sandbox Local I/O는 Backend DS/MDS resource와 구분합니다.</p>{clusterOpen&&<><div className="xlt-storage-cluster-controls">{["storage_system","storage_node"].map(name=>{const variable=sceneGraph.lookupVariable(name,model);return variable?<VariableValueSelectWrapper key={name} variable={variable} showAlways/>:null;})}</div><div className="xlt-storage-cluster-panels">{state.storageCluster?.map((panel,index)=><Native key={index} panel={panel}/>)}</div></>}</details></details></section>;
}

function StorageCollectionView({model,context}:{model:Shell;context:Context}) {
  const state=model.useState(), sampleData=useData(state.storageSamples), statusData=useData(state.storageStatus);
  const variable=sceneGraph.lookupVariable('storage_metric',model) as TextBoxVariable, variableState=variable.useState();
  const selectedValue=variableState?.value;
  const metric=typeof selectedValue==='string'&&selectedValue?selectedValue:context.variables.storage_metric?.[0];
  const rows=records(sampleData), statuses=records(statusData);
  const options=storageMetrics(rows), selection=storagePlotSelection(rows,metric);
  useEffect(()=>{
    if(!state.storagePlot)return;
    const unit=selection.unit==='operations'?'short':selection.unit||'none';
    const config=state.storagePlot.state.fieldConfig;
    if(config?.defaults?.unit!==unit)state.storagePlot.setState({fieldConfig:{...config,defaults:{...config.defaults,unit}}});
    const transformer=state.storagePlot.state.$data;
    if(transformer instanceof SceneDataTransformer){
      const transforms=transformer.state.transformations as any[];
      const filter=transforms.find(t=>t.id==='filterByValue');
      if(filter?.options?.filters?.[0]?.config?.options?.value!==metric){
        transformer.setState({transformations:transforms.map(t=>t.id==='filterByValue'?{...t,options:{...t.options,filters:[{fieldName:'metric_name',config:{id:'equal',options:{value:metric||''}}}]}}:t)});
        transformer.reprocessTransformations();
      }
    }
  },[state.storagePlot,selection.unit,metric]);
  const messages:Record<string,string>={
    'select-metric':'Current collection-point chart에 표시할 Metric을 하나 선택하세요.',
    'no-data':'이 Metric에 유효하고 고유한 Current collection point가 없습니다. 누락된 값은 측정값 0이 아닙니다.',
    'mixed-source':'이 Metric이 여러 Source table에 있습니다. Chart 표시를 보류했습니다. 아래 원본 record를 확인하세요.',
    'mixed-unit':'반환된 record의 Source unit이 서로 다릅니다. Chart 표시를 보류했으며 값을 변환하거나 합치지 않습니다.',
    'multiple-owner':'여러 Owner observation이 선택됐습니다. Chart를 확인하려면 완료된 observation을 하나 선택하세요.'};
  return <section aria-label="3FS collection context">
    <h4>3FS collection context</h4>
    <p className="xlt-notice">Shared-service 보고이며 Phase 또는 Run별 사용량이 아닙니다. Source DateTime의 해상도는 1초입니다. Collection interval과 host별 clock coverage는 보고된 값만 사용하고 보간이나 Phase attribution을 추정하지 않습니다.</p>
    <DataStatus provider={state.storageSamples}/>
    <label>Storage metric <select className="xlt-storage-metric" aria-label="Storage metric" value={metric||''} onChange={event=>{variable.setState({value:event.target.value});(sceneGraph.lookupVariable('detail_tab',model) as TextBoxVariable).setState({value:'3FS evidence'});}}>
      <option value="">Choose metric</option>
      {metric&&!options.includes(metric)&&<option value={metric}>선택한 Metric이 반환된 record에 없습니다</option>}
      {options.map(name=><option key={name} value={name}>{name}</option>)}
    </select></label>
    {selection.state==='ready'&&state.storagePlot?<><p className="xlt-muted">Current collection point는 원본 Source timestamp를 유지합니다. Unit: {selection.unit||'Not reported (raw)'} · {selection.points.length} points. Record table의 Baseline timestamp도 변경하지 않습니다.</p><Native panel={state.storagePlot}/></>:<p className="xlt-empty">{messages[selection.state]||'Collection-point panel을 사용할 수 없습니다.'}</p>}
    {state.storageSampleTable&&<details open><summary>Original collection records · Current / Baseline</summary><Native panel={state.storageSampleTable}/></details>}
    {state.storageComparison&&<details><summary>Comparable collection windows</summary><p className="xlt-muted">Delta는 기존 Diagnosis가 보고한 값만 표시합니다. Clock / Host mapping 또는 collection semantics가 Unknown이면 Baseline이 일치한다고 판단하지 않습니다.</p><Native panel={state.storageComparison}/></details>}
    <details><summary>Saved source coverage · {statuses.length} status records</summary><DataStatus provider={state.storageStatus}/>{state.storageStatusTable&&<Native panel={state.storageStatusTable}/>}</details>
    {!rows.length&&sampleData?.state===LoadingState.Done&&<p className="xlt-empty">이 구간에 저장된 Collection point가 없습니다. 선택적인 Source가 미설정·실패·빈 응답·미지원 상태일 수 있으므로 저장된 Coverage를 확인하세요. 정상 상태를 의미하지 않습니다.</p>}
  </section>;
}

export function RelatedTabs({panels,labels}:{panels:VizPanel[];labels:string[]}){const[tab,setTab]=useState(0);return <><div className="xlt-chips">{labels.map((label,i)=><button key={label} aria-pressed={tab===i} onClick={()=>setTab(i)}>{label}</button>)}</div>{panels[tab]&&<Native panel={panels[tab]}/>}</>;}

export function PolicyLifecycle({events,context,catalog}:{events:RecordRow[];context:Context;catalog:Catalog}){
 const rows=appliedPolicies(events).filter(row=>!context.variables.run_id?.length||context.variables.run_id.includes('.*')||context.variables.run_id.includes('$__all')||context.variables.run_id.includes(String(row.run_id)));
 return <details className="xlt-completed-detail"><summary>Policy / KV Lifecycle · {new Set(rows.map(row=>`${row.cluster}/${row.run_id}/${row.node}/${row.worker_id}`)).size} workers with applied-version events</summary><p className="xlt-notice">Producer가 Worker scope로 보고한 weights.applied만 적용 경계로 사용합니다. Trainer version과 KV counter는 실제 적용 Coverage나 Causality를 증명하지 않습니다.</p>{!rows.length?<p className="xlt-empty">이 구간에 Worker의 Policy 적용 Event가 없습니다. Native weight 적용을 확인한 지점에서 명시적인 계측을 활성화하세요.</p>:<div className="xlt-scroll"><table><thead><tr><th>Applied boundary</th><th>Worker</th><th>Version</th><th>Next</th></tr></thead><tbody>{rows.slice(-12).reverse().map((row,index)=><tr key={index}><td>{formatContextTime(eventTime(row),context.timezone,'time')} · {row.boundary_accuracy==='calibrated'?'calibrated':'node clock'}</td><td>{scalar(row.node)} / {scalar(row.worker_id)}</td><td>v{scalar(row.policy_version)} · producer reported</td><td><Link to="timeline" context={{...context,variables:{...context.variables,run_id:[String(row.run_id)],trace_id:['.*'],record_id:[]}}} catalog={catalog}>Timeline / KV</Link></td></tr>)}</tbody></table></div>}</details>;
}
