import { MatrixCellButton } from './MatrixCellButton';
import { MatrixEntityControl } from './MatrixEntityControl';
import { useEffect, useRef } from 'react';
import { LoadingState, getValueFormat } from '@grafana/data';
import { locationService } from '@grafana/runtime';
import { Catalog, findPanel, samples } from './catalog';
import { VARIABLE_NAMES, Context, RecordRow, readContext, appLink, Destination, numeric, scalar, subsystemDestination, investigationKey } from './context';
import { SUBSYSTEMS, phaseWindow, relatedPhaseWindow, gaugeSummary, ClockProof, clockQualifiedCell, contextSample, phaseComparison, stepEvidenceCell, metricCell, selectPhaseSample, Cell, PhaseWindow } from './semantics';
import { executionChoices, selectExecution, observedPhases, measuredWorkers, workerGpuCell, workerContext, resourceContext } from './distributed';
import { displayUiNote } from './ui-copy';
import { matrixEntities, filterMatrixEntity, matrixLookback, PHASE_COLORS, SUBSYSTEM_COLORS } from './matrix-presentation';
import { MATRIX_SPECS } from './matrix-contract';
import { useSceneData as useData } from './scene-data';
import type { Shell, ShellState } from './app-scene';
import { format, Link } from './view-components';

export function Matrix({model,selected,spans,evidence,baselineStep,baselineSpans,comparability,clockStatus,baselineClockStatus,clockProof,baselineClockProof}:{model:Shell;selected:RecordRow;spans:RecordRow[];evidence:RecordRow[];baselineStep?:RecordRow;baselineSpans:RecordRow[];comparability:string;clockStatus:string;baselineClockStatus:string;clockProof:ClockProof;baselineClockProof:ClockProof}){
 const ctx=readContext(window.location.search),workerKey=ctx.variables.phase_worker?.[0];
 const choices=executionChoices(spans,selected),ownSpans=selectExecution(spans,workerKey),ownBaseline=selectExecution(baselineSpans,workerKey),phases=observedPhases(spans,selected,workerKey);
 const phaseName=(name:string)=>({actor_update:'Training · actor',weight_sync:'Weight Sync',checkpoint_save:'Checkpoint',critic_update:'Training · critic',reference_log_prob:'Reference log prob',reference:'Reference',checkpoint_load:'Checkpoint load',rollout:'Rollout',reward:'Reward'}[name]||name);
 return <><div className="xlt-matrix-toolbar"><div className="xlt-chips"><button aria-pressed={model.state.matrixView!=="workers"} onClick={()=>model.setState({matrixView:'phase'})}>Phase Matrix</button><button aria-pressed={model.state.matrixView==="workers"} onClick={()=>model.setState({matrixView:'workers'})}>Worker Comparison</button></div><label>Execution worker<select aria-label="Execution worker" value={workerKey||''} onChange={event=>{const choice=choices.find(row=>row.key===event.target.value);locationService.push(appLink('analyze',choice?workerContext(ctx,choice.row,choice.key):{...ctx,variables:{...ctx.variables,phase_worker:[]}}));model.setState({matrixSelectionVersion:(model.state.matrixSelectionVersion||0)+1});}}><option value="">Execution path · 구간이 모호하면 Worker를 선택하세요</option>{workerKey&&!choices.some(choice=>choice.key===workerKey)&&<option value={workerKey}>선택한 Worker가 현재 구간에 없습니다</option>}{choices.map(choice=><option key={choice.key} value={choice.key}>{scalar(choice.row.node)} / {scalar(choice.row.worker_id)} · {scalar(choice.row.role)} / {scalar(choice.row.producer)}</option>)}</select></label></div>{model.state.matrixView==='workers'?<WorkerComparison model={model} selected={selected} spans={spans} clockStatus={clockStatus} clockProof={clockProof}/>:<div className="xlt-scroll"><table className="xlt-matrix"><thead><tr><th>Subsystem</th>{phases.map(phase=><th key={phase} style={{borderTop:`3px solid ${PHASE_COLORS[phase]}`}}>{phaseName(phase)}<small>{phaseWindow(ownSpans,selected,phase).status==='observed'?`${phaseWindow(ownSpans,selected,phase).accuracy} span`:phaseWindow(ownSpans,selected,phase).status==='ambiguous'?'Ambiguous interval':'No comparable interval'}</small></th>)}</tr></thead><tbody>{SUBSYSTEMS.map(name=><MatrixRow key={name} subsystem={name} phases={phases} model={model} selected={selected} spans={ownSpans} evidence={evidence} baselineStep={baselineStep} baselineSpans={ownBaseline} comparability={comparability} clockStatus={clockStatus} baselineClockStatus={baselineClockStatus} clockProof={clockProof}
                baselineClockProof={baselineClockProof}/>)}</tbody></table></div>}{!phases.length&&<p className="xlt-empty">이 Worker에 비교 가능한 Phase window가 없습니다. Step/span의 clock reference와 uncertainty를 확인하세요. Call duration은 Worker Comparison에서 확인할 수 있습니다.</p>}<div className="xlt-matrix-legend"><span>Sampled = Query로 관측한 값</span><span>Shared / Session = 소유 관계가 아닌 참고 context</span><span>Rolling = Phase 바깥까지 포함한 lookback</span><span>— = 연결이 확인된 observation 없음</span></div><p className="xlt-muted">Delta는 선언된 Workload field, 계측된 Phase와 entity가 일치할 때만 gauge window mean을 비교합니다. Rolling / Session 값에는 Phase delta가 없으며 Actor와 Critic update를 구분합니다.</p></>;
}

function MatrixRow({subsystem:s,phases,model,selected,spans,evidence,baselineStep,baselineSpans,comparability,clockStatus,baselineClockStatus,clockProof,baselineClockProof}:{subsystem:string;phases:string[];model:Shell;selected:RecordRow;spans:RecordRow[];evidence:RecordRow[];baselineStep?:RecordRow;baselineSpans:RecordRow[];comparability:string;clockStatus:string;baselineClockStatus:string;clockProof:ClockProof;baselineClockProof:ClockProof}){
 const index=SUBSYSTEMS.indexOf(s as (typeof SUBSYSTEMS)[number]);
 const data=useData(model.state.matrix[index]),baseData=useData(model.state.baselineMatrix?.[index]),spec=MATRIX_SPECS[s];
 const panel=findPanel(model.state.catalog[spec.dashboard],spec.panel),unit=panel?.fieldConfig?.defaults?.unit||spec.unit;
 const values=samples(data).map(p=>({...p,unit})),baseValues=samples(baseData).map(p=>({...p,unit}));
 const context=readContext(window.location.search),variable=`matrix_${s}_entity` as typeof VARIABLE_NAMES[number],selectedKey=context.variables[variable]?.[0];
 const entities=matrixEntities(values);const activeKey=selectedKey|| (entities.length===1?entities[0].key:undefined);
 const chosen=filterMatrixEntity(values,activeKey),baselineChosen=filterMatrixEntity(baseValues,activeKey),stepCell=stepEvidenceCell(evidence,s);
 const title=({gpu:'GPU',vllm:'vLLM',kv:'KV Cache',ray:'Ray',network:'Network',storage:'Storage',sandbox:'Sandbox'} as Record<string,string>)[s];
 const describe=(labels:Record<string,string>)=>s==='gpu'?`GPU ${labels.gpu||labels.gpu_uuid||'?'} · ${labels.nodename||labels.node||''}`:s==='ray'?`${labels.SessionName||'Session'} · ${labels.State||'State'}`:[labels.operation,labels.status,labels.engine_id||labels.engine,labels.device,labels.port,labels.worker_id].filter(Boolean).join(' · ')||labels.instance||'Observed entity';
 const expr=(data?.series||[]).map(frame=>String(frame.meta?.executedQueryString||''));const lookback=spec.rolling?matrixLookback(expr):0;
 return <tr><th><i className="xlt-subsystem-dot" style={{background:SUBSYSTEM_COLORS[s]}}/>{title}<small>{spec.label}</small><MatrixEntityControl title={title} selectedKey={selectedKey} entities={entities.map(e=>({key:e.key,label:describe(e.labels)}))} onChange={value=>{locationService.push(appLink(model.state.page,{...context,variables:{...context.variables,[variable]:value?[value]:[]}}));model.setState({matrixSelectionVersion:(model.state.matrixSelectionVersion||0)+1});}}/></th>{phases.map(phase=>{
  const parent=phaseWindow(spans,selected,phase),window=relatedPhaseWindow(spans,selected,parent,s);
  const choice=s==='ray'?contextSample(chosen,window):spec.rolling?selectPhaseSample(chosen,window):gaugeSummary(chosen,window);
  let cell:Cell=s==='ray'?{binding:'rolling-context',state:choice.sample?'observed':'missing',scope:'shared-service',type:'session sampled context',sample:choice.sample,value:choice.sample?.value,unit,explanation:'Session / State count at this time. Node identity was aggregated; this is not phase usage or a phase baseline.'}:metricCell(choice.sample?{...choice.sample,unit}:undefined,window,spec.scope,lookback);
  cell={...cell,observations:numeric('count' in choice?choice.count:undefined),evidence:stepCell.evidence,explanation:cell.explanation+(stepCell.evidence?.length?' Saved candidate evidence below describes the full Step, not phase attribution.':'')};
  if(s==='sandbox'&&window.span!==parent.span)cell={...cell,explanation:cell.explanation+' Related instrumented sandbox.exec call is linked through observed parent IDs.'};
  if(choice.entities>1)cell={...cell,value:undefined,state:'ambiguous',explanation:`${choice.entities} entities match. Choose an explicit entity; none are averaged together.`};
  cell=clockQualifiedCell(cell,clockStatus,clockProof,window);
  const baselineParent=baselineStep?phaseWindow(baselineSpans,baselineStep,phase):{status:'missing'} as PhaseWindow;
  const baselineWindow=baselineStep?relatedPhaseWindow(baselineSpans,baselineStep,baselineParent,s):baselineParent;
  const baselineChoice=!spec.rolling&&s!=='ray'?gaugeSummary(baselineChosen,baselineWindow):{entities:0,count:0};
  const baselineCell=clockQualifiedCell({...metricCell('sample' in baselineChoice&&baselineChoice.sample?{...baselineChoice.sample,unit}:undefined,baselineWindow,spec.scope,lookback),observations:baselineChoice.count},baselineClockStatus,baselineClockProof,baselineWindow);
  const comparison=phaseComparison(cell,baselineCell,window,baselineWindow,comparability);
  const observed=cell.value!==undefined;
  const label=data?.state===LoadingState.Error?'Query error':cell.sample&&cell.value===undefined&&cell.explanation.startsWith('Clock quality is')?'Clock unverified':observed?compactMatrixValue(cell.value!,unit):choice.entities>1?'Choose entity':window.status!=='observed'?'—':!values.length?'No data':s==='sandbox'?'—':'No linked sample';
  const quality=s==='ray'?'Session context':cell.binding==='rolling-context'?`Rolling${typeof lookback==='number'?` ${lookback/1000}s`:''} · ${cell.scope==='shared-service'?'Shared':'Node'}`:cell.scope==='shared-service'?'Sampled · Shared':cell.scope==='worker/cgroup'?'Sampled · Worker':'Sampled · Node';
  return <td key={phase}><MatrixCellButton identity={`${phase} × ${s} evidence`} label={label}
    quality={observed?quality:window.status==='ambiguous'?'Ambiguous span':s==='sandbox'?'No linked worker call':cell.scope}
    comparison={comparison} explanation={displayUiNote(cell.explanation)} comparisonReason={displayUiNote(comparison.reason)}
    observations={!spec.rolling&&s!=='ray'&&observed?cell.observations:undefined}
    linkedTool={s==='sandbox'&&window.span!==parent.span} stepEvidence={phase==='rollout'&&!!stepCell.evidence?.length}
    loading={data?.state===LoadingState.Loading} onClick={()=>{model.setState({selectedCell:{contextKey:investigationKey(context),phase,subsystem:s,cell,window}});setTimeout(()=>document.querySelector('.xlt-evidence')?.scrollIntoView({behavior:'smooth',block:'start'}),0);}}/></td>;
 })}</tr>;
}

export function EvidenceDetail({
  selected,
  context,
  catalog,
  close,
}: {
  selected: NonNullable<ShellState["selectedCell"]>;
  context: Context;
  catalog: Catalog;
  close: () => void;
}) {
  const { phase, subsystem, cell, window } = selected;
  const detailRef = useRef<HTMLElement>(null);
  const closeRef=useRef(close);closeRef.current=close;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    detailRef.current?.focus({ preventScroll: true });
    detailRef.current?.scrollIntoView({ block: "start" });
    const onEscape=(event:KeyboardEvent)=>{if(event.key==='Escape')closeRef.current();};
    document.addEventListener('keydown',onEscape);
    return () => {
      document.removeEventListener('keydown',onEscape);
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, []);
  const labels = cell.sample?.labels;
  const measuredContext = labels ? resourceContext(context, {
    ...labels,
    ...(['vllm','kv','storage'].includes(subsystem) && labels.instance ? {engine:labels.instance} : {}),
  }, subsystem==='sandbox'?'sandbox':'resource') : context;
  const ctx = {
    ...measuredContext,
    variables: {
      ...measuredContext.variables,
      phase: [phase === "Selected Step" ? ".*" : phase],
    },
  };
  return (
    <aside
      ref={detailRef}
      tabIndex={-1}
      className="xlt-evidence"
      aria-label="Evidence detail"
      onKeyDown={(e) => {
        if (e.key === "Escape") close();
      }}
    >
      <div className="xlt-section">
        <h3>
          {phase} × {subsystem}
        </h3>
        <button onClick={close}>Close</button>
      </div>
      <p>
        <b>{cell.scope}</b> · {cell.type} · {cell.binding}
      </p>
      <p>{displayUiNote(cell.explanation)}</p>
      <p className="xlt-muted">
        Phase boundary: {window.status}
        {window.accuracy
          ? ` · ${window.accuracy} · ${window.reference} · uncertainty ${format(window.uncertainty, "s")}`
          : ""}
        . MFU, Phase별 p99와 Phase baseline은 추정하지 않습니다.
      </p>
      {["supporting", "counter", "missing"].map((type) => (
        <div key={type}>
          <h4>
            {type === "counter"
              ? "Against"
              : type[0].toUpperCase() + type.slice(1)}
          </h4>
          {cell.evidence
            ?.filter((e) => e.evidence_type === type)
            .map((r, i) => (
              <p key={i}>
                <b>{scalar(r.signal)}</b> ·{" "}
                {format(r.baseline, scalar(r.unit, ""))} →{" "}
                {format(r.current, scalar(r.unit, ""))}
                <br />
                <small>
                  {scalar(r.observation_scope)} ·{" "}
                  {scalar(r.entity, "No entity mapping")} ·{" "}
                  {scalar(r.window_statistic, "Unknown statistic")} ·{" "}
                  {scalar(r.quality_warnings, "Quality not reported")}
                </small>
              </p>
            ))}
          {!cell.evidence?.some((e) => e.evidence_type === type) && (
            <p className="xlt-muted">저장된 기록에 보고되지 않았습니다.</p>
          )}
        </div>
      ))}
      <p className="xlt-notice">
        Shared / Node-wide evidence는 시간적 correlation입니다. Run별 resource 소유 관계나 causal path는 확인되지 않았습니다.
      </p>
      <div className="xlt-actions">
        {(
          ["timeline", subsystemDestination(subsystem), "logs"] as Destination[]
        ).map((to) => (
          <Link key={to} to={to} context={ctx} catalog={catalog}>
            {to}
          </Link>
        ))}
      </div>
    </aside>
  );
}

function compactMatrixValue(value:number,unit:string):string{const formatted=getValueFormat(unit)(value,unit==='short'||unit==='percent'||unit==='percentunit'?0:1);return `${formatted.prefix||''}${formatted.text}${formatted.suffix||''}`;}

function WorkerComparison({model,selected,spans,clockStatus,clockProof}:{model:Shell;selected:RecordRow;spans:RecordRow[];clockStatus:string;clockProof:ClockProof}){
 const data=useData(model.state.matrix[0]),values=samples(data);const rows=measuredWorkers(spans,selected);
 return <section><h3>Measured Worker Comparison</h3><p className="xlt-muted">행마다 선언된 execution identity를 하나 유지합니다. Peer duration은 operation, scope와 Workload fingerprint가 일치해야 비교합니다. GPU는 연결된 Sampled device이며 Worker 사용량이 아닙니다.</p><div className="xlt-scroll"><table><thead><tr><th>Worker / node</th><th>Phase coverage</th><th>Rollout call</th><th>Peer median / delta</th><th>GPU</th><th>Next</th></tr></thead><tbody>{rows.map(row=>{
  const device=workerGpuCell(row,values,clockStatus,clockProof);
  return <tr key={row.key}><td>{scalar(row.row.worker_id)} · {scalar(row.row.node)}<small>{scalar(row.row.producer)} / {scalar(row.row.role)}</small></td><td>{row.phases} observed phase types · {row.count} spans</td><td>{row.duration!==undefined?`${format(row.duration,'s')}${row.window.status==='observed'?'':' · call only / clock unmapped'}`:row.window.status}</td><td>{row.peers>=3?`${format(row.median,'s')} · ${row.delta===undefined?'Δ unavailable':format(row.delta,'%')}`:'No matched peer cohort'}</td><td title={device.explanation}>{device.value!==undefined?`${format(device.value,'percent')} · sampled`:'GPU identity, Sample 또는 Clock 품질을 확인할 수 없습니다'}</td><td><button onClick={()=>{const ctx=readContext(window.location.search);locationService.push(appLink('analyze',workerContext(ctx,row.row,row.key)));model.setState({matrixSelectionVersion:(model.state.matrixSelectionVersion||0)+1,matrixView:'phase'});}}>Inspect worker →</button></td></tr>;
 })}</tbody></table></div></section>;
}
