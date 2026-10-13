import React from 'react';
import type {VizPanel} from '@grafana/scenes';
import type {Context,Destination} from './context';
import {appLink} from './context';

export const RUN_SOURCES:ReadonlyArray<{name:string;source:Destination;panel:number;scope:string;route:string;tab?:string}>=[
 {name:'VERL',source:'overview',panel:7,scope:'Step time · Run / worker',route:'overview'},
 {name:'GPU',source:'compute',panel:2,scope:'Utilization · shared device',route:'compute'},
 {name:'vLLM',source:'stage',panel:9,scope:'Requests · shared engine',route:'stage-correlation'},
 {name:'Ray',source:'stage',panel:22,scope:'Task states · shared session',route:'stage-correlation'},
 {name:'Mooncake',source:'stage',panel:60,scope:'RPC p95 · shared service',route:'deep-dive',tab:'Connector RPC'},
 {name:'3FS',source:'storage',panel:103,scope:'Saved collection comparison',route:'deep-dive',tab:'3FS evidence'},
];

export function RunSources({panels,context,View}:{panels:(VizPanel|undefined)[];context:Context;View:React.ComponentType<{panel:VizPanel}>}){
 return <section className="xlt-run-sources" aria-label="Connected subsystems">
  <div className="xlt-section"><div><h3>Connected Subsystems</h3><p className="xlt-muted">현재 Cluster·Run·시간 범위의 관측입니다. 조사할 Run은 아래 목록에서 선택하세요.</p></div></div>
  <div className="xlt-run-source-grid">{RUN_SOURCES.map((source,index)=><article key={source.name} className="xlt-run-source" aria-label={`${source.name} signals`}>
   <header><div><h4>{source.name}</h4><small>{source.scope}</small></div><a href={appLink(source.route,source.tab?{...context,variables:{...context.variables,detail_tab:[source.tab]}}:context)} aria-label={`Open ${source.name} details`}>Details →</a></header>
   {panels[index]?<View panel={panels[index]!}/>:<p className="xlt-empty">Source panel을 사용할 수 없습니다.</p>}
  </article>)}</div>
 </section>;
}
