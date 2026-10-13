import { ScenarioExplorer } from './ScenarioExplorer';
import { candidateMetric } from './run-health';
import { ProgressCard, RunBrief, SlowSteps } from './RunBrief';
import { ResourcePanel } from './ResourcePanel';
import { completedStepKey, requestedStep, CompletedStepSelect, CompletedStepsTable } from './completed-steps';
import React, { useEffect, useState, useContext } from 'react';
import { LoadingState } from '@grafana/data';
import { locationService } from '@grafana/runtime';
import { useTheme2, Icon, Tooltip } from '@grafana/ui';
import { VariableValueSelectWrapper, sceneGraph, SceneQueryRunner, VizPanel } from '@grafana/scenes';
import { Catalog, findPanel, runner, records, samples } from './catalog';
import { VARIABLE_NAMES, Context, RecordRow, readContext, appLink, dashboardLink, Destination, selectStep, numeric, scalar, subsystemDestination, investigationKey } from './context';
import { SUBSYSTEMS, ClockProof, stepEvidenceCell } from './semantics';
import { runModelMetadata, resolveEventStep } from './selection';
import { boundaryPresentation, compactEntity } from './presentation';
import { pressureOrder, resourceContext, stepProjection } from './distributed';
import { tableRows } from './data';
import { infrastructure, resourceMetricIndices, resourceSelection } from './infrastructure';
import { InfrastructureView } from './InfrastructureView';
import { RunSources } from './RunSources';
import { RunExplorerView } from './RunExplorerView';
import { displaySummary, displayUiNote, signalPresentation } from './ui-copy';
import { formatContextTime, formatContextRange } from './time-context';
import { issueText } from './catalog-loading';
import { ComparisonWindow } from './comparison-window';
import { baselineBounds, PHASE_COLORS } from './matrix-presentation';
import { MATRIX_SPECS } from './matrix-contract';
import { eventTime, latestEntitySamples, recordedSpanErrors, reportedRollout, workerPeers } from './mockup';
import { DashboardChrome } from './DashboardChrome';
import { WORKSPACE_PAGES, themeContext } from './navigation';
import { nativeHighlight, nativeHighlightHint } from './native-highlight';
import { useSceneData as useData } from './scene-data';
import { NATIVE_PAGES, NativeWorkspace } from './native-workspace';
import { KpiCard } from './design-system/components';
import { formatObservation, format, Link, Native, DataStatus, Delta } from './view-components';
import type { Shell, Page } from './app-scene';
import { CatalogRetryContext } from './app-context';
import type { KpiSpec } from './scene-specs';
import { Kpi } from './Kpi';
import { KPI_SPECS, PRESSURE_SPECS } from './scene-specs';
import { RelatedTabs, PolicyLifecycle, RolloutReplicas, DeepWorkspace } from './ResourceDetails';
import { Matrix, EvidenceDetail } from './PhaseMatrix';

function AsyncDecision({summary}:{summary?:RecordRow}) {
 let value:RecordRow;try{value=JSON.parse(String(summary?.async_decision||'null'));}catch{return null;}
 if(!value||typeof value!=='object'||Array.isArray(value))return null;
 const fields=[['sampleable_count','Sampleable samples'],['remaining','Next-update sample gap'],['should_switch_to_rollout','Switch decision (0/1)'],['effective_switch_cost_seconds','Estimated switch cost (s)']];
 const valid=fields.filter(([key])=>typeof value[key]==='number'&&Number.isFinite(value[key])&&Number(value[key])>=0);
 if(!valid.length)return null;
 return <details className="xlt-completed-detail"><summary>Reported Async Trainer Decision</summary><p className="xlt-muted">다음 update를 위한 replay-buffer decision 기록입니다. 이 Step의 sample 대기, engine 병목 또는 sleep/wake 완료를 증명하지 않습니다.</p>{valid.map(([key,label])=><p key={key}>{label}: <b>{format(value[key])}</b></p>)}</details>;
}

export function ShellView({ model }: { model: Shell }) {
  const state = model.useState();
  const theme=useTheme2();
  const reload=useContext(CatalogRetryContext);
  const catalogFailures=Object.entries(state.catalog.issues||{}).filter(([,issue])=>issue?.state==='query-failed');
  const stepData = useData(state.steps),
    summaryData = useData(state.summary),
    compareData = useData(state.comparison),
    candidateData = useData(state.candidates),
    evidenceData = useData(state.evidence),
    spanData = useData(state.spans),
    eventData = useData(state.events),
    baselineStepData=useData(state.baselineSteps),
    baselineSpanData=useData(state.baselineSpans),
    rewardAgeData = useData(state.rewardAge),
    mfuData=useData(state.mfu),
    policyData=useData(state.policy),
    workloadData=useData(state.workload);
  const infraComponents=useData(state.infraComponents),infraEdges=useData(state.infraEdges),infraAvailability=useData(state.infraAvailability);
  const steps = records(stepData),
    summaries = records(summaryData),
    comparisons = records(compareData),
    candidates = records(candidateData),
    evidence = records(evidenceData),
    spans = records(spanData),
    events = records(eventData);
  const context = readContext(window.location.search),
    record = context.variables.record_id?.[0];
  const infrastructureModel=infrastructure(tableRows(infraComponents),tableRows(infraEdges),tableRows(infraAvailability),Date.now());
  const contextKey = investigationKey(context);
  useEffect(() => {
    // Native VizPanel activation can remount the custom view. Clear only an
    // evidence selection made in a different investigation, never on mount.
    if (state.selectedCell && state.selectedCell.contextKey !== contextKey)
      model.setState({ selectedCell: undefined });
  }, [model, contextKey, state.selectedCell]);
  const summaryEntities=new Set(summaries.filter(row=>!row.identity_conflict).map(row=>JSON.stringify([row.cluster,row.run_id,row.observer_node,row.node,row.worker_id])));
  const selected=requestedStep([...steps,...summaries],context)||
    (state.page==='overview'&&!record?.match(/[^.*]/)&&summaryEntities.size===1?
      [...summaries].filter(row=>!row.identity_conflict).sort((a,b)=>Number(b.window_end_ms)-Number(a.window_end_ms))[0]:undefined);
  const matching = (rows: RecordRow[]) => stepProjection(rows,selected,summaries);
  const boundary=boundaryPresentation(selected);
  const comparisonMeta=matching(summaries)[0];
  const correlationClock=scalar(comparisonMeta?.correlation_clock_status,'not_reported');
  const clockWithheld=correlationClock==='unsafe'||correlationClock==='unknown';
  const current = matching(comparisons).map(row=>clockWithheld?{...row,delta:null,delta_percent:null}:row),
    diagnosis = clockWithheld?[]:matching(candidates),
    proofs = matching(evidence);
  const synthetic =
    steps.some((s) => s.data_origin === "synthetic") ||
    summaries.some((s) => s.data_origin === "synthetic") ||
    spans.some((s) => (s.attributes as RecordRow)?.data_origin === "synthetic");
  const ownerRun=scalar(selected?.run_id,context.variables.run_id?.length===1?context.variables.run_id[0]:'');
  const modelMetadata = runModelMetadata(events, ownerRun, context.variables.cluster?.length === 1 ? context.variables.cluster[0] : undefined) || (typeof comparisonMeta?.run_model_identifier === 'string' ? comparisonMeta.run_model_identifier : undefined);
  const policySamples=policyData?.state===LoadingState.Error?[]:latestEntitySamples(samples(policyData)).filter(s=>s.labels.run_id===ownerRun);
  const activeWorkloads=workloadData?.state===LoadingState.Error?[]:latestEntitySamples(samples(workloadData)).filter(s=>s.labels.run_id===ownerRun&&s.value===1);
  const reportedMfu=mfuData?.state===LoadingState.Error?[]:latestEntitySamples(samples(mfuData)).filter(s=>s.labels.run_id===ownerRun);
  const navigate = (page: Page, c = context) =>
    locationService.push(appLink(page, c));
  const clockNodes=Array.isArray(comparisonMeta?.clock_required_nodes)?comparisonMeta.clock_required_nodes.filter((n:unknown):n is string=>typeof n==='string'):[];
  const clockProof:ClockProof={nodes:clockNodes,uncertainty:numeric(comparisonMeta?.correlation_clock_uncertainty_seconds),sampleAge:numeric(comparisonMeta?.correlation_clock_sample_age_seconds)};
  const baselineClockProof:ClockProof={nodes:clockNodes,uncertainty:numeric(comparisonMeta?.baseline_clock_uncertainty_seconds),sampleAge:numeric(comparisonMeta?.baseline_clock_sample_age_seconds)};
  const bounds=baselineBounds(selected,comparisonMeta);
  const comparisonKey=bounds?`${bounds.record}/${bounds.start}/${bounds.end}/${context.timezone}`:'';
  useEffect(()=>{
    if(state.page!=='analyze'||state.baselineKey===comparisonKey)return;
    if(!bounds){model.setState({baselineKey:comparisonKey,baselineMatrix:[],baselineSpans:undefined,baselineSteps:undefined});return;}
    const comparisonQuery=(key:Destination,id:number,label:string,refs?:string[])=>{
      const panel=findPanel(state.catalog[key],id);if(!panel)return undefined;
      const provider=runner(panel,refs);
      provider.setState({$timeRange:new ComparisonWindow(bounds.start,bounds.end,context.timezone),queries:provider.state.queries.map(q=>({...q,refId:`BASELINE_${label}_${q.refId}`}))});
      return provider;
    };
    model.setState({baselineKey:comparisonKey,baselineSpans:comparisonQuery('timeline',9,'spans'),baselineSteps:comparisonQuery('overview',20,'steps'),baselineMatrix:SUBSYSTEMS.map(name=>{const spec=MATRIX_SPECS[name];return !spec.rolling&&name!=='ray'?comparisonQuery(spec.dashboard,spec.panel,name,spec.refs):undefined;})});
  },[model,state.page,comparisonKey]);
  const baselineStep=records(baselineStepData).find(row=>row.record_id===bounds?.record&&row.run_id===selected?.run_id&&row.cluster===selected?.cluster);
  const baselineSpans=records(baselineSpanData);
  const workspaceCandidate=diagnosis.find(c=>c.candidate_id===context.variables.candidate_id?.[0]);
  const renderKpi=(spec:KpiSpec,index:number)=>(
<Kpi
                key={spec.name}
                spec={spec}
                provider={state.kpis[index]}
                context={context}
                boundary={boundary}
                history={spec.key?comparisons.filter(r=>r.signal===spec.key&&r.run_id===selected?.run_id&&r.observation_scope===current.find(c=>c.signal===spec.key)?.observation_scope&&r.entity===current.find(c=>c.signal===spec.key)?.entity).map(r=>({value:numeric(r.current),time:numeric(r.window_end_ms)})).filter(r=>r.value!==undefined&&r.time!==undefined) as {value:number;time:number}[]:undefined}
                ages={
                  ["Reward","Worker throughput","Step time","Reported rollout"].includes(spec.name) ? samples(rewardAgeData) : undefined
                }
                maxAge={Number(context.variables.training_max_age?.[0] || 300)}
                reported={
                  spec.name === "Reported rollout"
                    ? reportedRollout(
                        steps.find(
                          (s) =>
                            completedStepKey(s) === completedStepKey(selected),
                        ),
                      )
                    : undefined
                }
                comparison={
                  "key" in spec
                    ? current.find((c) => c.signal === spec.key)
                    : undefined
                }
                evidence={
                  "key" in spec
                    ? proofs.find((c) => c.signal === spec.key)
                    : undefined
                }
              />
  );
  return (
    <DashboardChrome synthetic={synthetic||!!context.variables.demo_generation?.[0]} page={state.page} context={context} nativePages={NATIVE_PAGES} onNavigate={route=>navigate(route as Page)} onTheme={()=>navigate(state.page,themeContext(context,theme.isDark?'light':'dark'))}
      contextBar={state.page==='demo'?null:<RunContext model={model} selected={selected} steps={steps} context={context} policySamples={policySamples} activeWorkloads={activeWorkloads} onStep={row=>navigate(state.page,selectStep(row,context))}/>}>
      {(catalogFailures.length>0||reload.error)&&<section className="xlt-notice" aria-label="Dashboard availability"><p role="alert">{reload.error?displayUiNote(reload.error):'일부 선택 Dashboard를 불러오지 못했습니다. 사용 가능한 Dashboard는 계속 사용할 수 있습니다.'}</p>{catalogFailures.map(([name,issue])=><p key={name}>{name}: {issueText(issue!)}</p>)}<button onClick={reload.retry} disabled={reload.refreshing}>Retry dashboards</button></section>}
      {state.nativeWorkspace&&<>
        <header className="xlt-header xlt-native-heading"><div><h2>{NATIVE_PAGES.find(p=>p.route===state.page)?.title}</h2><p>{NATIVE_PAGES.find(p=>p.route===state.page)?.description}</p></div><a className="xlt-link" href={appLink('infrastructure',context)}>Locate in topology →</a></header>
        <p className="xlt-notice">{NATIVE_PAGES.find(p=>p.route===state.page)?.notice}</p>
        <details className="xlt-filters"><summary>Resource and native panel filters</summary><div>{state.catalog[state.nativeWorkspace.destination]?.templating.list.filter(v=>VARIABLE_NAMES.includes(v.name)&&!['cluster','run_id'].includes(v.name)).map(v=>{const variable=sceneGraph.lookupVariable(v.name,model);return variable?<VariableValueSelectWrapper key={v.name} variable={variable} showAlways/>:null;})}</div></details>
        <NativeHighlights destination={state.nativeWorkspace.destination} sources={state.nativeWorkspace.panels} panels={state.nativePanels||[]}/>
        <NativeWorkspace workspace={state.nativeWorkspace} panels={state.nativePanels||[]} context={context} View={ResourcePanel}/>
        <footer>Correlation ≠ Attribution ≠ Causality. 상관관계만으로 소유 관계나 원인을 단정할 수 없습니다. No data는 측정값 0과 다릅니다.</footer>
      </>}
      {state.page==='demo'&&<ScenarioExplorer/>}
      {!state.nativeWorkspace&&state.page!=='demo'&&<>
      <header className={`xlt-header ${state.page==='overview'?'xlt-run-heading':''}`}>
        <div>
          <h2>
            {state.page === "overview"
              ? "Run Overview"
              : state.page === "analyze"
                ? "Phase × Subsystem Analysis"
                : state.page === "investigate"
                  ? `${boundary.label} Investigation`
                  : state.page === "timeline"
                    ? "Follow the same interval"
                  : state.page==='runs'?'Run Explorer':state.page==='infrastructure'?'Infrastructure':state.page==='logs'?'Logs & Events':workspaceCandidate?`Deep Dive: ${scalar(workspaceCandidate.component)}`:"Choose a subsystem"}
          </h2>
          {state.page!=='overview'&&<p className="xlt-page-description">{WORKSPACE_PAGES.find(p=>p.route===state.page)?.description}</p>}
          {modelMetadata && <p className="xlt-muted">Model: {modelMetadata} · Run metadata에 보고된 값입니다. 실제 weights/runtime은 검증되지 않았습니다.</p>}
          <details className="xlt-header-details"><summary>Selected observation details</summary><p>
            <b>
              {scalar(
                selected?.run_id,
                context.variables.run_id?.join(", ") || "Select a Run",
              )}
            </b>{" "}
            · {state.page === "overview" ? "Latest diagnosed " : ""}{boundary.label}{" "}
            {scalar(selected?.step, "not selected")} · Policy{" "}
            {scalar(selected?.policy_version,policySamples.length===1?format(policySamples[0].value):policySamples.length>1?'multiple sources':'not reported')} · Wrapped command{" "}
            {activeWorkloads.length===1?scalar(activeWorkloads[0].labels.state):activeWorkloads.length>1?'multiple reports':'not reported'}
          </p>
          {(policySamples.length||activeWorkloads.length)>0&&<p className="xlt-muted">Policy는 producer가 보고한 Trainer version입니다. Status는 node clock 기준 최신 wrapper 보고이며 Async Run 전체의 완료를 뜻하지 않습니다.</p>}
          {selected&&boundary.note&&<p className="xlt-muted">{displayUiNote(boundary.note)}</p>}
          {selected && (context.variables.run_id?.length!==1||context.variables.run_id[0]!==selected.run_id) && (
            <p className="xlt-muted">
              Run filter: {context.variables.run_id?.join(", ") || "All"} · this
              diagnosis가 선택한 Step의 Run은 {scalar(selected.run_id)}입니다.
            </p>
          )}
          </details>
        </div>
        <div className="xlt-header-actions">
          {selected && (
            <button
              onClick={() => navigate("analyze", selectStep(selected, context))}
            >
              Analyze {boundary.label} {scalar(selected.step)} →
            </button>
          )}
          <Link to="overview" context={context} catalog={state.catalog}>
            Detailed Run Overview
          </Link>
        </div>
      </header>
      {selected&&<p className="xlt-muted xlt-clock-quality" aria-label="Correlation clock quality">Clock quality: <b>{correlationClock.replace(/_/g,' ')}</b> · {scalar(comparisonMeta?.correlation_clock_scope,'scope not reported').replace(/_/g,' ')} · {scalar(comparisonMeta?.correlation_clock_method,'method not reported').replace(/_/g,' ')}{correlationClock!=='aligned'&&' · 정밀한 Phase correlation과 delta는 보류됩니다. Raw metrics는 계속 확인할 수 있습니다.'}</p>}

      <details className="xlt-filters">
        <summary>Trace, GPU, engine and evidence filters</summary>
        <div>
          {[
            "trace_id",
            "gpu",
            "engine",
            "record_id",
            "diagnosis_method",
            "sandbox_node",
            "worker",
            "role",
          ].map((name) => {
            const variable = sceneGraph.lookupVariable(name, model);
            return variable ? (
              <VariableValueSelectWrapper
                key={name}
                variable={variable}
                showAlways
              />
            ) : null;
          })}
        </div>
      </details>
      {state.page==='runs'&&<>
       <RunSources panels={state.runSources||[]} context={context} View={Native}/>
       <section aria-label="Cluster infrastructure"><div className="xlt-section"><h3>Cluster Infrastructure</h3><a href={appLink('infrastructure',context)}>All resources →</a></div>
        <InfrastructureView preview data={infrastructureModel} context={context}
         unavailable={infraComponents?.state===LoadingState.Error?'Query failure':!state.infraComponents?'Topology source를 사용할 수 없습니다':undefined}
         onSelect={node=>navigate('infrastructure',node.mapping==='configured'||node.type==='fabric'?resourceSelection(context,node,infrastructureModel):{...context,variables:{...context.variables,infra_component:[node.key]}})}
         metrics={()=>null} links={(_node,c)=><a href={appLink('infrastructure',c)}>Resource details →</a>}/>
       </section>
       <RunExplorerView context={context} steps={steps} liveState={stepData?.state} timeWindow={{from:sceneGraph.getTimeRange(model).state.value.from.valueOf(),to:sceneGraph.getTimeRange(model).state.value.to.valueOf()}}/>
      </>}
      {state.page === "overview" && (
        <>
          {stepData?.state===LoadingState.Done&&!steps.length&&<p className="xlt-empty">이 구간에 완료된 Step이 없습니다. Run / Time range를 선택하세요. 이력 누락을 정상 상태로 해석하지 않습니다.</p>}
          <div className="xlt-kpis xlt-primary-kpis" aria-label="Run key indicators">
            <ProgressCard selected={selected} queryState={stepData?.state} timezone={context.timezone}/>
            {KPI_SPECS.map((spec,index)=>({spec,index})).filter(({index})=>index===1||index===2).map(({spec,index})=>(
              renderKpi(spec,index)
            ))}
            <ErrorKpi spans={spans} data={spanData}/>
          </div>
          <RunBrief selected={selected} context={context} candidates={diagnosis} diagnosed={!!comparisonMeta} available={!!state.candidates} queryState={candidateData?.state} clock={correlationClock} onInvestigate={()=>navigate('investigate',selectStep(selected!,context))}/>
          <SlowSteps rows={steps} selected={selected} context={context} onSelect={c=>navigate('investigate',c)}/>
          <details className="xlt-secondary-signals"><summary>Learning & resource signals · Reward, rollout, GPU, KV, Storage</summary>
           <p className="xlt-muted">Learning signal과 shared resource는 서로 다른 scope입니다. Run의 전체 health나 자원 소유 관계를 뜻하지 않습니다.</p>
           <div className="xlt-kpis">
            {KPI_SPECS.map((spec,index)=>({spec,index})).filter(({index})=>index!==1&&index!==2).map(({spec,index})=>(
              renderKpi(spec,index)
            ))}
           </div>
          </details>
          {reportedMfu.length>0&&<p className="xlt-mfu-strip">Framework가 보고한 MFU · {reportedMfu.slice(0,3).map(s=>`${s.labels.phase}: ${format(s.value,'percentunit')} (${s.labels.worker_id})`).join(' · ')} · 완료된 logger observation</p>}
          <AsyncDecision summary={comparisonMeta}/>
          <details className="xlt-execution-details"><summary>Execution detail · Timeline, events & system signals</summary>
          <section className="xlt-overview-top"><div>
            <div className="xlt-section">
              <h3><Icon name="history"/> Agent RL Timeline</h3>
              <button onClick={() => navigate("timeline")}>
                Open timeline →
              </button>
            </div>
            <p className="xlt-muted">
              Exact / Calibrated application span입니다. 시간 구간이 없는 Event는 Phase duration이 아닙니다.
            </p>
            <div className="xlt-phase-legend">{Object.entries(PHASE_COLORS).map(([name,color])=><span key={name}><i style={{background:color}}/>{name.replace(/_/g," ")}</span>)}</div>
            <Native panel={state.timeline} />
          </div><div><h3><Icon name="list-ul"/> Recent Events</h3>
              <DataStatus provider={state.events} />
              <RecentEvents
                rows={events}
                steps={steps}
                spans={spans}
                context={context}
                catalog={state.catalog}
              />
            </div></section>
          <section className="xlt-observe-grid">
            <div>
              <h3><Icon name="chart-line"/> Related Metrics</h3>
              <p className="xlt-muted">
                같은 Time range의 Sampled / Shared signal을 비교합니다. 시간적 correlation이며 resource 소유 관계를 뜻하지 않습니다.
              </p>
              <RelatedTabs panels={state.related} labels={state.relatedLabels}/>
            </div>
            <div>
              <h3><Icon name="info-circle"/> System Signals</h3><HealthSummary candidates={diagnosis}/><p className="xlt-muted">저장된 diagnosis signal입니다. Collector UP을 의미하는 health 상태가 아닙니다.</p></div>
          </section>
          <PolicyLifecycle events={events} context={context} catalog={state.catalog}/>
          </details>
          <details className="xlt-completed-detail">
            <summary>Completed Steps · choose another investigation</summary>{" "}
            <section>
              <div className="xlt-section">
                <h3>Find a slow Step</h3>
                <span>완료된 observation입니다. 비교할 Step을 선택하세요.</span>
              </div>
              <DataStatus provider={state.steps} />
              <Steps
                rows={steps}
                selected={selected}
                context={context}
                onSelect={(c) => navigate("analyze", c)}
                limit={8}
              />
            </section>
          </details>
        </>
      )}
      {state.page === "analyze" && (
        <>
          {!selected && (
            <section>
              <h3>Select a completed Step</h3>
              <DataStatus provider={state.steps} />
              <Steps
                rows={steps}
                context={context}
                onSelect={(c) => navigate("analyze", c)}
              />
            </section>
          )}
          {selected && (
            <section>
              <div className="xlt-section">
                <h3>Phase × Subsystem</h3>
                <button onClick={() => navigate("investigate")}>
                  Compare baseline & candidates →
                </button>
              </div>
              <p className="xlt-muted">
                Measured phase 구간에서 관측한 값입니다. Sampled mean과 Shared rolling window는 참고 context이며 Phase별 resource 사용량이 아닙니다.
              </p>
              <Matrix
                model={model}
                selected={selected}
                spans={spans}
                evidence={proofs}
                baselineStep={baselineStep}
                baselineSpans={baselineSpans}
                comparability={scalar(comparisonMeta?.workload_comparability,"unverified")}
                clockStatus={correlationClock}
                clockProof={clockProof}
                baselineClockProof={baselineClockProof}
                baselineClockStatus={scalar(comparisonMeta?.baseline_clock_status,'not_reported')}
              />
            </section>
          )}
          <Pressure model={model} selected={selected} spans={spans} spanData={spanData} />
          <RolloutReplicas summary={comparisonMeta} context={context} catalog={state.catalog} />
          <section><h3>Subsystem Signals</h3><HealthSummary candidates={diagnosis}/></section><TopChanges rows={current}/><WorkerOutliers model={model} selected={selected} />
        </>
      )}
      {state.page === "investigate" && (
        <>
          {!selected && (
            <section>
              <h3>Select a completed Step</h3>
              <DataStatus provider={state.steps} />
              <Steps
                rows={steps}
                context={context}
                onSelect={(c) => navigate("investigate", c)}
              />
            </section>
          )}
          {selected && (
            <div className="xlt-investigate-grid">
              <RunBrief selected={selected} context={context} candidates={diagnosis} diagnosed={!!comparisonMeta} available={!!state.candidates} queryState={candidateData?.state} clock={correlationClock} actionLabel="Review candidates ↓" onInvestigate={()=>{const target=document.querySelector<HTMLElement>('.xlt-candidate-section');target?.focus();target?.scrollIntoView({behavior:'smooth'});}}/>
              <section className="xlt-investigation-section">
                <div className="xlt-section">
                  <h3><Icon name="exchange-alt"/> What changed?</h3>
                  <Link to="summary" context={context} catalog={state.catalog}>
                    Full Bottleneck Summary
                  </Link>
                </div>
                <p className="xlt-muted">
                  저장된 {boundary.label} 구간 · Workload comparability: {" "}
                  {scalar(
                    matching(summaries)[0]?.workload_comparability,
                    "unverified",
                  )}
                  . 기록에 없는 unit / statistic / entity는 Unknown으로 유지합니다.
                </p>
                <DataStatus provider={state.comparison} />
                <div className="xlt-scroll">
                  <table className="xlt-comparison-table" role="table" aria-label="Current and Baseline signals">
                    <thead>
                      <tr>
                        <th scope="col" role="columnheader">Signal</th>
                        <th scope="col" role="columnheader">Current</th>
                        <th scope="col" role="columnheader">Baseline</th>
                        <th scope="col" role="columnheader">Change</th>
                        <th scope="col" role="columnheader">Quality / Scope</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...current]
                        .sort(
                          (a, b) =>
                            Math.abs(Number(b.delta_percent) || 0) -
                            Math.abs(Number(a.delta_percent) || 0),
                        )
                        .map((r, i) => (
                          <tr key={i}>
                            <td title={scalar(r.signal)}><Tooltip content={signalPresentation(String(r.signal)).hint}><span tabIndex={0}>{r.signal==='step_duration_seconds'?boundary.timeLabel:signalPresentation(String(r.signal)).label}</span></Tooltip></td>
                            <td>{formatObservation(r, scalar(r.unit, ""))}</td>
                            <td>{format(r.baseline, scalar(r.unit, ""))}</td>
                            <td>
                              <Delta row={r} />
                            </td>
                            <td>
                              <details><summary>{r.quality_warnings?'Limited':r.comparison_status&&r.comparison_status!=='not_reported'?scalar(r.comparison_status):'Unknown quality'}</summary>
                                <small>{scalar(r.observation_scope)} · {scalar(r.window_statistic, "Unknown statistic")}</small>
                                <small className="xlt-entity">{scalar(r.entity,'Entity not reported')}</small>
                                {!!r.quality_warnings&&<small>{scalar(r.quality_warnings)}</small>}
                              </details>
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
                {!current.length && (
                  <p className="xlt-empty">
                    이 구간에 비교 가능한 Baseline 기록이 없습니다. 비교 결과를 임의로 추정하지 않습니다.
                  </p>
                )}
              </section>
              <p>
                <button onClick={() => navigate("analyze")}>
                  Open Phase × Subsystem →
                </button>{" "}
                <button onClick={() => navigate("timeline")}>
                  Inspect measured timeline →
                </button>
              </p>
              <section className="xlt-candidate-section" tabIndex={-1}>
                <h3><Icon name="search"/> Bottleneck Candidates</h3>
                <DataStatus provider={state.candidates} />
                <div className="xlt-candidates">
                  {[...diagnosis]
                    .sort(
                      (a, b) =>
                        Number(b.state === "strong_signal") -
                        Number(a.state === "strong_signal"),
                    )
                    .map((c, i) => (
                      <article className="xlt-card" key={i}>
                        <span
                          className={`xlt-badge ${c.state === "strong_signal" ? "xlt-badge-warning" : ""}`}
                        >
                          {scalar(c.state).replace(/_/g, " ")} · candidate
                        </span>
                        <h4>{scalar(c.component)}</h4>
                        {(c.run_relation || c.resource_attribution === 'not_established') && <p className="xlt-muted">{c.run_relation === 'configured' ? 'Run relation: 설정된 endpoint입니다. 소유 관계는 검증되지 않았습니다.' : c.run_relation === 'unlinked' ? '선택한 Run과의 연결이 확인되지 않은 Shared signal입니다.' : 'Run과의 관계가 미확인인 Shared context입니다.'}</p>}
                        <p>{displaySummary(findPanel(state.catalog.summary,3),c.summary)}</p>
                        <p className="xlt-muted">
                          Supporting{" "}
                          {
                            proofs.filter(
                              (e) =>
                                e.candidate_id === c.candidate_id &&
                                e.evidence_type === "supporting",
                            ).length
                          }{" "}
                          · Against{" "}
                          {
                            proofs.filter(
                              (e) =>
                                e.candidate_id === c.candidate_id &&
                                e.evidence_type === "counter",
                            ).length
                          }{" "}
                          · Missing{" "}
                          {
                            proofs.filter(
                              (e) =>
                                e.candidate_id === c.candidate_id &&
                                e.evidence_type === "missing",
                            ).length
                          }
                        </p>
                        <p className="xlt-muted">
                          {scalar(c.observation_scope)} · Confidence는 원인일 확률이 아닙니다.
                        </p>
                        {Boolean(c.context_status)&&<p className="xlt-notice">Replica context: {scalar(c.context_status).replace(/_/g,' ')} · 관측한 lifecycle / eligibility입니다. Resource 소유 관계를 뜻하지 않습니다.</p>}
                        <button
                          onClick={() =>
                            model.setState({
                              selectedCell: {
                                contextKey: investigationKey(context),
                                phase: "Selected Step",
                                subsystem: String(c.component),
                                cell: {
                                  ...stepEvidenceCell(
                                    proofs,
                                    String(c.component) === "compute"
                                      ? "gpu"
                                      : String(c.component),
                                  ),
                                  scope: scalar(c.observation_scope),
                                  evidence: proofs.filter(
                                    (e) => e.candidate_id === c.candidate_id,
                                  ),
                                },
                                window: { status: "missing" },
                              },
                            })
                          }
                        >
                          Open Evidence →
                        </button>
                      <div className="xlt-candidate-actions">
                        <button onClick={()=>navigate('deep-dive',{...context,variables:{...context.variables,candidate_id:[String(c.candidate_id)],detail_tab:candidateMetric(c.component)?[candidateMetric(c.component)!]:[]}})}>Deep Dive →</button>
                          <Link
                            to="timeline"
                            context={context}
                            catalog={state.catalog}
                          >
                            Timeline
                          </Link>
                          <Link
                            to={subsystemDestination(String(c.component))}
                            context={context}
                            catalog={state.catalog}
                          >
                            {String(c.component) === "storage"
                              ? "Storage"
                              : "Subsystem"}
                          </Link>
                          <Link
                            to="stage"
                            context={context}
                            catalog={state.catalog}
                          >
                            vLLM
                          </Link>
                          <Link
                            to="logs"
                            context={context}
                            catalog={state.catalog}
                          >
                            Logs
                          </Link>
                        </div>
                      </article>
                    ))}
                </div>
                {!diagnosis.length && (
                  <p className="xlt-empty">
                    저장된 Candidate가 없습니다. Diagnosis 누락을 정상 상태로 해석하지 않습니다.
                  </p>
                )}
              </section>
            </div>
          )}
        </>
      )}
      {state.page === "timeline" && (
        <>
          <section>
            <h3>Measured spans</h3>
            <Native panel={state.timeline} />
          </section>
          <section>
            <h3>Approximate completed Step window</h3>
            <Native panel={state.approximate} />
          </section>
          <div className="xlt-related">
            {state.related.map((p, i) => (
              <Native key={i} panel={p} />
            ))}
          </div>
          <Link to="timeline" context={context} catalog={state.catalog}>
            Full Cross-Layer Timeline + events
          </Link>
        </>
      )}
      {state.page === "overview" && (
        <details className="xlt-completed-detail">
          <summary>Subsystem Deep Dive · existing dashboards</summary>
          <div className="xlt-deep">
            {(
              [
                ["stage", "vLLM / KV",9],
                ["stage", "Ray",22],
                ["stage", "Sandbox",12],
                ["compute", "Compute / GPU / Network"],
                ["storage", "Storage"],
                ["timeline", "Spans / events / clock"],
                ["summary", "Baseline / Evidence"],
                ["logs", "Run Logs"],
              ] as [Destination, string, number?][]
            ).map(([to, label,panel]) => (
              <article className="xlt-card" key={label}>
                <Link to={to} panel={panel} context={context} catalog={state.catalog}>
                  {label}
                </Link>
                <p className="xlt-muted">
                  Native Dashboard에서 현재 Run / Step / Time context를 유지합니다.
                </p>
              </article>
            ))}
          </div>
        </details>
      )}
      {state.page==='infrastructure'&&<InfrastructureView data={infrastructureModel} context={context}
        unavailable={infraComponents?.state===LoadingState.Error?'Query failure':!state.infraComponents?'Canonical Topology panel을 사용할 수 없습니다':undefined}
        onSelect={node=>{const c=node.mapping==='configured'||node.type==='fabric'?resourceSelection(context,node,infrastructureModel):{...context,variables:{...context.variables,infra_component:[node.key]}};navigate('infrastructure',c);model.setState({infraSelectionVersion:(state.infraSelectionVersion||0)+1});}}
        links={(node,c)=><><Link to={node.kind==='storage'?'storage':'compute'} context={c} catalog={state.catalog}>Full resource metrics</Link><a href={appLink('deep-dive',{...c,variables:{...c.variables,candidate_id:[],detail_tab:[node.kind==='storage'?'Local I/O mean':'GPU']}})}>Deep Dive →</a></>}
        metrics={node=>resourceMetricIndices(node).map(index=>state.infraMetrics?.[index]?<Native key={index} panel={state.infraMetrics[index]}/>:<p key={index} className="xlt-empty">Canonical resource panel을 사용할 수 없습니다.</p>)}/>}
      {state.page==='logs'&&<><section><h3>Log Search</h3><p className="xlt-notice">Application Run context는 유지됩니다. Log payload / directory filter는 독립적이며, Run을 선택해도 모든 node-local log가 해당 Run에 귀속되는 것은 아닙니다.</p><div className="xlt-actions">{['log_run_id','workload','node','trace_id'].map(name=>{const variable=sceneGraph.lookupVariable(name,model);return variable?<VariableValueSelectWrapper key={name} variable={variable} showAlways/>:null;})}<Link to="logs" context={context} catalog={state.catalog}>Full Run Logs</Link></div>{state.logsPanel?<Native panel={state.logsPanel}/>:<p className="xlt-empty">Loki / Canonical Run Logs를 사용할 수 없습니다. 성공한 query의 결과가 비어 있다는 뜻은 아닙니다.</p>}</section><section><h3>Recorded Events</h3>{state.logEvents?<Native panel={state.logEvents}/>:<p className="xlt-empty">Event source를 사용할 수 없습니다. Loki와 명시적인 Event 수집을 활성화하세요.</p>}</section></>}
      {state.page === "deep-dive" && (
        <><DeepWorkspace model={model} summary={comparisonMeta} candidate={workspaceCandidate} evidence={proofs} panels={state.detailPanels} context={context} catalog={state.catalog}/><section><h3>Phase Correlation · measured intervals</h3><Native panel={state.timeline}/><p className="xlt-muted">선택한 Step과 같은 Time range의 execution interval입니다. 구간이 겹친다고 Phase의 resource 소유 관계가 확인되는 것은 아닙니다.</p></section><Pressure model={model} selected={selected} spans={spans} spanData={spanData} /><section><h3>Existing subsystem dashboards</h3><div className="xlt-actions">{(['compute','storage','stage','timeline','logs'] as Destination[]).map(to=><Link key={to} to={to} context={context} catalog={state.catalog}>{to} ↗</Link>)}</div></section></>
      )}
      {state.selectedCell && (
        <div className="xlt-evidence-layout">
          <div>
            <Native panel={state.timeline} />
            <p className="xlt-muted">
              이 구간의 Measured span boundary입니다. Resource signal은 여전히 Sampled 관측입니다.
            </p>
          </div>
          <EvidenceDetail
            selected={state.selectedCell}
            context={context}
            catalog={state.catalog}
            close={() => model.setState({ selectedCell: undefined })}
          />
        </div>
      )}
      <Coverage model={model}/>
      <footer className="xlt-muted">
        Correlation ≠ Attribution ≠ Causality. 상관관계만으로 소유 관계나 원인을 단정할 수 없습니다. No data는 측정값 0과 다릅니다.{" "}
      </footer>
      </>}
    </DashboardChrome>
  );
}

function NativeHighlights({destination,sources,panels}:{destination:Destination;sources:import('./catalog').Panel[];panels:VizPanel[]}){
 const [expanded,setExpanded]=useState(false);
 const ids:Partial<Record<Destination,number[]>>={compute:[2,6,30,9],storage:[1,2,30,31],stage:[4,9,28,60],signals:[1,2,5,6]};
 return ids[destination]?<details className="xlt-secondary-signals" onToggle={event=>setExpanded(event.currentTarget.open)}><summary>Latest query summaries · entity별 값은 아래 Matrix / Time series에서 확인하세요</summary>{expanded&&<div className="xlt-kpis xlt-native-highlights">{ids[destination]!.map(id=>{const index=sources.findIndex(p=>p.id===id);return index>=0?<NativeHighlight key={id} source={sources[index]} panel={panels[index]}/>:null;})}</div>}</details>:null;
}

function NativeHighlight({source,panel}:{source:import('./catalog').Panel;panel:VizPanel}){
 useEffect(()=>panel.activate(),[panel]);
 const data=useData(panel.state.$data),result=nativeHighlight(data);
 return <article className="xlt-card xlt-kpi" data-query-state={data?.state||'Loading'}><span className="xlt-eyebrow">{source.title}</span><strong className={result.state==='observed'?undefined:'xlt-kpi-state'}>{result.state==='observed'?format(result.sample?.value,source.fieldConfig?.defaults?.unit||result.sample?.unit||''):result.state==='multiple'?'Multiple signals':result.state==='error'?'Query failure':result.state==='loading'?'Loading':result.state==='invalid'?'Unknown':'No data'}</strong><small>{result.state==='multiple'?nativeHighlightHint(result.entities):'반환된 최신 query evaluation입니다. Source scope를 유지합니다.'}</small><small>Producer freshness는 별도 확인이 필요합니다.</small><details><summary>Source / Scope</summary><p>{source.description||'Native source 관측이며 Run별 사용량으로 귀속하지 않습니다.'}</p></details></article>;
}

function Steps({rows,selected,context,onSelect,limit=8}:{rows:RecordRow[];selected?:RecordRow;context:Context;
 onSelect:(context:Context)=>void;limit?:number}){
  return <CompletedStepsTable rows={rows} selected={selected} context={context} onSelect={onSelect} limit={limit} format={format}/>;
}

function ErrorKpi({
  spans,
  data,
}: {
  spans: RecordRow[];
  data?: import("@grafana/data").PanelData;
}) {
  const status = recordedSpanErrors(spans);
  return (
    <KpiCard>
      <span className="xlt-eyebrow xlt-kpi-label">Recorded errors<Tooltip content="조회된 span 중 error 상태의 기록 수입니다. 전체 workload 오류율은 아니며, status가 없으면 Not observed입니다."><button className="xlt-help" aria-label="Recorded errors 설명"><Icon name="info-circle" size="sm"/></button></Tooltip></span>
      <strong className={!data||data.state===LoadingState.Error||data.state===LoadingState.Loading||status.count===undefined?'xlt-kpi-state':status.count>0?'xlt-recorded-errors':undefined}>
        {data?.state === LoadingState.Error
          ? "Query error"
          : !data ? "Not configured" : data.state === LoadingState.Loading
            ? "Loading…"
            : status.count === undefined ? "Not observed" : format(status.count)}
      </strong>
      <span className="xlt-muted">관측된 Span record</span>
      <small>
        Status coverage {status.observed}/{status.total} · limit 5,000
        {status.limited ? " reached" : ""}
      </small>
      <small title="반환된 record만으로 전체 Workload error rate를 판단할 수 없습니다.">
        Coverage limited
      </small>
    </KpiCard>
  );
}

function RecentEvents({
  rows,
  steps,
  spans,
  context,
  catalog,
}: {
  rows: RecordRow[];
  steps: RecordRow[];
  spans: RecordRow[];
  context: Context;
  catalog: Catalog;
}) {
  const recent = [...rows]
    .sort((a, b) => (eventTime(b) ?? -Infinity) - (eventTime(a) ?? -Infinity))
    .slice(0, 6);
  return (
    <div className="xlt-scroll xlt-events">
      <table>
        <thead>
          <tr>
            <th>Time / clock</th>
            <th>Event</th>
            <th>Context</th>
          </tr>
        </thead>
        <tbody>
          {recent.map((r, i) => {
            const time=eventTime(r),resolution=resolveEventStep(r,steps,spans);
            let target:Context={...context,variables:{...context.variables,record_id:[],candidate_id:[],phase_worker:[],cluster:r.cluster?[String(r.cluster)]:context.variables.cluster,run_id:r.run_id?[String(r.run_id)]:context.variables.run_id,trace_id:r.trace_id?[String(r.trace_id)]:['.*']}};
            if(resolution.step)target=selectStep(resolution.step,context);
            return (
              <tr key={i}>
                <td>
                  {time === undefined
                    ? "Unknown"
                    : formatContextTime(time,context.timezone,'time')}
                  <small>
                    {r.boundary_accuracy === "calibrated"
                      ? `calibrated ±${format(r.time_uncertainty_seconds, "s")}`
                      : "node clock"}
                  </small>
                </td>
                <td>
                  <a href={dashboardLink("timeline", target)}>
                    {scalar(r.name)}
                  </a>
                </td>
                <td>
                  Step {scalar(r.step)}
                  <small className="xlt-muted" title={displayUiNote(resolution.reason)}>{resolution.state==='matched'?`Linked · ${scalar(resolution.step?.worker_id)}`:resolution.state==='ambiguous'?`${resolution.candidates.length} candidates`:'Step과의 연결이 확인되지 않았습니다'}</small>
                  {resolution.state==='ambiguous'&&<details><summary>Choose Step</summary>{resolution.candidates.map((row,index)=><p key={index}><a href={appLink('investigate',selectStep(row,context))}>{scalar(row.node)} / {scalar(row.worker_id)} · {scalar(row.record_id)}</a></p>)}</details>}
                  <small>
                    {scalar(r.phase)} · {scalar(r.node)} · {scalar(r.worker_id)}
                  </small>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!recent.length && (
        <p className="xlt-empty">
          이 구간에 EventRecorder Event가 없습니다. Counter 변화는 Event record가 아닙니다.
        </p>
      )}
    </div>
  );
}

function Pressure({
  model,
  selected,
  spans,
  spanData,
}: {
  model: Shell;
  selected?: RecordRow;
  spans: RecordRow[];
  spanData?: import("@grafana/data").PanelData;
}) {
  const errors = recordedSpanErrors(spans);
  const pressureEvidence=stepProjection(records(useData(model.state.evidence)),selected,records(useData(model.state.summary)));
  return (
    <section className="xlt-pressure">
      <div className="xlt-section">
        <h3>System Pressure · priority entities</h3>
        <span>구간 끝에 가까운 query sample입니다. Producer freshness나 원인을 보장하지 않습니다.</span>
      </div>
      <div className="xlt-pressure-grid">
        {PRESSURE_SPECS.map((spec, index) => (
          <PressureCard
            key={spec.name}
            spec={spec}
            provider={model.state.pressure[index]}
            context={readContext(window.location.search)}
            catalog={model.state.catalog}
            evidence={pressureEvidence}
            endTime={sceneGraph.getTimeRange(model).state.value.to.valueOf()}
          />
        ))}
      </div>
      <p className="xlt-muted">
        Recorded span errors:{" "}
        {spanData?.state === LoadingState.Error
          ? "Query error"
          : format(errors.count)}{" "}
        · known status {errors.observed}/{errors.total} · 5,000 record query 상한입니다. Record가 없다는 것은 error가 0이라는 뜻이 아닙니다.
      </p>
    </section>
  );
}

function PressureCard({
  spec,
  provider,
  context,
  catalog,
  evidence,
  endTime,
}: {
  evidence:RecordRow[];
  endTime:number;
  spec: (typeof PRESSURE_SPECS)[number];
  provider?: SceneQueryRunner;
  context: Context;
  catalog: Catalog;
}) {
  const data = useData(provider),
    ranked=pressureOrder(samples(data),{endTime,maxAgeMs:Math.max(15000,3*(data?.request?.intervalMs||0)),kind:spec.name==='GPU utilization'?'utilization':spec.name==='Ray task states'?'task-state':'higher',signal:spec.signal,unit:findPanel(catalog[spec.dashboard],spec.panel)?.fieldConfig?.defaults?.unit||spec.unit,evidence}),
    entities=ranked.map(row=>row.sample);
  return (
    <article className="xlt-card">
      <span className="xlt-eyebrow">{spec.name}</span>
      {!provider ? (
        <p className="xlt-muted">Unavailable</p>
      ) : data?.state === LoadingState.Error ? (
        <p className="xlt-error">Query error</p>
      ) : !entities.length ? (
        <p className="xlt-muted">{samples(data).length?'Stale · 구간 끝에 가까운 sample이 없습니다':'No data'}</p>
      ) : (
        ranked.slice(0, 2).map((row, i) => {const s=row.sample;return (
          <div className="xlt-pressure-value" key={i}>
            <strong>{row.unitKnown ? format(s.value * (spec.scale || 1), spec.unit) : 'Unit unknown'}</strong>
            <small className="xlt-entity" title={JSON.stringify(s.labels)}>{compactEntity(s.labels)}</small>
            <small>{row.supporting?`동일 entity의 Step evidence · baseline 변화율 ${row.delta===undefined?'Unknown':format(row.delta,'%')}. 최신 sample과 구간 통계는 다를 수 있습니다.`:spec.name==='GPU utilization'?'Utilization 관측이며 fault 판정이 아닙니다':spec.name==='Ray task states'?'State 우선순위이며 자동 합산하지 않습니다':'관측된 signal이 가장 높으며 원인 판정은 아닙니다'}</small>
            <Link to={spec.dashboard} context={resourceContext(context,{...s.labels,...(spec.name==='vLLM waiting'&&s.labels.instance?{engine:s.labels.instance}:{})},spec.name==='Sandbox I/O PSI'?'sandbox':'resource')} catalog={catalog}>Inspect entity →</Link>
          </div>
        );})
      )}
      {entities.length > 2 && (
        <small>+{entities.length - 2} 추가 entity · Details에서 확인하세요</small>
      )}
      <p className="xlt-muted">{spec.scope}</p>
      <Link to={spec.dashboard} context={context} catalog={catalog}>
        Details
      </Link>
    </article>
  );
}

function WorkerOutliers({
  model,
  selected,
}: {
  model: Shell;
  selected?: RecordRow;
}) {
  const duration = useData(model.state.workerDurations),
    step = useData(model.state.workerSteps),
    age = useData(model.state.workerAge);
  const rows = workerPeers(
    samples(duration),
    samples(step),
    samples(age),
    Number(
      readContext(window.location.search).variables.training_max_age?.[0] ||
        300,
    ),
  );
  return (
    <section>
      <h3>Outlier worker snapshots</h3>
      <p className="xlt-muted">
        같은 Run / producer / role / phase / node / Reported step cohort입니다. Peer 차이는 조사 후보이며 Workload comparability는 미확인입니다. Snapshot Step은 선택한 Step과 다를 수 있습니다: {" "}
        {scalar(selected?.step)}.
      </p>
      <div className="xlt-scroll">
        <table>
          <thead>
            <tr>
              <th>Worker</th>
              <th>Snapshot step</th>
              <th>Reported rollout</th>
              <th>Peer median / difference</th>
              <th>Quality</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 8).map((r, i) => (
              <tr key={i}>
                <td>
                  {scalar(r.sample.labels.worker_id)}
                  <small className="xlt-muted">
                    {" "}
                    · {scalar(r.sample.labels.producer)}
                  </small>
                </td>
                <td>{format(r.step)}</td>
                <td>{format(r.sample.value, "s")}</td>
                <td
                  className={
                    r.delta !== undefined && r.delta > 25 ? "xlt-attention" : ""
                  }
                >
                  {format(r.median, "s")} /{" "}
                  {r.delta === undefined
                    ? "N/A"
                    : `${r.delta > 0 ? "+" : ""}${format(r.delta, "%")}`}
                </td>
                <td>
                  {r.peers >= 3 ? `${r.peers} peers` : "No comparable cohort"} ·
                  sampled · age {format(r.age, "s")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && (
          <p className="xlt-empty">
            Worker별 rollout observation이 없습니다. Driver의 Phase 합계를 Worker outlier로 해석하지 않습니다.
          </p>
        )}
      </div>
    </section>
  );
}

function HealthSummary({candidates}:{candidates:RecordRow[]}) {
  const definitions=[['GPU','compute'],['vLLM','rollout'],['Storage','storage'],['Network','network'],['Ray','ray'],['Sandbox','sandbox']];
  return <div className="xlt-health-grid">{definitions.map(([name,component])=>{const rows=candidates.filter(c=>c.component===component),strong=rows.some(c=>c.state==='strong_signal');return <article className="xlt-card" key={name}><b>{name}</b><span className={strong?'xlt-attention':'xlt-muted'}>{strong?'Strong signal':rows.some(c=>c.state==='supporting_signal')?'Supporting signal':rows.length?'Weak signal':'Not assessed'}</span><small>{rows.length?'저장된 Step candidate':'Subsystem 판단 기록이 없습니다'}</small></article>;})}</div>;
}

function TopChanges({rows}:{rows:RecordRow[]}) {
  return <section><h3>Top Changes · Step evidence</h3><p className="xlt-muted">저장된 비교 구간이며 Phase별 resource 사용량으로 귀속하지 않습니다. Workload comparability는 source가 제공한 기준을 유지합니다.</p><div className="xlt-scroll"><table><thead><tr><th>Signal</th><th>Current</th><th>Baseline</th><th>Delta</th><th>Scope</th></tr></thead><tbody>{[...rows].sort((a,b)=>Math.abs(Number(b.delta_percent)||0)-Math.abs(Number(a.delta_percent)||0)).slice(0,4).map((r,i)=><tr key={i}><td>{scalar(r.signal)}</td><td>{formatObservation(r,scalar(r.unit,''))}</td><td>{format(r.baseline,scalar(r.unit,''))}</td><td><Delta row={r}/></td><td>{scalar(r.observation_scope)}</td></tr>)}</tbody></table></div></section>;
}

function RunContext({model,selected,steps,context,policySamples,activeWorkloads,onStep}:{model:Shell;selected?:RecordRow;steps:RecordRow[];context:Context;policySamples:import('./semantics').Sample[];activeWorkloads:import('./semantics').Sample[];onStep:(row:RecordRow)=>void}){
  const range=sceneGraph.getTimeRange(model);const rangeState=range.useState();
  const control=(name:string)=>{const variable=sceneGraph.lookupVariable(name,model);return variable?<VariableValueSelectWrapper variable={variable} showAlways layout="vertical"/>:null;};
  return <section className="xlt-run-context xlt-context-top" aria-label="Run Context">
    <div className="xlt-context-fields">
      <div className="xlt-context-variable">{control('cluster')}</div><div className="xlt-context-variable">{control('run_id')}</div>
      {model.state.steps&&<CompletedStepSelect rows={steps} selected={selected} timezone={range.getTimeZone()} label={boundaryPresentation(selected).label}
        optionLabel={row=>`${boundaryPresentation(row).label} ${scalar(row.step)} · ${format(row.step_duration_seconds,'s')}`} onStep={onStep}/>}
      <div className="xlt-context-time"><span>Time range</span><div>{model.state.contextControls.map((c,i)=>{const Control=c.Component as React.ComponentType<{model:any}>;return <Control key={i} model={c}/>;})}</div><div className="xlt-visible-range">{formatContextRange(rangeState.value.from.valueOf(),rangeState.value.to.valueOf(),range.getTimeZone())}</div></div>
    </div>
    <div className="xlt-context-meta"><span>Policy <b>{scalar(selected?.policy_version,policySamples.length===1?format(policySamples[0].value):'Not reported')}</b> · trainer version (reported)</span><span>Wrapped command <b>{activeWorkloads.length===1?scalar(activeWorkloads[0].labels.state):'Not reported'}</b> · latest report</span><details><summary>Observer / Resource</summary><div>{control('source_node')}{control('node')}{control('worker')}{control('role')}</div></details></div>
  </section>;
}

function Coverage({model}:{model:Shell}) {
  const state=model.useState();
  const providers=React.useMemo(()=>[state.steps,state.spans,state.events,state.comparison,state.evidence,
    ...(state.page==='deep-dive'?[state.storageSamples,state.storageStatus]:[]),
    ...state.kpis,...state.matrix,...(state.baselineMatrix||[]),...state.pressure]
    .filter((provider,index,all)=>!provider||all.indexOf(provider)===index),
    [state.steps,state.spans,state.events,state.comparison,state.evidence,state.kpis,state.matrix,state.baselineMatrix,state.pressure,state.page,state.storageSamples,state.storageStatus]);
  const [data,setData]=useState(providers.map(provider=>provider?.state.data));
  useEffect(()=>{
    const refresh=()=>setData(providers.map(provider=>provider?.state.data));
    // Observe providers already used by the screen. Coverage never activates queries.
    const subscriptions=providers.filter((provider):provider is SceneQueryRunner=>!!provider).map(provider=>provider.subscribeToState(refresh));
    refresh();return ()=>subscriptions.forEach(subscription=>subscription.unsubscribe());
  },[providers]);
  const unavailable=providers.filter(provider=>!provider).length;
  const errors=data.filter(value=>value?.state===LoadingState.Error).length;
  const loading=data.filter(value=>value?.state===LoadingState.Loading).length;
  const empty=data.filter(value=>value?.state===LoadingState.Done&&!value.series.some(frame=>frame.length)).length;
  return <details className="xlt-completed-detail"><summary>Diagnosis / Query Coverage · {errors} errors · {empty} empty · {unavailable} optional unavailable</summary>
    <p>{loading} loading · {providers.filter(Boolean).length} Native provider입니다. 데이터가 있다는 것만으로 Freshness나 전체 Telemetry coverage가 확인되지는 않습니다.</p>
    <div className="xlt-scroll"><table><thead><tr><th>Native provider</th><th>Status</th><th>Targets</th><th>Latest query elapsed</th></tr></thead><tbody>{providers.filter(Boolean).map((provider,index)=>{
      const value=data[providers.indexOf(provider)],request=value?.request;
      const elapsed=request?.endTime!==undefined&&request.startTime!==undefined&&request.endTime>=request.startTime?request.endTime-request.startTime:undefined;
      return <tr key={index}><td>{provider!.state.key||`Provider ${index+1}`}</td><td>{value?.state||'Not active'}{value?.state===LoadingState.Error&&<small>{value.errors?.map(error=>error.message).join(' · ')||value.error?.message||'Native datasource error'}</small>}</td><td>{provider!.state.queries.length}</td><td>{elapsed===undefined?'Not reported':format(elapsed,'ms')}</td></tr>;
    })}</tbody></table></div>
    <p className="xlt-muted">최신 Grafana request의 elapsed time이며 Backend CPU 비용이나 누적 request 수가 아닙니다. 동일한 GPU/Ray detail target은 Pressure provider를 공유합니다. 필요할 때 활성화하는 추가 Native panel은 이 목록에 포함되지 않습니다. Matrix query는 최대 600 points, Baseline interval은 최대 1시간으로 제한합니다.</p>
    <p className="xlt-muted">Matrix에서 모호한 Phase와 Worker 연결 상태를 명시합니다. Application age가 Stale / Unknown이면 KPI를 보류하고, 측정된 0은 값으로 유지합니다. Inspect와 Explore는 Grafana 기능을 사용합니다.</p>
  </details>;
}
