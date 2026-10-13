import React from 'react';
import {appLink} from './context';
import type {Context} from './context';
import type {Catalog} from './catalog';
import {NATIVE_PAGES} from './native-workspace';

export function DashboardDirectory({context,catalog,onNavigate}:{context:Context;catalog:Catalog;onNavigate:(route:string)=>void}){
 return <section className="xlt-dashboard-directory" aria-label="Dashboard library">
  <header><h2>Dashboards</h2><p className="xlt-muted">조사 목적에 맞는 상세 지표를 선택하세요.</p></header>
  <div className="xlt-dashboard-directory-grid">{NATIVE_PAGES.map(page=><a key={page.route} className="xlt-card" href={appLink(page.route,context)} onClick={event=>{event.preventDefault();onNavigate(page.route);}}>
   <h3>{page.title}</h3><p className="xlt-muted">{page.description}</p>
   {!catalog[page.destination]&&<small className="xlt-muted">Dashboard unavailable</small>}
   <span>Open dashboard →</span>
  </a>)}</div>
 </section>;
}
