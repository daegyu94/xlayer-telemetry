import {getProductStyles} from './design-system/styles';
import React, {useState,useEffect,useRef} from 'react';
import {useTheme2, useStyles2, Icon} from '@grafana/ui';
import {Context, appLink} from './context';
import {WORKSPACE_PAGES,PRIMARY_PAGES,INVESTIGATION_PAGES,primaryRoute} from './navigation';

type Entry={route:string;title:string};
const icons:Record<string,any>={overview:'apps',analyze:'table',investigate:'search',timeline:'history','deep-dive':'dashboard',infrastructure:'sitemap',logs:'file-alt',runs:'history','start-here':'rocket','stage-correlation':'layers','bottleneck-summary':'list-ul',compute:'monitor',storage:'database',signals:'chart-line'};

export function DashboardChrome({page,context,nativePages,contextBar,children,onNavigate,onTheme,synthetic=false}:{synthetic?:boolean;page:string;context:Context;nativePages:readonly Entry[];contextBar:React.ReactNode;children:React.ReactNode;onNavigate:(route:string)=>void;onTheme:()=>void}){
 const [open,setOpen]=useState(false);const menuRef=useRef<HTMLButtonElement>(null);const sidebarRef=useRef<HTMLElement>(null);
 const closeMenu=()=>{setOpen(false);menuRef.current?.focus();};
 useEffect(()=>{
  if(!open)return;
  sidebarRef.current?.querySelector<HTMLButtonElement>('.xlt-sidebar-close')?.focus();
  const keyboard=(event:KeyboardEvent)=>{
   if(event.key==='Escape'){event.preventDefault();closeMenu();return;}
   if(event.key!=='Tab')return;
   const items=Array.from(sidebarRef.current?.querySelectorAll<HTMLElement>('a[href],button:not([disabled]),summary')||[]).filter(element=>element.getClientRects().length>0);
   const first=items[0],last=items[items.length-1];
   if(!first)return;
   if(!sidebarRef.current?.contains(document.activeElement)||event.shiftKey&&document.activeElement===first){event.preventDefault();(event.shiftKey?last:first).focus();}
   else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  };
  document.addEventListener('keydown',keyboard);
  return()=>document.removeEventListener('keydown',keyboard);
 },[open]);
 const theme=useTheme2();const styles=useStyles2(getProductStyles);
 const title=[...WORKSPACE_PAGES,...nativePages].find(p=>p.route===page)?.title||'Dashboard';
 const nav=(entries:readonly Entry[])=>entries.map(entry=><a key={entry.route} href={appLink(entry.route,context)} aria-current={entry.route===page||entries===PRIMARY_PAGES&&entry.route===primaryRoute(page)?'page':undefined} onClick={e=>{e.preventDefault();setOpen(false);onNavigate(entry.route);}}><Icon name={icons[entry.route]||'dashboard'} size="md"/><span>{entry.title}</span></a>);
 return <div className={`xlt xlt-dashboard ${styles.root}`} data-theme={theme.isDark?'dark':'light'}>
  {open&&<button className="xlt-menu-scrim" aria-label="Dismiss navigation overlay" onClick={closeMenu}/>}
  <aside ref={sidebarRef} role={open?'dialog':undefined} aria-modal={open||undefined} className={`xlt-sidebar ${open?'is-open':''}`} aria-label="Dashboard navigation">
   <button className="xlt-sidebar-close" aria-label="Close navigation" onClick={closeMenu}><Icon name="times"/></button>
   <div className="xlt-brand"><img src="/public/plugins/xlayer-telemetry-app/img/logo.svg" alt=""/><div><b>XLayer Telemetry</b><small>Agent RL observability</small></div></div>
   <div className="xlt-nav-group">Workspace</div><nav className="xlt-nav" aria-label="XLayer investigation">{nav(PRIMARY_PAGES)}</nav>
   <details className="xlt-library-nav" open={nativePages.some(p=>p.route===page)?true:undefined}><summary>Dashboards</summary><nav className="xlt-nav" aria-label="Canonical dashboards">{nav(nativePages)}</nav></details>
   <a aria-current={page==='demo'?'page':undefined} className="xlt-demo-link" href={appLink('demo',context)} onClick={e=>{e.preventDefault();setOpen(false);onNavigate('demo');}}>Demo Scenarios <small>Synthetic data</small></a>
   <div className="xlt-sidebar-foot">실행을 확인하고, 느린 구간을 조사하세요.</div>
  </aside>
  <div className="xlt-main">
   <div className="xlt-topbar"><div className="xlt-breadcrumb"><button ref={menuRef} className="xlt-menu-toggle" aria-label="Open navigation" aria-expanded={open} onClick={()=>setOpen(!open)}><Icon name="bars"/></button><span>XLayer <i>/</i> <b>{title}</b></span>{synthetic&&<a className="xlt-badge" href={appLink('demo',context)}>Synthetic demo</a>}</div><button className="xlt-theme-toggle" aria-label={`Switch to ${theme.isDark?'Light':'Dark'} theme`} onClick={onTheme}><Icon name="adjust-circle"/></button></div>
   {contextBar}
   {primaryRoute(page)==='investigate'&&<nav className="xlt-investigation-nav" aria-label="Investigation views">{nav(INVESTIGATION_PAGES)}</nav>}
   <main className="xlt-content">{children}</main>
  </div>
 </div>;
}
