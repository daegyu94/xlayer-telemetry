import React, {useState} from 'react';
import {useTheme2, Icon} from '@grafana/ui';
import {Context, appLink} from './context';
import {WORKSPACE_PAGES} from './navigation';

type Entry={route:string;title:string};
const icons:Record<string,any>={overview:'apps',analyze:'table',investigate:'search',timeline:'history','deep-dive':'dashboard',infrastructure:'sitemap',logs:'file-alt','start-here':'rocket','stage-correlation':'layers','bottleneck-summary':'list-ul',compute:'monitor',storage:'database',signals:'chart-line'};

export function DashboardChrome({page,context,nativePages,contextBar,children,onNavigate,onTheme}:{page:string;context:Context;nativePages:readonly Entry[];contextBar:React.ReactNode;children:React.ReactNode;onNavigate:(route:string)=>void;onTheme:()=>void}){
 const [open,setOpen]=useState(false);const theme=useTheme2();
 const title=[...WORKSPACE_PAGES,...nativePages].find(p=>p.route===page)?.title||'Dashboard';
 const nav=(entries:readonly Entry[])=>entries.map(entry=><a key={entry.route} href={appLink(entry.route,context)} aria-current={entry.route===page?'page':undefined} onClick={e=>{e.preventDefault();setOpen(false);onNavigate(entry.route);}}><Icon name={icons[entry.route]||'dashboard'} size="md"/><span>{entry.route==='start-here'?'Start Here':entry.route==='stage-correlation'?'Stage Correlation':entry.title}</span></a>);
 return <div className="xlt xlt-dashboard" data-theme={theme.isDark?'dark':'light'}>
  {open&&<button className="xlt-menu-scrim" aria-label="Close navigation" onClick={()=>setOpen(false)}/>}
  <aside className={`xlt-sidebar ${open?'is-open':''}`} aria-label="Dashboard navigation">
   <div className="xlt-brand"><img src="/public/plugins/xlayer-telemetry-app/img/logo.svg" alt=""/><div><b>XLayer Telemetry</b><small>SYSTEMS OBSERVABILITY</small></div></div>
   <div className="xlt-nav-group">WORKSPACE</div><nav className="xlt-nav" aria-label="XLayer investigation">{nav(WORKSPACE_PAGES)}</nav>
   <div className="xlt-nav-group">NATIVE DASHBOARDS</div><nav className="xlt-nav" aria-label="Canonical dashboards">{nav(nativePages)}</nav>
   <div className="xlt-sidebar-foot">Collect → Correlate → Diagnose<small>Grafana App + native panels</small><small>Resource ownership is not inferred.</small></div>
  </aside>
  <div className="xlt-main">
   <div className="xlt-topbar"><div className="xlt-breadcrumb"><button className="xlt-menu-toggle" aria-label="Open navigation" aria-expanded={open} onClick={()=>setOpen(!open)}><Icon name="bars"/></button><span>XLayer <i>/</i> <b>{title}</b></span></div><button className="xlt-theme-toggle" aria-label={`Switch to ${theme.isDark?'Light':'Dark'} theme`} onClick={onTheme}><Icon name="adjust-circle"/></button></div>
   {contextBar}
   <main className="xlt-content">{children}</main>
  </div>
 </div>;
}
