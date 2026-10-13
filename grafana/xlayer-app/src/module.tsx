import { DemoBoundary } from './DemoBoundary';
import { useEffect, useState } from 'react';
import { AppPlugin, ThemeContext, createTheme } from '@grafana/data';
import { locationService } from '@grafana/runtime';
import { Router } from 'react-router-dom';
import { loadCatalog } from './catalog';
import { APP_BASE, readContext, appLink, appTheme } from './context';
import { displayUiNote } from './ui-copy';
import { validTimezone } from './time-context';
import type { Catalog } from './catalog';
import { createApp } from './app-scene';
import { contextIssue, CatalogRetryContext } from './app-context';

function Ready({catalog}: {catalog: Catalog}) {
  // A catalog retry must discard the previous pages' cached scene metadata.
  const [app] = useState(() => createApp(catalog));
  const [location, setLocation] = useState(locationService.getLocation());
  useEffect(
    () =>
      locationService
        .getHistory()
        .listen(() => setLocation(locationService.getLocation())),
    [],
  );
  return (
    <Router location={location} navigator={locationService.getHistory() as any}>
      <app.Component model={app} />
    </Router>
  );
}

const dashboardThemes={light:createTheme({colors:{mode:'light'}}),dark:createTheme({colors:{mode:'dark'}})};

function Root() {
  const [visualTheme,setVisualTheme]=useState(appTheme(readContext(window.location.search)));
  useEffect(()=>locationService.getHistory().listen(()=>setVisualTheme(appTheme(readContext(locationService.getLocation().search)))),[]);
  const [catalog,setCatalog]=useState<Catalog>();
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [refresh,setRefresh]=useState(0),[revision,setRevision]=useState(0),[refreshing,setRefreshing]=useState(false);
  const [context,setContext]=useState(()=>readContext(window.location.search));
  useEffect(()=>locationService.getHistory().listen(()=>setContext(readContext(locationService.getLocation().search))),[]);
  const invalidContext=contextIssue(context);
  useEffect(() => {
    let active = true;
    setRefreshing(true);setError('');
    if (
      window.location.pathname === APP_BASE ||
      window.location.pathname === `${APP_BASE}/`
    ) {
      locationService.replace(
        appLink(readContext(window.location.search).variables.run_id?.some(v=>v!=='.*'&&v!=='$__all')?"overview":"runs", readContext(window.location.search)),
      );
    }
    loadCatalog()
      .then((c) => {
        if (active) {setCatalog(c);setStatus("ready");setRevision(v=>v+1);setRefreshing(false);}
      })
      .catch((e) => {
        if (active) {
          setError(e.message || String(e));
          setStatus(current=>current==='ready'?'ready':'error');setRefreshing(false);
        }
      });
    return () => {
      active = false;
    };
  }, [refresh]);
  if(invalidContext)return <ThemeContext.Provider value={dashboardThemes[appTheme(context)]}><div className="xlt"><p role="alert" className="xlt-error">{invalidContext}</p><p>Run 선택은 유지됩니다. 최근 30분으로 복구하면 시간 범위가 변경됩니다.</p><button onClick={()=>{const current=readContext(locationService.getLocation().search);locationService.replace(appLink(window.location.pathname.slice(APP_BASE.length+1)||'overview',{...current,from:'now-30m',to:'now',timezone:validTimezone(current.timezone)?current.timezone:'browser'}));}}>Use last 30 minutes</button></div></ThemeContext.Provider>;
  return <ThemeContext.Provider value={dashboardThemes[visualTheme]}>{status === "ready" && catalog ? (
    <CatalogRetryContext.Provider value={{retry:()=>setRefresh(v=>v+1),refreshing,error}}><DemoBoundary generation={window.location.pathname.endsWith('/demo')?undefined:context.variables.demo_generation?.[0]}><Ready catalog={catalog} key={`${revision}/${context.variables.demo_generation?.[0]||''}`}/></DemoBoundary></CatalogRetryContext.Provider>
  ) : (
    <div className="xlt">
      <p>
        {status === "loading"
          ? "Provisioning된 XLayer Dashboard를 불러오는 중입니다…"
          : `Dashboard를 불러오지 못했습니다. ${displayUiNote(error)}`}
      </p>
      {status==='error'&&<button onClick={()=>setRefresh(v=>v+1)} disabled={refreshing}>Retry dashboards</button>}
    </div>
  )}</ThemeContext.Provider>;
}

export const plugin = new AppPlugin().setRootPage(Root);
