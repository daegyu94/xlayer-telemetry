import React,{useEffect,useMemo,useState} from 'react';
import {getFieldDisplayName,getValueFormat} from '@grafana/data';
import type {VizPanel} from '@grafana/scenes';
import {initialResourceMode,matrixColorAlpha,metricLabel,resourceMatrix,resourceMetricAvailability,resourcePanelKey} from './resource-matrix';
import type {Reduction,ResourceMatrixProfile,ResourceCell} from './resource-matrix';
import {validDateMs} from './time-context';
import {useSceneData} from './scene-data';
import {timelinePanelHeight} from './native-panel-layout';

const plans=new WeakMap<VizPanel,{profile:ResourceMatrixProfile;metrics:Record<string,string>}>();
export function registerResourcePanel(panel:VizPanel,source:{type:string;targets?:any[]},profile?:ResourceMatrixProfile){
 if(source.type==='timeseries'&&profile)plans.set(panel,{profile,metrics:Object.fromEntries((source.targets||[]).map(t=>[t.refId,metricLabel(t.legendFormat,t.refId)]))});
 return panel;
}
function valueText(cell:ResourceCell,unit:string){
 if(cell.value===undefined)return '—';
 const v=getValueFormat(unit||'short')(cell.value,2);
 return `${v.prefix||''}${v.text}${v.suffix||''}`;
}
function additionalIdentityLabels(cells:ResourceCell[]){
 const fixed=new Set(['cluster','node','nodename','gpu','device','port']);
 const keys=[...new Set(cells.flatMap(cell=>Object.keys(cell.labels)))].filter(key=>!fixed.has(key));
 return keys.filter(key=>new Set(cells.map(cell=>cell.labels[key]||'')).size>1);
}
/** The original VizPanel still owns query/time/variables; only its rendering changes. */
export function ResourcePanel({panel}:{panel:VizPanel}){
 const plan=plans.get(panel);
 const key=resourcePanelKey(panel);
 return plan?<MatrixPanel key={key} panel={panel} plan={plan}/>:['state-timeline','status-history'].includes(panel.state.pluginId)?<NativeTimelinePanel key={key} panel={panel}/>:<panel.Component key={key} model={panel}/>;
}
function NativeTimelinePanel({panel}:{panel:VizPanel}){
 useEffect(()=>panel.activate(),[panel]);
 const state=panel.useState(),data=useSceneData(state.$data);
 return <div className="xlt-native-timeline" data-query-state={data?.state||'Loading'} style={{height:timelinePanelHeight(data)+24,paddingBottom:24,boxSizing:'border-box'}}><panel.Component model={panel}/></div>;
}
function MatrixPanel({panel,plan}:{panel:VizPanel;plan:NonNullable<ReturnType<typeof plans.get>>}){
 // The Matrix replaces VizPanel's Scene wrapper, so retain its activation lifecycle.
 useEffect(()=>panel.activate(),[panel]);
 const state=panel.useState(),data=useSceneData(state.$data);
 const [selectedMode,setMode]=useState<'matrix'|'series'>(),[reduction,setReduction]=useState<Reduction>('latest');
 const mode=selectedMode||'matrix';
 const [metric,setMetric]=useState(''),[selected,setSelected]=useState<string|undefined>();
 const [original]=useState(state.fieldConfig);
 const unit=state.fieldConfig.defaults.unit||'';
 const matrix=useMemo(()=>resourceMatrix(data,plan.profile,reduction,unit),[data,plan.profile,reduction,unit]);
 useEffect(()=>{if(selectedMode===undefined){const initial=initialResourceMode(matrix);if(initial)setMode(initial);}},[matrix,selectedMode]);
 const metricRefs=Object.keys(plan.metrics),activeMetric=metric||metricRefs[0]||'';
 const activeMetricKey=activeMetric?JSON.stringify([activeMetric]):'';
 const cells=matrix.cells.filter(c=>c.metric===activeMetricKey),chosen=cells.find(c=>c.key===selected);
 const metricAvailability=resourceMetricAvailability(matrix,activeMetric);
 const unsupported=matrix.state==='unsupported'||(metricAvailability==='present'&&cells.length>0&&!cells.some(c=>c.dimensionValid));
 useEffect(()=>{if(unsupported&&mode==='matrix')setMode('series');},[unsupported,mode]);
 const names=chosen&&data?[getFieldDisplayName(chosen.frame.fields[chosen.fieldIndex],chosen.frame,data.series)]:[];
 const duplicate=chosen&&data&&matrix.cells.some(c=>c.key!==chosen.key&&getFieldDisplayName(c.frame.fields[c.fieldIndex],c.frame,data.series)===names[0]);
 useEffect(()=>{
  // VizPanel caches fields with applied overrides; selection must invalidate that cache.
  panel.clearFieldConfigCache();
  panel.setState({fieldConfig:mode==='matrix'&&chosen&&!duplicate?{...original,overrides:[...original.overrides,{matcher:{id:'byNames',options:{mode:'exclude',names}},properties:[{id:'custom.hideFrom',value:{viz:true,legend:true,tooltip:true}}]}]}:original});
  return ()=>{panel.clearFieldConfigCache();panel.setState({fieldConfig:original});};
 },[panel,mode,chosen?.key,duplicate,names.join('\0'),original]);
 const max=Math.max(0,...cells.filter(c=>c.status==='observed').map(c=>c.value||0));
 const messages={error:'Query Failure · 조회에 실패했습니다',loading:'Loading · 데이터를 조회하고 있습니다','no-data':'No data · 수집된 데이터가 없습니다',unsupported:`Matrix의 ${plan.profile.dimension} identity가 보고되지 않아 원본 Time series를 표시합니다.`};
 return <section className="xlt-resource-panel" data-resource-title={state.title} data-query-state={data?.state||'Loading'}>
  <header><h3>{state.title}</h3><div className="xlt-resource-controls"><button aria-pressed={mode==='matrix'} disabled={unsupported} onClick={()=>setMode('matrix')}>Matrix</button><button aria-pressed={mode==='series'} onClick={()=>setMode('series')}>Time series</button></div></header>
  {mode==='series'?<>{unsupported&&<p className="xlt-empty" role="status">{messages.unsupported}</p>}<div className="xlt-resource-detail"><panel.Component model={panel}/></div></>:<>
   <div className="xlt-resource-controls"><label>Calculation <select aria-label="Matrix calculation" value={reduction} onChange={e=>setReduction(e.target.value as Reduction)}><option value="latest">Latest evaluation</option><option value="mean">Range mean</option><option value="max">Range max</option></select></label>
   <label>Metric <select aria-label="Matrix metric" value={activeMetric} disabled={metricRefs.length<2} onChange={e=>{setMetric(e.target.value);setSelected(undefined);}}>{metricRefs.map(ref=><option key={ref} value={ref}>{plan.metrics[ref]||ref}</option>)}</select></label></div>
   {state.description&&<details className="xlt-resource-note"><summary>Source / Scope</summary><p>{state.description}</p></details>}
   <p className="xlt-muted xlt-resource-note">Query evaluation의 {reduction==='latest'?'최신 값':reduction==='mean'?'반환된 samples의 산술 평균':'구간 최댓값'}입니다. Mean은 불규칙한 sample 간격을 고려한 시간 가중 평균이 아닙니다. {plan.profile.color.mode==='relative'?'색상은 같은 Metric의 선택 Entity 중 상대 크기입니다.':'색상은 전체 선택 Entity에 고정된 0~100% 범위를 사용합니다.'} 색은 Health나 Run ownership을 뜻하지 않습니다. Producer freshness는 별도 확인이 필요합니다.</p>
   {matrix.state!=='ready'&&matrix.state!=='unsupported'?<p className="xlt-empty" role="status">{messages[matrix.state as keyof typeof messages]}</p>:metricAvailability==='missing'?<p className="xlt-empty" role="status">선택한 Metric({plan.metrics[activeMetric]||activeMetric})의 source series가 반환되지 않았습니다. 0이나 다른 Metric 값으로 대체하지 않습니다.</p>:metricAvailability==='empty'?<p className="xlt-empty" role="status">선택한 Metric의 query 결과가 없습니다. 측정값 0과 구분합니다.</p>:unsupported?<p className="xlt-empty" role="status">{messages.unsupported}</p>:matrix.state!=='ready'?<p className="xlt-empty" role="status">{messages[matrix.state as keyof typeof messages]}</p>:<div className="xlt-resource-scroll"><table><thead><tr><th>Cluster / Node</th>{matrix.columns.map(c=><th key={c}>{plan.profile.dimension==='gpu'?'GPU ':''}{c}</th>)}</tr></thead><tbody>{matrix.rows.map(row=><tr key={row}><th scope="row">{row}</th>{matrix.columns.map(column=>{
    const matching=cells.filter(c=>c.row===row&&c.column===column),cell=matching.length===1?matching[0]:undefined;
    const text=cell?valueText(cell,unit):'—',status=cell?.status|| (matching.length>1?'unknown':'no-data');
    const unitValue=plan.profile.color.mode==='fixed-percent'?plan.profile.color.unit:cell?.frame.fields[cell.fieldIndex].config.unit||unit;
    const alpha=cell?matrixColorAlpha(cell.value,plan.profile.color,max,unitValue):undefined;
    const differing=additionalIdentityLabels(matching);
    const unavailableReason=matching.length>1?`복수의 source identity${differing.length?` (${differing.join(', ')})`:''}가 같은 좌표에 있습니다. 값을 합치지 않습니다.`:'이 좌표의 telemetry가 없습니다.';
    return <td key={column}><button className="xlt-resource-cell" data-state={status} aria-pressed={selected===cell?.key&&!!cell} disabled={!cell||status!=='observed'} aria-label={`${row} · ${column} · ${text} · ${status}`} title={cell?`${JSON.stringify(cell.labels)}\n${cell.count} finite samples · Latest evaluation: ${validDateMs(cell.time)?new Date(cell.time).toISOString():'Unknown'}`:unavailableReason} style={status==='observed'&&alpha!==undefined?{background:`rgba(69,102,213,${alpha})`}:undefined} onClick={()=>setSelected(cell?.key)}><b>{text}</b>{status!=='observed'&&<small>{status==='no-data'?'No data':status==='stale'?'Stale':'Unknown'}</small>}</button></td>;
   })}</tr>)}</tbody></table></div>}
   {chosen&&<div className="xlt-resource-selection"><div className="xlt-resource-controls"><b>{chosen.row} · {chosen.column}</b><button onClick={()=>setSelected(undefined)}>Clear selection</button></div>{duplicate?<p className="xlt-empty">동일한 series 표시 이름이 중복돼 선택한 identity만 분리할 수 없습니다. Time series와 기존 필터에서 확인하세요.</p>:<div className="xlt-resource-detail"><panel.Component model={panel}/></div>}</div>}
  </>}
 </section>;
}
