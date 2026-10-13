import React from 'react';
import type {VizPanel} from '@grafana/scenes';
import type {Context,Destination} from './context';
import {appLink} from './context';

export const RUN_SOURCES:ReadonlyArray<{name:string;route:string;tab?:string;panels:ReadonlyArray<{source:Destination;panel:number;scope:string;title?:string}>}>=[
 {name:'VERL',route:'overview',panels:[
  {source:'overview',panel:7,scope:'Training step time · node mean of fresh workers'},
  {source:'stage',panel:2,scope:'Reward mean · Run / worker'},
 ]},
 {name:'vLLM',route:'stage-correlation',panels:[
  {source:'stage',panel:21,title:'Request latency p95',scope:'Time to first token p95 · engine'},
  {source:'stage',panel:28,scope:'Prefix cache token hit ratio · engine'},
 ]},
 {name:'Ray',route:'stage-correlation',panels:[
  {source:'stage',panel:22,title:'Task states',scope:'Pending and running · shared session'},
  {source:'stage',panel:24,title:'Logical CPU resources',scope:'Available and used · scheduler'},
 ]},
 {name:'Mooncake',route:'deep-dive',tab:'Connector RPC',panels:[
  {source:'stage',panel:60,title:'Store request latency p95',scope:'KV Store · operation'},
  {source:'stage',panel:63,title:'Cache lookup rates',scope:'Memory/file hits and lookups'},
 ]},
 {name:'3FS',route:'deep-dive',tab:'3FS evidence',panels:[
  {source:'storage',panel:101,scope:'Collection reports · original time and units'},
 ]},
];

export const RUN_SOURCE_PANELS=RUN_SOURCES.flatMap(source=>source.panels);

export function RunSources({panels,context,View}:{panels:(VizPanel|undefined)[];context:Context;View:React.ComponentType<{panel:VizPanel;title?:string}>}){
 return <section className="xlt-run-sources" aria-label="Connected subsystems">
  <div className="xlt-section"><div><h3>Connected Subsystems</h3><p className="xlt-muted">현재 Cluster·Run·시간 범위의 관측입니다. 조사할 Run은 아래 목록에서 선택하세요.</p></div></div>
  <div className="xlt-run-source-grid">{RUN_SOURCES.map((source,index)=>{
   const offset=RUN_SOURCES.slice(0,index).reduce((count,item)=>count+item.panels.length,0);
   return <article key={source.name} className="xlt-run-source" aria-label={`${source.name} signals`}>
    <header><h4>{source.name}</h4><a href={appLink(source.route,source.tab?{...context,variables:{...context.variables,detail_tab:[source.tab]}}:context)} aria-label={`Open ${source.name} details`}>Details →</a></header>
    {source.panels.length?<div className={`xlt-run-source-panels${source.name==='3FS'?' xlt-run-source-wide':''}`}>{source.panels.map((metric,metricIndex)=><div key={metric.panel} className="xlt-run-source-metric">{source.name==='3FS'&&context.variables.demo_generation?.length&&<small>Demo · Synthetic collection fixture, not a ClickHouse connection</small>}<small>{metric.scope}</small>{panels[offset+metricIndex]?<View panel={panels[offset+metricIndex]!} title={metric.title}/>:<p className="xlt-empty">Source panel을 사용할 수 없습니다.</p>}</div>)}</div>:<p className="xlt-empty">ClickHouse 연결과 latency·queue metric 이름을 설정하면 표시됩니다.</p>}
   </article>})}</div>
 </section>;
}
