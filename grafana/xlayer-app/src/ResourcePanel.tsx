import React,{useEffect,useMemo,useState} from 'react';
import {getFieldDisplayName,getValueFormat} from '@grafana/data';
import type {PanelData} from '@grafana/data';
import type {VizPanel,SceneDataProvider} from '@grafana/scenes';
import {resourceKind,resourceMatrix} from './resource-matrix';
import type {Reduction,ResourceKind,ResourceCell} from './resource-matrix';

const plans=new WeakMap<VizPanel,{kind:ResourceKind;metrics:Record<string,string>}>();
export function registerResourcePanel(panel:VizPanel,source:{type:string;targets?:any[];options?:any}){
 const kind=source.type==='timeseries'&&source.options?.legend?.enableFacetedFilter?resourceKind(source.targets||[]):undefined;
 if(kind)plans.set(panel,{kind,metrics:Object.fromEntries((source.targets||[]).map(t=>[t.refId,(t.legendFormat||'').replace(/\{\{[^}]+\}\}/g,'').trim()||t.refId]))});
 return panel;
}
function useData(provider:SceneDataProvider|undefined){
 const [data,setData]=useState<PanelData|undefined>(provider?.state.data);
 useEffect(()=>{setData(provider?.state.data);if(!provider)return;
  const stop=provider.activate(),sub=provider.subscribeToState(s=>setData(s.data));
  return ()=>{sub.unsubscribe();stop();};
 },[provider]);return data;
}
function valueText(cell:ResourceCell,unit:string){
 if(cell.value===undefined)return '—';
 const v=getValueFormat(unit||'short')(cell.value,2);
 return `${v.prefix||''}${v.text}${v.suffix||''}`;
}
/** The original VizPanel still owns query/time/variables; only its rendering changes. */
export function ResourcePanel({panel}:{panel:VizPanel}){
 const plan=plans.get(panel);
 return plan?<MatrixPanel panel={panel} plan={plan}/>:<panel.Component model={panel}/>;
}
function MatrixPanel({panel,plan}:{panel:VizPanel;plan:NonNullable<ReturnType<typeof plans.get>>}){
 // The Matrix replaces VizPanel's Scene wrapper, so retain its activation lifecycle.
 useEffect(()=>panel.activate(),[panel]);
 const state=panel.useState(),data=useData(state.$data);
 const [mode,setMode]=useState<'matrix'|'series'>('matrix'),[reduction,setReduction]=useState<Reduction>('latest');
 const [metric,setMetric]=useState(''),[selected,setSelected]=useState<string|undefined>();
 const [original]=useState(state.fieldConfig);
 const matrix=useMemo(()=>resourceMatrix(data,plan.kind,reduction),[data,plan.kind,reduction]),activeMetric=matrix.metrics.includes(metric)?metric:matrix.metrics[0];
 const cells=matrix.cells.filter(c=>c.metric===activeMetric),chosen=cells.find(c=>c.key===selected);
 const names=chosen&&data?[getFieldDisplayName(chosen.frame.fields[chosen.fieldIndex],chosen.frame,data.series)]:[];
 const duplicate=chosen&&data&&matrix.cells.some(c=>c.key!==chosen.key&&getFieldDisplayName(c.frame.fields[c.fieldIndex],c.frame,data.series)===names[0]);
 useEffect(()=>{
  // VizPanel caches fields with applied overrides; selection must invalidate that cache.
  panel.clearFieldConfigCache();
  panel.setState({fieldConfig:mode==='matrix'&&chosen&&!duplicate?{...original,overrides:[...original.overrides,{matcher:{id:'byNames',options:{mode:'exclude',names}},properties:[{id:'custom.hideFrom',value:{viz:true,legend:true,tooltip:true}}]}]}:original});
  return ()=>{panel.clearFieldConfigCache();panel.setState({fieldConfig:original});};
 },[panel,mode,chosen?.key,duplicate,names.join('\0'),original]);
 const unit=original.defaults.unit||'',max=Math.max(0,...cells.filter(c=>c.status==='observed').map(c=>c.value||0));
 const messages={error:'Query Failure · 조회에 실패했습니다',loading:'Loading · 데이터를 조회하고 있습니다','no-data':'No data · 수집된 데이터가 없습니다'};
 return <section className="xlt-resource-panel" data-resource-title={state.title}>
  <header><h3>{state.title}</h3><div className="xlt-resource-controls"><button aria-pressed={mode==='matrix'} onClick={()=>setMode('matrix')}>Matrix</button><button aria-pressed={mode==='series'} onClick={()=>setMode('series')}>Time series</button></div></header>
  {mode==='series'?<div className="xlt-resource-detail"><panel.Component model={panel}/></div>:<>
   <div className="xlt-resource-controls"><label>Calculation <select aria-label="Matrix calculation" value={reduction} onChange={e=>setReduction(e.target.value as Reduction)}><option value="latest">Latest evaluation</option><option value="mean">Range mean</option><option value="max">Range max</option></select></label>
   {matrix.metrics.length>1&&<label>Metric <select aria-label="Matrix metric" value={activeMetric} onChange={e=>{setMetric(e.target.value);setSelected(undefined);}}>{matrix.metrics.map(key=><option key={key} value={key}>{plan.metrics[JSON.parse(key)[0]]||JSON.parse(key).join(' · ')}</option>)}</select></label>}</div>
   {state.description&&<details className="xlt-resource-note"><summary>Source / Scope</summary><p>{state.description}</p></details>}
   <p className="xlt-muted xlt-resource-note">Query evaluation의 {reduction==='latest'?'최신 값':reduction==='mean'?'구간 평균':'구간 최댓값'}입니다. 색상은 관측값의 상대 크기이며 Health나 Run ownership을 뜻하지 않습니다. Producer freshness는 별도 확인이 필요합니다.</p>
   {matrix.state!=='ready'?<p className="xlt-empty" role="status">{messages[matrix.state as keyof typeof messages]}</p>:<div className="xlt-resource-scroll"><table><thead><tr><th>Cluster / Node</th>{matrix.columns.map(c=><th key={c}>{plan.kind==='gpu'?'GPU ':''}{c}</th>)}</tr></thead><tbody>{matrix.rows.map(row=><tr key={row}><th scope="row">{row}</th>{matrix.columns.map(column=>{
    const matching=cells.filter(c=>c.row===row&&c.column===column),cell=matching.length===1?matching[0]:undefined;
    const text=cell?valueText(cell,unit):'—',status=cell?.status|| (matching.length>1?'unknown':'no-data');
    return <td key={column}><button className="xlt-resource-cell" data-state={status} aria-pressed={selected===cell?.key&&!!cell} disabled={!cell||status!=='observed'} aria-label={`${row} · ${column} · ${text} · ${status}`} title={cell?`${JSON.stringify(cell.labels)}\n${cell.count} finite samples · Latest evaluation: ${Number.isFinite(cell.time)?new Date(cell.time!).toISOString():'Unknown'}`:matching.length>1?'Multiple source identities; values are not merged':'No data for this coordinate'} style={status==='observed'?{background:`rgba(69,102,213,${.06+.30*(max>0?Math.max(0,cell!.value||0)/max:0)})`}:undefined} onClick={()=>setSelected(cell?.key)}><b>{text}</b>{status!=='observed'&&<small>{status==='no-data'?'No data':status==='stale'?'Stale':'Unknown'}</small>}</button></td>;
   })}</tr>)}</tbody></table></div>}
   {chosen&&<div className="xlt-resource-selection"><div className="xlt-resource-controls"><b>{chosen.row} · {chosen.column}</b><button onClick={()=>setSelected(undefined)}>Clear selection</button></div>{duplicate?<p className="xlt-empty">동일한 series 표시 이름이 중복돼 선택한 identity만 분리할 수 없습니다. Time series와 기존 필터에서 확인하세요.</p>:<div className="xlt-resource-detail"><panel.Component model={panel}/></div>}</div>}
  </>}
 </section>;
}
