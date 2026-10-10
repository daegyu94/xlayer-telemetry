import React, { useEffect, useState, useRef } from "react";
import { AppPlugin, LoadingState, getValueFormat, dateTimeFormat, FieldType, ThemeContext, createTheme, PageLayoutType } from "@grafana/data";
import { locationService } from "@grafana/runtime";
import { Router } from "react-router-dom";
import {Sparkline,useTheme2,Icon} from '@grafana/ui';
import {
  SceneApp,
  SceneAppPage,
  EmbeddedScene,
  SceneTimeRange,
  SceneTimePicker,
  SceneRefreshPicker,
  VariableValueSelectors,
  VariableValueSelectWrapper,
  sceneGraph,
  SceneObjectBase,
  SceneObjectState,
  SceneComponentProps,
  SceneQueryRunner,
  SceneDataProvider,
  TextBoxVariable,
  SceneDataTransformer,
  VizPanel,
  useSceneApp,
} from "@grafana/scenes";
import {
  Catalog,
  loadCatalog,
  findPanel,
  runner,
  viz,
  variables,
  records,
  samples,
} from "./catalog";
import {
  APP_BASE,
  VARIABLE_NAMES,
  Context,
  RecordRow,
  readContext,
  appLink,
  dashboardLink,
  Destination,
  selectStep,
  numeric,
  scalar,
  sceneTime,
  subsystemDestination,
  investigationKey,
} from "./context";
import {
  PHASES,
  SUBSYSTEMS,
  phaseWindow,
  relatedPhaseWindow,
  gaugeSummary,
  ClockProof,
  clockQualifiedCell,
  contextSample,
  phaseComparison,
  stepEvidenceCell,
  metricCell,
  selectPhaseSample,
  Cell,
  PhaseWindow,
} from "./semantics";
import { runModelMetadata, resolveEventStep, resolveKpiEntity, APPLICATION_AGE_IDENTITY_KEYS } from "./selection";
import {boundaryPresentation,entitySelectionHint,compactEntity,DEEP_DIVE_SPECS,detailTabIndex} from './presentation';
import { storageMetrics, storagePlotSelection } from './storage-series';
import {parseStorageOverview,storageDetailGroups,storageSourceContext} from './storage-overview';
import {canonicalRefs} from './data';
import { executionChoices, executionKey, selectExecution, observedPhases, measuredWorkers, pressureOrder, appliedPolicies, workerContext, resourceContext, stepProjection } from "./distributed";
import { replicaRows } from './replicas';
import { tableRows } from './data';
import { INFRA_PANELS, infrastructure, resourceSelection, logPanel } from './infrastructure';
import { InfrastructureView } from './InfrastructureView';
import {RunExplorerView} from './RunExplorerView';
import {displaySummary,displayUiNote} from './ui-copy';

function AsyncDecision({summary}:{summary?:RecordRow}) {
 let value:RecordRow;try{value=JSON.parse(String(summary?.async_decision||'null'));}catch{return null;}
 if(!value||typeof value!=='object'||Array.isArray(value))return null;
 const fields=[['sampleable_count','Sampleable samples'],['remaining','Next-update sample gap'],['should_switch_to_rollout','Switch decision (0/1)'],['effective_switch_cost_seconds','Estimated switch cost (s)']];
 const valid=fields.filter(([key])=>typeof value[key]==='number'&&Number.isFinite(value[key])&&Number(value[key])>=0);
 if(!valid.length)return null;
 return <details className="xlt-completed-detail"><summary>Reported Async Trainer Decision</summary><p className="xlt-muted">다음 update를 위한 replay-buffer decision 기록입니다. 이 Step의 sample 대기, engine 병목 또는 sleep/wake 완료를 증명하지 않습니다.</p>{valid.map(([key,label])=><p key={key}>{label}: <b>{format(value[key])}</b></p>)}</details>;
}

function RolloutReplicas({summary,context,catalog}:{summary?:RecordRow;context:Context;catalog:Catalog}) {
 const rows=replicaRows(summary);
 if(!rows.length)return null;
 return <section><h3>Rollout Replica Coverage</h3><p className="xlt-muted">설정된 배치를 보여줍니다. Request routing이나 resource 소유 관계를 뜻하지 않으며, engine별 sample·baseline·clock 검사를 유지합니다. 누락된 metric은 0이 아닙니다.</p>
  <div className="xlt-scroll"><table><thead><tr><th scope="col">Replica / engine</th><th scope="col">Queue / KV</th><th scope="col">Evidence quality</th><th scope="col">Observations / Next</th></tr></thead><tbody>{rows.map((r,i)=><tr key={`${r.id}-${i}`}>
   <td><b>{r.id}</b><small className="xlt-entity" title={r.instance}>{r.instance} · engine {scalar(r.identity?.engine,'not reported')}</small><small>{r.nodes.join(', ')} · configured</small><small>Serving: {r.serving.state} · sampled context</small><small>Router: {r.serving.registered===undefined?'Unknown':r.serving.registered?'Registered':'Not registered'} · in-flight {format(r.serving.inflight)}</small><small>Applied policy: {r.serving.applied===undefined||r.serving.applied===null?'Unknown':`v${scalar(r.serving.applied)}`} · worker report</small>{Object.keys(r.serving.workload).length>0&&<details><summary>Reported workload / generation</summary><small>{JSON.stringify(r.serving.workload)}</small><small>Generation: {scalar(r.serving.generation,'Unknown')}</small></details>}</td>
   <td>{format(r.signals.vllm_requests_waiting?.current)} requests<br/>{format(typeof r.signals.vllm_kv_cache_usage?.current==='number'?r.signals.vllm_kv_cache_usage.current*100:undefined,'%')} KV<small>per engine · shared service</small></td>
   <td>{r.status.replace(/_/g,' ')}<small>Clock: {r.clock} / baseline {r.baselineClock}</small><details><summary>Coverage · {r.missing.length} issues</summary>{r.missing.length?r.missing.map((issue,j)=><small key={j}>{issue}</small>):<small>응답에 포함된 source coverage입니다. 전체 scrape나 Replica lifecycle을 증명하지 않습니다.</small>}</details></td>
   <td>{r.candidates.length?r.candidates.map((c,j)=><small key={j}>{scalar(c.id)} · {scalar(c.state).replace(/_/g,' ')}</small>):<small>{r.status==='observed'?'관측된 이상이 없습니다':'판단 근거가 부족하거나 correlation이 보류됐습니다'}</small>}<Link to="stage" context={resourceContext(context,{node:r.endpoint_node,engine:r.instance})} catalog={catalog}>Inspect endpoint →</Link></td>
  </tr>)}</tbody></table></div></section>;
}
import { ComparisonWindow } from "./comparison-window";
import { baselineBounds, matrixEntities, matrixEntityKey, filterMatrixEntity, matrixLookback, PHASE_COLORS, SUBSYSTEM_COLORS } from "./matrix-presentation";
import { MATRIX_SPECS } from "./matrix-contract";
import {
  eventTime,
  latestEntitySamples,
  recordedSpanErrors,
  reportedRollout,
  workerPeers,
} from "./mockup";
import { DashboardChrome } from './DashboardChrome';
import {WORKSPACE_PAGES,themeContext} from './navigation';
import {nativeHighlight} from './native-highlight';
import {NATIVE_PAGES,buildNativeWorkspace,NativeWorkspace,NativeWorkspaceModel} from './native-workspace';
import "./style.css";

type Page = typeof WORKSPACE_PAGES[number]['route'] | typeof NATIVE_PAGES[number]['route'];
type ShellState = SceneObjectState & {
  page: Page;
  nativeWorkspace?:NativeWorkspaceModel;
  nativePanels?:VizPanel[];
  catalog: Catalog;
  steps?: SceneQueryRunner;
  spans?: SceneQueryRunner;
  events?: SceneQueryRunner;
  rewardAge?: SceneQueryRunner;
  mfu?:SceneQueryRunner;
  policy?:SceneQueryRunner;
  workload?:SceneQueryRunner;
  summary?: SceneQueryRunner;
  comparison?: SceneQueryRunner;
  candidates?: SceneQueryRunner;
  evidence?: SceneQueryRunner;
  kpis: (SceneQueryRunner | undefined)[];
  matrix: (SceneQueryRunner | undefined)[];
  baselineMatrix?: (SceneQueryRunner|undefined)[];
  baselineSpans?: SceneQueryRunner;
  baselineSteps?: SceneQueryRunner;
  baselineKey?:string;
  matrixSelectionVersion?:number;
  matrixView?:"phase"|"workers";
  workerDurations?: SceneQueryRunner;
  workerSteps?: SceneQueryRunner;
  workerAge?: SceneQueryRunner;
  timeline?: VizPanel;
  approximate?: VizPanel;
  related: VizPanel[];
  detailPanels:(VizPanel|undefined)[];
  storageSamples?: SceneQueryRunner;
  storageStatus?: SceneQueryRunner;
  storageSampleTable?: VizPanel;
  storageStatusTable?: VizPanel;
  storagePlot?: VizPanel;
  storageComparison?: VizPanel;
  storageCluster?: VizPanel[];
  infraComponents?:SceneQueryRunner;
  infraEdges?:SceneQueryRunner;
  infraAvailability?:SceneQueryRunner;
  infraMetrics?:(VizPanel|undefined)[];
  infraSelectionVersion?:number;
  logsPanel?:VizPanel;
  logEvents?:VizPanel;
  pressure: (SceneQueryRunner | undefined)[];
  contextControls:(SceneTimePicker|SceneRefreshPicker)[];
  selectedCell?: {
    contextKey: string;
    phase: string;
    subsystem: string;
    cell: Cell;
    window: PhaseWindow;
  };
};
class Shell extends SceneObjectBase<ShellState> {
  static Component = ShellView;
}
type KpiSpec = {
  name: string;
  key?: string;
  source?: Destination;
  id?: number;
  refs?: string[];
  unit: string;
  description: string;
  phase?: string;
  scale?: number;
};
const KPI_SPECS: KpiSpec[] = [
  {
    name: "Reward",
    source: "stage",
    id: 2,
    refs: ["A"],
    unit: "",
    description: "Run / Worker sample입니다. 아래 freshness 상태를 확인하세요.",
  },
  {
    name: "Step time",
    key: "step_duration_seconds",
    source: "overview",
    id: 30,
    unit: "s",
    description: "Run / Worker에서 완료된 observation입니다.",
  },
  {
    name: "Worker throughput",
    source: "overview",
    id: 31,
    unit: "tok/s",
    description: "SDK가 기록한 Worker sample입니다.",
  },
  {
    name: "Reported rollout",
    source: "stage",
    id: 4,
    refs: ["A"],
    phase: "rollout",
    unit: "s",
    description: "완료된 stage의 Reported duration입니다. 실제 execution boundary가 아닙니다.",
  },
  {
    name: "GPU utilization",
    key: "gpu_utilization_percent",
    source: "overview",
    id: 33,
    unit: "%",
    description: "관측한 device 중 utilization이 가장 높은 값입니다. Node scope이며 Run별 사용률이 아닙니다.",
  },
  {
    name: "KV token hit",
    source: "stage",
    id: 28,
    refs: ["A"],
    scale: 100,
    unit: "%",
    description: "Local prefix TOKEN hit입니다. Rolling / Shared engine 관측이며 Mooncake DFS hit와 다릅니다.",
  },
  {
    name: "3FS latency",
    key: "threefs_p99_latency",
    unit: "ms",
    description: "Entity별 reported p99의 최댓값입니다. Shared-service evidence이며 전체 요청의 p99, RPC p95 또는 disk mean이 아닙니다.",
  },
];
const PRESSURE_SPECS: {
  name: string;
  dashboard: Destination;
  panel: number;
  refs: string[];
  unit: string;
  scope: string;
  scale?: number;
}[] = [
  {
    name: "vLLM waiting",
    dashboard: "stage",
    panel: 9,
    refs: ["A"],
    unit: "requests",
    scope: "Shared engine의 queue를 Sampled로 관측한 값입니다.",
  },
  {
    name: "Ray task states",
    dashboard: "stage",
    panel: 22,
    refs: ["A"],
    unit: "tasks",
    scope: "Session 단위 집계입니다. Node identity는 제공되지 않습니다.",
  },
  {
    name: "Sandbox I/O PSI",
    dashboard: "stage",
    panel: 12,
    refs: ["A"],
    unit: "%",
    scale: 100,
    scope: "Worker/cgroup sample입니다. Phase별 사용량으로 귀속하지 않습니다.",
  },
  {
    name: "RDMA tx wait",
    dashboard: "compute",
    panel: 43,
    refs: ["A"],
    unit: "ticks/s",
    scope: "Port counter rate입니다. Latency 단위가 아닙니다.",
  },
  {
    name: "Storage busy",
    dashboard: "storage",
    panel: 3,
    refs: ["A"],
    unit: "%",
    scale: 100,
    scope: "Node/device의 Rolling busy ratio입니다.",
  },
  {
    name: "GPU utilization",
    dashboard: "compute",
    panel: 2,
    refs: ["A"],
    unit: "%",
    scope: "Node/device의 Sampled 관측값입니다.",
  },
];
function makeScene(page: Page, catalog: Catalog) {
  const context = readContext(window.location.search);
  const queryCache = new Map<string, SceneQueryRunner>();
  const query = (key: Destination, id: number, refs?: string[]) => {
    const panel = findPanel(catalog[key], id);
    if (!panel) return undefined;
    const cacheKey = JSON.stringify([key, id, canonicalRefs(panel.targets||[],refs)]);
    if (queryCache.has(cacheKey)) return queryCache.get(cacheKey);
    const provider = runner(panel, refs);
    queryCache.set(cacheKey, provider);
    return provider;
  };
  const native = (key: Destination, id: number) => {
    const panel = findPanel(catalog[key], id);
    if(!panel)return undefined;
    const pressure=PRESSURE_SPECS.find(s=>s.dashboard===key&&s.panel===id&&
      JSON.stringify(canonicalRefs(panel.targets||[],s.refs))===JSON.stringify(canonicalRefs(panel.targets||[])));
    return viz(panel,pressure&&(page==='analyze'||page==='investigate'||page==='deep-dive')?query(key,id,pressure.refs):undefined);
  };
  const body = new Shell({
    page,
    catalog,
    kpis: [],
    matrix: [],
    related: [],
    detailPanels:[],
    pressure: [],
    contextControls:[new SceneTimePicker({isOnCanvas:false}),new SceneRefreshPicker({ intervals: ['5s','10s','30s','1m'] })],
  });
  const nativePage=NATIVE_PAGES.find(p=>p.route===page);
  if(nativePage){const workspace=buildNativeWorkspace(catalog,nativePage.destination);body.setState({nativeWorkspace:workspace,nativePanels:workspace.panels.map(panel=>viz(panel))});}
  const resourcePage=!!nativePage||page==='infrastructure'||page==='logs'||page==='runs';
  if(page==='runs')body.setState({steps:query('overview',20)});
  if(!resourcePage){
    body.setState({ steps: query("overview", 20), spans: query("timeline", 9) });
    body.setState({mfu:query('overview',40),policy:query('overview',41),workload:query('overview',42)});
  } else if(context.variables.record_id?.some(id=>id!=='.*'&&id!=='$__all'))body.setState({steps:query('overview',20),summary:query('summary',2)});
  if (!resourcePage&&(page !== "deep-dive"||context.variables.candidate_id?.[0]))
    body.setState({
      steps: query("overview", 20),
      summary: query("summary", 2),
      comparison: query("summary", 4),
      candidates: query("summary", 3),
      evidence: query("summary", 6),
      spans: query("timeline", 9),
    });
  if(page==='infrastructure')body.setState({infraComponents:query('compute',INFRA_PANELS.components),infraEdges:query('compute',INFRA_PANELS.edges),infraAvailability:query('compute',INFRA_PANELS.availability),
    infraMetrics:[native('compute',30),native('overview',2),native('compute',2),native('compute',6),native('compute',8),native('compute',9),native('storage',1),native('storage',2),native('storage',30),native('storage',31)]});
  if(page==='logs'){
    const panel=logPanel(findPanel(catalog.logs,1));
    body.setState({logsPanel:panel?viz(panel):undefined,logEvents:native('timeline',10)});
  }
  if(page==='deep-dive')body.setState({summary:query('summary',2),detailPanels:DEEP_DIVE_SPECS.map(s=>s.dashboard&&s.panel!==undefined?native(s.dashboard,s.panel):undefined)});
  if (page === 'deep-dive') {
    const samples = query('storage',101), status = query('storage',104);
    const samplePanel = findPanel(catalog.storage,101), statusPanel = findPanel(catalog.storage,104);
    body.setState({storageSamples:samples,storageStatus:status,
      storageSampleTable:samplePanel ? viz(samplePanel,samples) : undefined,
      storageStatusTable:statusPanel ? viz(statusPanel,status) : undefined,
      storagePlot:native('storage',102),storageComparison:native('storage',103)});
    body.setState({storageCluster:[111,112,113,114,115].map(id=>native('storage',id)).filter(Boolean) as VizPanel[]});
  }
  if (page === "overview")
    body.setState({
      kpis: KPI_SPECS.map((s) =>
        s.source && s.id !== undefined
          ? query(s.source, s.id, s.refs)
          : undefined,
      ),
      events: query("timeline", 10),
      rewardAge: query("stage", 3, ["A"]),
      timeline: native("timeline", 2),
      related: [native("signals", 5), native("signals", 2),native("stage",28),native("stage",60),native("compute",9)].filter(
        Boolean,
      ) as VizPanel[],
    });
  // Scene objects must be direct state properties / array elements. A plain
  // dictionary is not parented by Scenes and silently loses variable/time scope.
  if (page === "analyze")
    body.setState({
      matrix: SUBSYSTEMS.map((key) => {
        const spec = MATRIX_SPECS[key];
        return query(spec.dashboard, spec.panel, spec.refs);
      }),
    });
  if (page === "analyze")
    body.setState({
      workerDurations: query("stage", 4, ["A"]),
      workerSteps: query("overview", 44, ["A"]),
      workerAge: query("stage", 3, ["A"]),
    });
  if (page === "analyze" || page === "investigate" || page === "deep-dive") {
    body.setState({
      pressure: PRESSURE_SPECS.map((s) => query(s.dashboard, s.panel, s.refs)),
      timeline: native("timeline", 2),
    });
  }
  if (page === "timeline")
    body.setState({
      timeline: native("timeline", 2),
      approximate: native("timeline", 3),
      related: [native("timeline", 4), native("timeline", 5)].filter(
        Boolean,
      ) as VizPanel[],
    });
  return new EmbeddedScene({
    $timeRange: new SceneTimeRange({
      from: sceneTime(context.from),
      to: sceneTime(context.to),
      timeZone: context.timezone,
    }),
    $variables: variables(catalog, context),
    body,
    controls: [],
  });
}
let loadedCatalog: Catalog;
function createApp() {
  const pages = (
    [...WORKSPACE_PAGES,...NATIVE_PAGES].map(p=>p.route) as Page[]
  ).map(
    (page) =>
      new SceneAppPage({
        layout: PageLayoutType.Custom,
        title: [...WORKSPACE_PAGES,...NATIVE_PAGES].find(p=>p.route===page)!.title,
        renderTitle: () => null,
        url: `${APP_BASE}/${page}`,
        routePath: `${APP_BASE}/${page}`,
        preserveUrlKeys: [
          "from",
          "to",
          "timezone",
          "theme",
          ...VARIABLE_NAMES.map((n) => `var-${n}`),
        ],
        getScene: () => makeScene(page, loadedCatalog),
      }),
  );
  return new SceneApp({
    pages,
    urlSyncOptions: { updateUrlOnInit: true, createBrowserHistorySteps: true },
  });
}
function Ready() {
  const app = useSceneApp(createApp);
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
const dashboardThemes={light:createTheme({colors:{mode:'light',text:{primary:'#1e293b',secondary:'#526174',link:'#4c67b9'}},typography:{fontSize:14}}),dark:createTheme({colors:{mode:'dark',text:{primary:'#e6ecf5',secondary:'#b4c2d1',link:'#9bb0f2'}},typography:{fontSize:14}})};
function Root() {
  const inheritedTheme=useTheme2();
  const [visualTheme,setVisualTheme]=useState(readContext(window.location.search).theme || (inheritedTheme.isDark?'dark':'light'));
  useEffect(()=>locationService.getHistory().listen(()=>setVisualTheme(readContext(window.location.search).theme || (inheritedTheme.isDark?'dark':'light'))),[inheritedTheme.isDark]);
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    if (
      window.location.pathname === APP_BASE ||
      window.location.pathname === `${APP_BASE}/`
    ) {
      locationService.replace(
        appLink("overview", readContext(window.location.search)),
      );
    }
    loadCatalog()
      .then((c) => {
        loadedCatalog = c;
        if (active) setStatus("ready");
      })
      .catch((e) => {
        if (active) {
          setError(e.message || String(e));
          setStatus("error");
        }
      });
    return () => {
      active = false;
    };
  }, []);
  return status === "ready" ? (
    <ThemeContext.Provider value={dashboardThemes[visualTheme]}><Ready /></ThemeContext.Provider>
  ) : (
    <div className="xlt">
      <p>
        {status === "loading"
          ? "Provisioning된 XLayer Dashboard를 불러오는 중입니다…"
          : `Dashboard를 불러오지 못했습니다. ${displayUiNote(error)}`}
      </p>
    </div>
  );
}
export const plugin = new AppPlugin().setRootPage(Root);

function useData(provider: SceneDataProvider | undefined) {
  const [snapshot, setSnapshot] = useState({provider,data:provider?.state.data});
  useEffect(() => {
    setSnapshot({provider,data:provider?.state.data});
    if (!provider) return;
    const deactivate = provider.activate();
    const sub = provider.subscribeToState((s) => setSnapshot({provider,data:s.data}));
    return () => {
      sub.unsubscribe();
      deactivate();
    };
  }, [provider]);
  return snapshot.provider===provider?snapshot.data:provider?.state.data;
}
function Native({ panel }: { panel?: VizPanel }) {
  return panel ? (
    <div className="xlt-panel">
      <panel.Component model={panel} />
    </div>
  ) : (
    <div className="xlt-empty">
      Loki Timeline을 사용할 수 없습니다. 완료된 duration은 Stage Correlation에서 확인하세요.
    </div>
  );
}
function format(value: unknown, unit = "") {
  const n = numeric(value);
  if (
    n !== undefined &&
    ["s", "ms", "percent", "percentunit", "Bps", "bytes", "short"].includes(
      unit,
    )
  ) {
    const rendered = getValueFormat(unit)(n, 2);
    return `${rendered.prefix || ""}${rendered.text}${rendered.suffix || ""}`;
  }
  return n === undefined
    ? "—"
    : `${new Intl.NumberFormat("en", { maximumFractionDigits: 2, notation: Math.abs(n) >= 10000 ? "compact" : "standard" }).format(n)}${unit ? " " + unit : ""}`;
}
function Delta({ row }: { row?: RecordRow }) {
  const delta = numeric(row?.delta_percent);
  return (
    <span
      title={
        delta === undefined
          ? "저장된 비교 구간에서 유효한 relative delta를 계산할 수 없습니다."
          : `${delta}% vs saved baseline`
      }
      className={
        delta === undefined
          ? "xlt-muted xlt-change"
          : Math.abs(delta) >= 20
            ? "xlt-attention xlt-change"
            : "xlt-change"
      }
    >
      {delta === undefined
        ? numeric(row?.baseline) === 0
          ? "Δ unavailable · baseline 0"
          : "No baseline"
        : `${delta >= 0 ? "+" : ""}${format(delta, "%")} vs baseline`}
    </span>
  );
}
function DataStatus({ provider }: { provider?: SceneQueryRunner }) {
  const data = useData(provider);
  if (!provider)
    return (
      <p className="xlt-empty">
        Loki / diagnosis projection이 설정되지 않았습니다. Metrics와 Deep Dive는 계속 사용할 수 있습니다.
      </p>
    );
  if (data?.state === LoadingState.Error)
    return (
      <p role="alert" className="xlt-error">
        Query failure: {" "}
        {displayUiNote(data.error?.message || data.errors?.map((e) => e.message).join(";") || "상세 오류가 보고되지 않았습니다")}.
        데이터가 없다는 것은 측정값 0과 다릅니다.
      </p>
    );
  if (!data || data.state === LoadingState.Loading)
    return <p className="xlt-muted">불러오는 중입니다…</p>;
  return null;
}
function Link({
  to,
  context,
  children,
  catalog,
  panel,
}: {
  to: Destination;
  context: Context;
  children: React.ReactNode;
  catalog: Catalog;
  panel?:number;
}) {
  return catalog[to] ? (
    <a className="xlt-link" href={dashboardLink(to, context)+(panel!==undefined?`&viewPanel=${panel}`:'')}>
      {children} ↗
    </a>
  ) : (
    <span className="xlt-muted">{children} · unavailable</span>
  );
}
function ShellView({ model }: { model: Shell }) {
  const state = model.useState();
  const theme=useTheme2();
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
  const contextKey = investigationKey(context);
  useEffect(() => {
    // Native VizPanel activation can remount the custom view. Clear only an
    // evidence selection made in a different investigation, never on mount.
    if (state.selectedCell && state.selectedCell.contextKey !== contextKey)
      model.setState({ selectedCell: undefined });
  }, [model, contextKey, state.selectedCell]);
  const literalMatches=(values:string[]|undefined,value:unknown)=>!values?.length||values.some(v=>v==='.*'||v==='$__all')||values.includes(String(value));
  const requested=[...steps,...summaries].filter(row=>!row.identity_conflict&&row.record_id===record&&
    literalMatches(context.variables.cluster,row.cluster)&&literalMatches(context.variables.run_id,row.run_id));
  const requestedIdentities=new Set(requested.map(row=>JSON.stringify([row.cluster,row.run_id,row.observer_node,row.node,row.record_id,row.window_start_ms,row.window_end_ms])));
  const summaryEntities=new Set(summaries.filter(row=>!row.identity_conflict).map(row=>JSON.stringify([row.cluster,row.run_id,row.observer_node,row.node,row.worker_id])));
  const selected=requestedIdentities.size===1?requested[0]:
    state.page==='overview'&&!record?.match(/[^.*]/)&&summaryEntities.size===1?
      [...summaries].filter(row=>!row.identity_conflict).sort((a,b)=>Number(b.window_end_ms)-Number(a.window_end_ms))[0]:undefined;
  const matching = (rows: RecordRow[]) => stepProjection(rows,selected);
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
  return (
    <DashboardChrome page={state.page} context={context} nativePages={NATIVE_PAGES} onNavigate={route=>navigate(route as Page)} onTheme={()=>navigate(state.page,themeContext(context,theme.isDark?'light':'dark'))}
      contextBar={<RunContext model={model} selected={selected} steps={steps} context={context} policySamples={policySamples} activeWorkloads={activeWorkloads} onStep={row=>navigate(state.page,selectStep(row,context))}/>}>
      {state.nativeWorkspace&&<>
        <header className="xlt-header xlt-native-heading"><div><span className="xlt-eyebrow">{NATIVE_PAGES.find(p=>p.route===state.page)?.kicker}</span><h2>{NATIVE_PAGES.find(p=>p.route===state.page)?.title}</h2><p>{NATIVE_PAGES.find(p=>p.route===state.page)?.description}</p></div><a className="xlt-link" href={appLink('infrastructure',context)}>Locate in topology →</a></header>
        <p className="xlt-notice">{NATIVE_PAGES.find(p=>p.route===state.page)?.notice}</p>
        <details className="xlt-filters"><summary>Resource and native panel filters</summary><div>{state.catalog[state.nativeWorkspace.destination]?.templating.list.filter(v=>VARIABLE_NAMES.includes(v.name)&&!['cluster','run_id'].includes(v.name)).map(v=>{const variable=sceneGraph.lookupVariable(v.name,model);return variable?<VariableValueSelectWrapper key={v.name} variable={variable} showAlways/>:null;})}</div></details>
        <NativeHighlights destination={state.nativeWorkspace.destination} sources={state.nativeWorkspace.panels} panels={state.nativePanels||[]}/>
        <NativeWorkspace workspace={state.nativeWorkspace} panels={state.nativePanels||[]} context={context}/>
        <footer>Correlation ≠ Attribution ≠ Causality. 상관관계만으로 소유 관계나 원인을 단정할 수 없습니다. No data는 측정값 0과 다릅니다.</footer>
      </>}
      {!state.nativeWorkspace&&<>
      <header className="xlt-header">
        <div>
          <span className="xlt-eyebrow">
            {WORKSPACE_PAGES.find(p=>p.route===state.page)?.kicker}{' '}
            {synthetic && <span className="xlt-badge">Synthetic demo</span>}
          </span>
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
          <p className="xlt-page-description">{WORKSPACE_PAGES.find(p=>p.route===state.page)?.description}</p>
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
      {selected&&<p className="xlt-muted" aria-label="Correlation clock quality">Clock quality: <b>{correlationClock.replace(/_/g,' ')}</b> · {scalar(comparisonMeta?.correlation_clock_scope,'scope not reported').replace(/_/g,' ')} · {scalar(comparisonMeta?.correlation_clock_method,'method not reported').replace(/_/g,' ')}{correlationClock!=='aligned'&&' · 정밀한 Phase correlation과 delta는 보류됩니다. Raw metrics는 계속 확인할 수 있습니다.'}</p>}

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
      {state.page==='runs'&&<RunExplorerView context={context} steps={steps} liveState={stepData?.state} timeWindow={{from:sceneGraph.getTimeRange(model).state.value.from.valueOf(),to:sceneGraph.getTimeRange(model).state.value.to.valueOf()}}/>}
      {state.page === "overview" && (
        <>
          {stepData?.state===LoadingState.Done&&!steps.length&&<p className="xlt-empty">이 구간에 완료된 Step이 없습니다. Run / Time range를 선택하세요. 이력 누락을 정상 상태로 해석하지 않습니다.</p>}
          <div className="xlt-kpis">
            {KPI_SPECS.map((spec, index) => (
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
                            s.record_id === selected?.record_id &&
                            s.run_id === selected?.run_id,
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
            ))}
            <ErrorKpi spans={spans} data={spanData} />
          </div>
          {reportedMfu.length>0&&<p className="xlt-mfu-strip">Framework가 보고한 MFU · {reportedMfu.slice(0,3).map(s=>`${s.labels.phase}: ${format(s.value,'percentunit')} (${s.labels.worker_id})`).join(' · ')} · 완료된 logger observation</p>}
          <AsyncDecision summary={comparisonMeta}/>
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
              <RelatedTabs panels={state.related}/>
            </div>
            <div>
              <h3><Icon name="info-circle"/> System Signals</h3><HealthSummary candidates={diagnosis}/><p className="xlt-muted">저장된 diagnosis signal입니다. Collector UP을 의미하는 health 상태가 아닙니다.</p></div>
          </section>
          <PolicyLifecycle events={events} context={context} catalog={state.catalog}/>
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
                limit={3}
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
                  <table>
                    <thead>
                      <tr>
                        <th>Signal</th>
                        <th>Current</th>
                        <th>Baseline</th>
                        <th>Change</th>
                        <th>Scope / observation</th>
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
                            <td title={scalar(r.signal)}>{r.signal==='step_duration_seconds'?boundary.timeLabel:scalar(r.signal)}</td>
                            <td>{format(r.current, scalar(r.unit, ""))}</td>
                            <td>{format(r.baseline, scalar(r.unit, ""))}</td>
                            <td>
                              <Delta row={r} />
                            </td>
                            <td>
                              {scalar(r.observation_scope)} ·{" "}
                              {scalar(r.window_statistic, "Unknown statistic")}
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
              <section className="xlt-candidate-section">
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
                        <button onClick={()=>navigate('deep-dive',{...context,variables:{...context.variables,candidate_id:[String(c.candidate_id)]}})}>Deep Dive →</button>
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
      {state.page==='infrastructure'&&<InfrastructureView data={infrastructure(tableRows(infraComponents),tableRows(infraEdges),tableRows(infraAvailability),Date.now())} context={context}
        unavailable={infraComponents?.state===LoadingState.Error?'Query failure':!state.infraComponents?'Canonical Topology panel을 사용할 수 없습니다':undefined}
        onSelect={node=>{const c=node.mapping==='configured'?resourceSelection(context,node):{...context,variables:{...context.variables,infra_component:[node.key]}};navigate('infrastructure',c);model.setState({infraSelectionVersion:(state.infraSelectionVersion||0)+1});}}
        links={(node,c)=><><Link to={node.kind==='storage'?'storage':'compute'} context={c} catalog={state.catalog}>Full resource metrics</Link><a href={appLink('deep-dive',{...c,variables:{...c.variables,candidate_id:[],detail_tab:[node.kind==='storage'?'Local I/O mean':'GPU']}})}>Deep Dive →</a></>}
        metrics={node=>{const indices=node.type==='gpu'?[2,3]:node.type==='ssd'?[6,7,8,9]:node.type==='nic'?[4]:node.kind==='storage'?[0,1,4,5,6,8]:[0,1,2,4,5];return indices.map(index=>state.infraMetrics?.[index]?<Native key={index} panel={state.infraMetrics[index]}/>:<p key={index} className="xlt-empty">Canonical resource panel을 사용할 수 없습니다.</p>);}}/>}
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
 const ids:Partial<Record<Destination,number[]>>={compute:[2,6,30,9],storage:[1,2,30,31],stage:[4,9,28,60],signals:[1,2,5,6]};
 return ids[destination]?<div className="xlt-kpis xlt-native-highlights">{ids[destination]!.map(id=>{const index=sources.findIndex(p=>p.id===id);return index>=0?<NativeHighlight key={id} source={sources[index]} panel={panels[index]}/>:null;})}</div>:null;
}
function NativeHighlight({source,panel}:{source:import('./catalog').Panel;panel:VizPanel}){
 const data=useData(panel.state.$data),result=nativeHighlight(data);
 return <article className="xlt-card xlt-kpi"><span className="xlt-eyebrow">{source.title}</span><strong className={result.state==='observed'?undefined:'xlt-kpi-state'}>{result.state==='observed'?format(result.sample?.value,source.fieldConfig?.defaults?.unit||result.sample?.unit||''):result.state==='multiple'?'Multiple entities':result.state==='error'?'Query failure':result.state==='loading'?'Loading':result.state==='invalid'?'Unknown':'No data'}</strong><small>{result.state==='multiple'?`${result.entities.length} signal entity · Node / Device를 선택하세요`:'반환된 최신 query evaluation입니다. Source scope를 유지합니다.'}</small><small>이 summary만으로 producer freshness가 확인되지는 않습니다.</small><small className="xlt-entity" title={source.description}>{source.description||'Native source 관측이며 Run별 사용량으로 귀속하지 않습니다.'}</small></article>;
}
function Steps({
  rows,
  selected,
  context,
  onSelect,
  limit = 8,
}: {
  rows: RecordRow[];
  selected?: RecordRow;
  context: Context;
  onSelect: (c: Context) => void;
  limit?: number;
}) {
  const unique = [
    ...new Map(rows.map((r) => [String(r.record_id), r])).values(),
  ].filter(
    (r) =>
      numeric(r.window_start_ms) !== undefined &&
      numeric(r.window_end_ms)! > numeric(r.window_start_ms)!,
  );
  const multipleRuns = new Set(unique.map(row => String(row.run_id))).size > 1;
  return (
    <div className="xlt-scroll">
      <table>
        <thead>
          <tr>
            {multipleRuns && <th scope="col">Run</th>}
            <th scope="col">Step</th>
            <th scope="col">Duration</th>
            <th scope="col">Boundary</th>
            <th scope="col">Observer / worker</th>
            <th scope="col">Next</th>
          </tr>
        </thead>
        <tbody>
          {unique
            .sort((a, b) => Number(b.window_end_ms) - Number(a.window_end_ms))
            .slice(0, limit)
            .map((r) => (
              <tr
                key={String(r.record_id)}
                aria-selected={r.record_id === selected?.record_id}
              >
                {multipleRuns && <td>{scalar(r.run_id)}</td>}
                <td>{scalar(r.step)}</td>
                <td>{format(r.step_duration_seconds, "s")}</td>
                <td>{scalar(r.boundary_accuracy)}</td>
                <td>
                  {scalar(r.observer_node || r.node)} / {scalar(r.worker_id)}
                </td>
                <td>
                  <button onClick={() => onSelect(selectStep(r, context))}>
                    Analyze Step {scalar(r.step)} →
                  </button>
                </td>
              </tr>
            ))}
        </tbody>
      </table>
      {!unique.length && (
        <p className="xlt-empty">
          이 구간에 완료된 Step이 없습니다. Run / Time range를 선택하세요. Loki Step history는 선택적 source입니다.
        </p>
      )}
    </div>
  );
}
function Kpi({
  spec,
  provider,
  comparison,
  evidence,
  history,
  ages,
  maxAge,
  reported,
  context,
  boundary,
}: {
  spec: (typeof KPI_SPECS)[number];
  provider?: SceneQueryRunner;
  context:Context;
  boundary:ReturnType<typeof boundaryPresentation>;
  comparison?: RecordRow;
  evidence?: RecordRow;
  history?:{value:number;time:number}[];
  ages?: import("./semantics").Sample[];
  maxAge?: number;
  reported?: number;
}) {
  const theme=useTheme2();
  const data = useData(provider),
    values = samples(data);
  const application=["Reward","Step time","Worker throughput","Reported rollout"].includes(spec.name);
  const selection=resolveKpiEntity(values,{scope:application?'application':'resource',selectedRuns:context.variables.run_id,worker:application?context.variables.worker:undefined,phase:spec.phase,ages:application?ages:undefined,requireFreshness:application,maxAgeSeconds:maxAge??300,ageIdentityKeys:application?APPLICATION_AGE_IDENTITY_KEYS:undefined,evaluationTime:data?.timeRange?.to.valueOf()});
  const latest=selection.sample,age=selection.age,stale=selection.state==='stale';
  const projected = comparison || evidence;
  const trend=(reported!==undefined?[]:projected?[...(history||[])]:values.filter(v=>JSON.stringify(v.labels)===JSON.stringify(latest?.labels))).sort((a,b)=>a.time-b.time);
  let value =
    reported !== undefined
      ? reported
      : projected
        ? projected.current
        : stale
          ? undefined
          : latest?.value;
  const unit =
    reported !== undefined
      ? "s"
      : projected
        ? scalar(projected.unit, "")
        : spec.unit;
  const nativeUnit = [
    "s",
    "ms",
    "percent",
    "percentunit",
    "Bps",
    "bytes",
  ].includes(unit);
  if (spec.scale && !comparison && !evidence && numeric(value) !== undefined)
    value = Number(value) * spec.scale;
  return (
    <article className="xlt-card xlt-kpi">
      <span className="xlt-eyebrow">{spec.key==='step_duration_seconds'?boundary.timeLabel:spec.name}</span>
      <strong className={!projected&&reported===undefined&&selection.state!=='observed'?"xlt-kpi-state":undefined}>
        {!projected && reported===undefined && data?.state === LoadingState.Error ? (
          "Query error"
        ) : !projected && reported===undefined && !data && provider ? (
          "Loading…"
        ) : !projected&&reported===undefined&&selection.state!=='observed'?(
          selection.state==='multiple'?'Multiple entities':selection.state==='freshness-unknown'?'Freshness unknown':selection.state==='stale'?'Stale':selection.state==='invalid'?'Invalid data':'No data'
        ) : nativeUnit ? (
          format(value, unit)
        ) : (
          <>
            {format(value)}
            {unit && <span className="xlt-kpi-unit"> {unit}</span>}
          </>
        )}
      </strong>
      <Delta row={comparison} />
      {numeric(comparison?.baseline)!==undefined&&<small>vs baseline {format(comparison?.baseline,scalar(comparison?.unit,''))}</small>}
      {(projected||selection.state==='observed')&&trend.length>1&&<div className="xlt-spark" title={projected?'저장된 Step observation이며 causal model이 아닙니다.':'표시한 entity의 Sampled 추이입니다.'}>
        <Sparkline theme={theme} width={110} height={25} sparkline={{
          x:{name:'Time',type:FieldType.time,values:trend.map(p=>p.time),config:{}},
          y:{name:spec.name,type:FieldType.number,values:trend.map(p=>p.value),config:{color:{mode:'fixed',fixedColor:'#4566d5'}},state:{range:{min:Math.min(...trend.map(p=>p.value)),max:Math.max(...trend.map(p=>p.value)),delta:Math.max(...trend.map(p=>p.value))-Math.min(...trend.map(p=>p.value))}}}
        }}/>
      </div>}
      {ages && !projected && reported===undefined && age!==undefined && (
        <small>
          {stale
            ? "Stale sample"
            : age === undefined
              ? "Freshness unknown"
              : `Sample age ${format(age, "s")}`}
        </small>
      )}
      {!projected&&reported===undefined&&selection.state!=='observed'&&<small title={displayUiNote(selection.reason)}>{selection.state==='multiple'?`${selection.entities.length} entities · ${displayUiNote(entitySelectionHint(selection.entities.map(s=>s.labels)))}`:selection.state==='freshness-unknown'?'일치하는 producer age를 확인할 수 없습니다':selection.state==='stale'?'Producer age가 허용 범위를 초과했습니다':selection.state==='invalid'?'서로 충돌하는 source data입니다':'일치하는 observation이 없습니다'}</small>}
      {reported !== undefined && (
        <small>Selected {boundary.label} · reported duration</small>
      )}
      {projected && !unit && <small>Unit이 보고되지 않았습니다</small>}
      <small title={spec.description} className="xlt-kpi-scope">
        {comparison || evidence
          ? `${boundary.label} · ${scalar(comparison?.observation_scope || evidence?.observation_scope)}`
          : reported !== undefined
            ? `Reported · ${boundary.label}`
            : spec.name === "KV token hit"
              ? "Rolling · shared engine"
              : spec.name === "GPU utilization"
                ? "Sampled · device"
                : spec.key==='threefs_p99_latency'?'Missing · shared-service':application?'Reported · worker':'Sampled · worker'}
      </small>
      {comparison || evidence ? (
        <small className="xlt-entity" title={scalar(comparison?.entity || evidence?.entity,'Entity not reported')}>
          {scalar(
            comparison?.entity || evidence?.entity,
            "Entity not reported",
          )}
        </small>
      ) : (
        latest && (
          <small className="xlt-entity" title={JSON.stringify(latest.labels)}>
            {compactEntity(latest.labels)}
          </small>
        )
      )}
    </article>
  );
}

function Matrix({model,selected,spans,evidence,baselineStep,baselineSpans,comparability,clockStatus,baselineClockStatus,clockProof,baselineClockProof}:{model:Shell;selected:RecordRow;spans:RecordRow[];evidence:RecordRow[];baselineStep?:RecordRow;baselineSpans:RecordRow[];comparability:string;clockStatus:string;baselineClockStatus:string;clockProof:ClockProof;baselineClockProof:ClockProof}){
 const ctx=readContext(window.location.search),workerKey=ctx.variables.phase_worker?.[0];
 const choices=executionChoices(spans,selected),ownSpans=selectExecution(spans,workerKey),ownBaseline=selectExecution(baselineSpans,workerKey),phases=observedPhases(spans,selected,workerKey);
 const phaseName=(name:string)=>({actor_update:'Training · actor',weight_sync:'Weight Sync',checkpoint_save:'Checkpoint',critic_update:'Training · critic',reference_log_prob:'Reference log prob',reference:'Reference',checkpoint_load:'Checkpoint load',rollout:'Rollout',reward:'Reward'}[name]||name);
 return <><div className="xlt-matrix-toolbar"><div className="xlt-chips"><button aria-pressed={model.state.matrixView!=="workers"} onClick={()=>model.setState({matrixView:'phase'})}>Phase Matrix</button><button aria-pressed={model.state.matrixView==="workers"} onClick={()=>model.setState({matrixView:'workers'})}>Worker Comparison</button></div><label>Execution worker<select aria-label="Execution worker" value={workerKey||''} onChange={event=>{const choice=choices.find(row=>row.key===event.target.value);locationService.push(appLink('analyze',choice?workerContext(ctx,choice.row,choice.key):{...ctx,variables:{...ctx.variables,phase_worker:[]}}));model.setState({matrixSelectionVersion:(model.state.matrixSelectionVersion||0)+1});}}><option value="">Execution path · 구간이 모호하면 Worker를 선택하세요</option>{workerKey&&!choices.some(choice=>choice.key===workerKey)&&<option value={workerKey}>선택한 Worker가 현재 구간에 없습니다</option>}{choices.map(choice=><option key={choice.key} value={choice.key}>{scalar(choice.row.node)} / {scalar(choice.row.worker_id)} · {scalar(choice.row.role)} / {scalar(choice.row.producer)}</option>)}</select></label></div>{model.state.matrixView==='workers'?<WorkerComparison model={model} selected={selected} spans={spans}/>:<div className="xlt-scroll"><table className="xlt-matrix"><thead><tr><th>Subsystem</th>{phases.map(phase=><th key={phase} style={{borderTop:`3px solid ${PHASE_COLORS[phase]}`}}>{phaseName(phase)}<small>{phaseWindow(ownSpans,selected,phase).status==='observed'?`${phaseWindow(ownSpans,selected,phase).accuracy} span`:phaseWindow(ownSpans,selected,phase).status==='ambiguous'?'Ambiguous interval':'No comparable interval'}</small></th>)}</tr></thead><tbody>{SUBSYSTEMS.map(name=><MatrixRow key={name} subsystem={name} phases={phases} model={model} selected={selected} spans={ownSpans} evidence={evidence} baselineStep={baselineStep} baselineSpans={ownBaseline} comparability={comparability} clockStatus={clockStatus} baselineClockStatus={baselineClockStatus} clockProof={clockProof}
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
 return <tr><th><i className="xlt-subsystem-dot" style={{background:SUBSYSTEM_COLORS[s]}}/>{title}<small>{spec.label}</small>{entities.length>1&&<select className="xlt-entity-select" aria-label={`${title} entity`} value={selectedKey||''} onChange={event=>{locationService.push(appLink(model.state.page,{...context,variables:{...context.variables,[variable]:event.target.value?[event.target.value]:[]}}));model.setState({matrixSelectionVersion:(model.state.matrixSelectionVersion||0)+1});}}><option value="">Entity를 선택하세요 ({entities.length})</option>{selectedKey&&!entities.some(e=>e.key===selectedKey)&&<option value={selectedKey}>선택한 Entity가 현재 구간에 없습니다</option>}{entities.map(e=><option key={e.key} value={e.key}>{describe(e.labels)}</option>)}</select>}</th>{phases.map(phase=>{
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
  return <td key={phase}><button className="xlt-cell" disabled={data?.state===LoadingState.Loading} aria-busy={data?.state===LoadingState.Loading} aria-label={`${phase} × ${s} evidence`} title={displayUiNote(cell.explanation)} onClick={()=>{model.setState({selectedCell:{contextKey:investigationKey(context),phase,subsystem:s,cell,window}});setTimeout(()=>document.querySelector('.xlt-evidence')?.scrollIntoView({behavior:'smooth',block:'start'}),0);}}><b>{label}</b>{comparison.comparable&&<span className={`xlt-matrix-delta ${comparison.delta===0?"xlt-delta-flat":comparison.delta&&comparison.delta>0?"xlt-delta-up":"xlt-delta-down"}`} title={displayUiNote(comparison.reason)}>{comparison.delta===undefined?'Δ unavailable · baseline 0':comparison.delta===0?'No change vs baseline':`${comparison.delta>0?'↑ +':'↓ '}${Math.abs(comparison.delta).toFixed(1)}% vs baseline`}</span>}<small>{observed?quality:window.status==='ambiguous'?'Ambiguous span':s==='sandbox'?'No linked worker call':cell.scope}</small>{!spec.rolling&&s!=='ray'&&observed&&<small>{cell.observations} query observations · mean</small>}{s==='sandbox'&&window.span!==parent.span&&<small>Linked tool call</small>}{phase==='rollout'&&!!stepCell.evidence?.length&&<span className="xlt-step-evidence">Step evidence →</span>}</button></td>;
 })}</tr>;
}

function EvidenceDetail({
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
  const ctx = {
    ...context,
    variables: {
      ...context.variables,
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

function ErrorKpi({
  spans,
  data,
}: {
  spans: RecordRow[];
  data?: import("@grafana/data").PanelData;
}) {
  const status = recordedSpanErrors(spans);
  return (
    <article className="xlt-card xlt-kpi">
      <span className="xlt-eyebrow">Recorded errors</span>
      <strong>
        {data?.state === LoadingState.Error
          ? "Query error"
          : !data
            ? "N/A"
            : format(status.count)}
      </strong>
      <span className="xlt-muted">관측된 Span record</span>
      <small>
        Status coverage {status.observed}/{status.total} · limit 5,000
        {status.limited ? " reached" : ""}
      </small>
      <small title="반환된 record만으로 전체 Workload error rate를 판단할 수 없습니다.">
        Coverage limited
      </small>
    </article>
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
                    : new Date(time).toLocaleTimeString()}
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
  const pressureEvidence=stepProjection(records(useData(model.state.evidence)),selected);
  return (
    <section className="xlt-pressure">
      <div className="xlt-section">
        <h3>System Pressure · priority entities</h3>
        <span>구간 끝 시점의 관측값입니다. 원인을 판정한 결과가 아닙니다.</span>
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
}: {
  evidence:RecordRow[];
  spec: (typeof PRESSURE_SPECS)[number];
  provider?: SceneQueryRunner;
  context: Context;
  catalog: Catalog;
}) {
  const data = useData(provider),
    ranked=pressureOrder(samples(data),{kind:spec.name==='GPU utilization'?'utilization':spec.name==='Ray task states'?'task-state':'higher',signal:({"vLLM waiting":"vllm_requests_waiting","GPU utilization":"gpu_utilization_percent","Storage busy":"storage_device_busy_ratio","Sandbox I/O PSI":"sandbox_io_pressure_ratio","RDMA tx wait":"rdma_tx_wait_per_second"} as Record<string,string>)[spec.name],unit:findPanel(catalog[spec.dashboard],spec.panel)?.fieldConfig?.defaults?.unit||spec.unit,scale:1,evidence}),
    entities=ranked.map(row=>row.sample);
  return (
    <article className="xlt-card">
      <span className="xlt-eyebrow">{spec.name}</span>
      {!provider ? (
        <p className="xlt-muted">Unavailable</p>
      ) : data?.state === LoadingState.Error ? (
        <p className="xlt-error">Query error</p>
      ) : !entities.length ? (
        <p className="xlt-muted">No data</p>
      ) : (
        ranked.slice(0, 2).map((row, i) => {const s=row.sample;return (
          <div className="xlt-pressure-value" key={i}>
            <strong>{format(s.value * (spec.scale || 1), spec.unit)}</strong>
            <small className="xlt-entity" title={JSON.stringify(s.labels)}>{compactEntity(s.labels)}</small>
            <small>{row.supporting?'명시적인 Step evidence와 일치합니다':spec.name==='GPU utilization'?'Utilization 관측이며 fault 판정이 아닙니다':spec.name==='Ray task states'?'State 우선순위이며 자동 합산하지 않습니다':'관측된 signal이 가장 높으며 원인 판정은 아닙니다'}</small>
            <Link to={spec.dashboard} context={resourceContext(context,{...s.labels,...(spec.name==='vLLM waiting'&&s.labels.instance?{engine:s.labels.instance}:{})})} catalog={catalog}>Inspect entity →</Link>
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
  const definitions=[['GPU','compute'],['vLLM','rollout'],['Storage','storage'],['Network','communication'],['Ray','ray'],['Sandbox','sandbox']];
  return <div className="xlt-health-grid">{definitions.map(([name,component])=>{const rows=candidates.filter(c=>c.component===component),strong=rows.some(c=>c.state==='strong_signal');return <article className="xlt-card" key={name}><b>{name}</b><span className={strong?'xlt-attention':'xlt-muted'}>{strong?'Strong signal':rows.some(c=>c.state==='supporting_signal')?'Supporting signal':rows.length?'Weak signal':'Not assessed'}</span><small>{rows.length?'저장된 Step candidate':'Subsystem 판단 기록이 없습니다'}</small></article>;})}</div>;
}
function TopChanges({rows}:{rows:RecordRow[]}) {
  return <section><h3>Top Changes · Step evidence</h3><p className="xlt-muted">저장된 비교 구간이며 Phase별 resource 사용량으로 귀속하지 않습니다. Workload comparability는 source가 제공한 기준을 유지합니다.</p><div className="xlt-scroll"><table><thead><tr><th>Signal</th><th>Current</th><th>Baseline</th><th>Delta</th><th>Scope</th></tr></thead><tbody>{[...rows].sort((a,b)=>Math.abs(Number(b.delta_percent)||0)-Math.abs(Number(a.delta_percent)||0)).slice(0,4).map((r,i)=><tr key={i}><td>{scalar(r.signal)}</td><td>{format(r.current,scalar(r.unit,''))}</td><td>{format(r.baseline,scalar(r.unit,''))}</td><td><Delta row={r}/></td><td>{scalar(r.observation_scope)}</td></tr>)}</tbody></table></div></section>;
}
function CommonStorageOverview({model,summary,context,onThreeFS}:{model:Shell;summary?:RecordRow;context:Context;onThreeFS:()=>void}){
 const state=model.useState(),value=parseStorageOverview(summary?.storage_overview);
 const groups=[['connector','Connector RPC'],['dfs_client','Mooncake DFS client'],['master_memory','Master memory']] as const;
 return <div className="xlt-common-storage"><div className="xlt-section"><h3>Common Storage Overview</h3><span className="xlt-badge">Backend / adapter not reported</span></div>
 <DataStatus provider={state.summary}/><p className="xlt-muted">Step / Phase → KV operation → Connector / DFS client → Backend는 조사 경로입니다. 실제로 관측된 execution dependency가 아닙니다.</p>
 {!value?<p className="xlt-empty">선택한 context에 저장된 Common Storage coverage가 없습니다. Native Metric tab은 사용할 수 있으며, 3FS source가 설정돼 있다는 것만으로 Workload backend를 특정하지 않습니다.</p>:<>
 <div className="xlt-health-grid">{groups.map(([layer,title])=>{const entries=value.signals.filter(row=>row.layer===layer),observed=entries.filter(row=>row.current!==null);return <article className="xlt-card" key={layer}><b>{title}</b><span>{observed.length} / {entries.length} reported signals</span><small>{entries.every(row=>row.status==='not_configured')?'Profile not configured':'Shared service · sampled / rolling'}</small></article>;})}</div>
 <details><summary>Source coverage · values, scope, entity and quality</summary><div className="xlt-scroll"><table><thead><tr><th>Signal</th><th>Current / Baseline</th><th>State / Scope</th><th>Entity / Quality</th></tr></thead><tbody>{value.signals.map(row=><tr key={row.signal}><td>{row.signal}<small>{row.unit} · {row.statistic}</small></td><td>{format(row.current,row.unit==='seconds'?'s':row.unit)} / {format(row.baseline,row.unit==='seconds'?'s':row.unit)}</td><td>{row.status.replace(/_/g,' ')}<small>{row.scope}</small></td><td title={JSON.stringify(row.entity)}>{compactEntity(row.entity)}<small>{row.quality_issues.join(', ')||'추가 품질 안내가 없습니다'}</small>{row.entity.node&&<Link to="stage" context={storageSourceContext(context,row)} catalog={state.catalog}>Source metrics</Link>}</td></tr>)}</tbody></table></div></details>
 <p className="xlt-muted">3FS source: {value.threefs.status.replace(/_/g,' ')} · Shared-service 관측입니다. 이 Mooncake client와의 연결은 확인되지 않았습니다.</p></>}
 <button onClick={onThreeFS}>3FS Deep Dive →</button><p className="xlt-notice">Native Metric 누락은 비활성화, idle, 미지원 또는 source 장애 때문일 수 있습니다. 전달된 DFS keys/bytes에는 checksum failure가 겹칠 수 있으며, rate는 물리 IOPS나 operation 실패 확률이 아닙니다.</p>
 </div>;
}
function DeepWorkspace({model,summary,candidate,evidence,panels,context,catalog}:{model:Shell;summary?:RecordRow;candidate?:RecordRow;evidence:RecordRow[];panels:(VizPanel|undefined)[];context:Context;catalog:Catalog}) {
 const[tab,setTab]=useState(()=>detailTabIndex(context.variables.detail_tab?.[0])),proofs=evidence.filter(e=>e.candidate_id===candidate?.candidate_id),spec=DEEP_DIVE_SPECS[tab];
 const[clusterOpen,setClusterOpen]=useState(false),state=model.useState();
 const storageProofs=evidence.filter(e=>String(e.signal||'').startsWith('threefs_'));
 const groups=storageDetailGroups(DEEP_DIVE_SPECS);
 return <section className="xlt-workspace"><CommonStorageOverview model={model} summary={summary} context={context} onThreeFS={()=>setTab(detailTabIndex('3FS evidence'))}/><details className="xlt-storage-cluster" onToggle={event=>setClusterOpen(event.currentTarget.open)}><summary>Storage Cluster Resources · declared DS/MDS inventory</summary><p className="xlt-notice">명시적인 Resource node mapping만 사용합니다. Exporter 가용성은 Node health나 service-to-device 경로의 증거가 아닙니다. GPU host의 Sandbox Local I/O는 Backend DS/MDS resource와 구분합니다.</p>{clusterOpen&&<><div className="xlt-storage-cluster-controls">{["storage_system","storage_node"].map(name=>{const variable=sceneGraph.lookupVariable(name,model);return variable?<VariableValueSelectWrapper key={name} variable={variable} showAlways/>:null;})}</div><div className="xlt-storage-cluster-panels">{state.storageCluster?.map((panel,index)=><Native key={index} panel={panel}/>)}</div></>}</details><div className="xlt-workspace-grid"><div>
  <h3>Key Findings</h3>{candidate?<><span className="xlt-badge xlt-badge-warning">{scalar(candidate.state).replace(/_/g,' ')}</span><p>{displaySummary(findPanel(catalog.summary,3),candidate.summary)}</p>
  {proofs.filter(e=>e.evidence_type==='supporting').slice(0,3).map((e,i)=><p key={i}><b>{i+1}. {scalar(e.signal)}</b><br/>{format(e.baseline,scalar(e.unit,''))} → {format(e.current,scalar(e.unit,''))}<br/><small className="xlt-entity" title={scalar(e.entity,'Entity not reported')}>{scalar(e.observation_scope)} · {scalar(e.entity,'Entity not reported')}</small></p>)}
  <h4>Against / Missing</h4>{proofs.filter(e=>e.evidence_type==='missing'||e.evidence_type==='counter').map((e,i)=><p key={i}>{scalar(e.signal)} · {scalar(e.observation_scope)}</p>)}</>:<p className="xlt-empty">Investigate에서 Candidate를 선택하세요. 이 workspace에서 Supporting / Counter / Missing evidence를 함께 확인할 수 있습니다.</p>}
  <p className="xlt-notice">Shared evidence는 correlation입니다. Run별 소유 관계나 causal path는 확인되지 않았습니다.</p><Link to="timeline" context={context} catalog={catalog}>Detailed Timeline</Link>
 </div><div><h3>Detailed Metrics</h3>{([['Common storage',groups.common],['Backend-specific · implemented',groups.backend],['Related subsystem context',groups.context]] as const).map(([title,items])=><div key={title}><h4>{title}</h4><div className="xlt-chips">{items.map(s=>{const index=detailTabIndex(s.label);return <button key={s.label} aria-pressed={tab===index} onClick={()=>setTab(index)}>{s.label}</button>;})}</div></div>)}
  {spec.label==='3FS evidence'?<div className="xlt-storage-evidence"><StorageCollectionView model={model} context={context}/><details><summary>Saved aggregate evidence</summary><h4>3FS · saved service observations</h4>{storageProofs.length?storageProofs.map((e,i)=><p key={i}><b>{scalar(e.signal)}</b> · {scalar(e.evidence_type)}<br/>{format(e.baseline,scalar(e.unit,''))} → {format(e.current,scalar(e.unit,''))}{!e.unit&&<small>Unit이 보고되지 않았습니다</small>}<small className="xlt-entity" title={scalar(e.entity,'Entity not reported')}>{scalar(e.entity,'Entity not reported')}</small></p>):<p className="xlt-empty">이 구간에 저장된 3FS evidence가 없습니다. RPC p95나 disk mean으로 대체하지 않습니다.</p>}</details></div>:panels[tab]?<Native panel={panels[tab]}/>:<p className="xlt-empty">이 Source의 Canonical panel을 사용할 수 없습니다.</p>}
  <p className="xlt-muted">{displayUiNote(spec.note)}</p><p className="xlt-muted">Connector / DFS client 관측은 Backend에 종속되지 않습니다. 3FS Service evidence는 선택적인 별도 Source이며, 경로가 확인되지 않은 Node/device 관측은 참고 context로 유지합니다.</p>
  <div className="xlt-actions"><Link to="stage" context={context} catalog={catalog}>Full KV / Mooncake</Link><Link to="storage" context={context} catalog={catalog}>Full Storage</Link><Link to="logs" context={context} catalog={catalog}>Logs</Link><Link to="timeline" context={context} catalog={catalog}>Events / Spans</Link></div>
 </div></div></section>;
}

function StorageCollectionView({model,context}:{model:Shell;context:Context}) {
  const state=model.useState(), sampleData=useData(state.storageSamples), statusData=useData(state.storageStatus);
  const variable=sceneGraph.lookupVariable('storage_metric',model) as TextBoxVariable, variableState=variable.useState();
  const selectedValue=variableState?.value;
  const metric=typeof selectedValue==='string'&&selectedValue?selectedValue:context.variables.storage_metric?.[0];
  const rows=records(sampleData), statuses=records(statusData);
  const options=storageMetrics(rows), selection=storagePlotSelection(rows,metric);
  useEffect(()=>{
    if(!state.storagePlot)return;
    const unit=selection.unit==='operations'?'short':selection.unit||'none';
    const config=state.storagePlot.state.fieldConfig;
    if(config?.defaults?.unit!==unit)state.storagePlot.setState({fieldConfig:{...config,defaults:{...config.defaults,unit}}});
    const transformer=state.storagePlot.state.$data;
    if(transformer instanceof SceneDataTransformer){
      const transforms=transformer.state.transformations as any[];
      const filter=transforms.find(t=>t.id==='filterByValue');
      if(filter?.options?.filters?.[0]?.config?.options?.value!==metric){
        transformer.setState({transformations:transforms.map(t=>t.id==='filterByValue'?{...t,options:{...t.options,filters:[{fieldName:'metric_name',config:{id:'equal',options:{value:metric||''}}}]}}:t)});
        transformer.reprocessTransformations();
      }
    }
  },[state.storagePlot,selection.unit,metric]);
  const messages:Record<string,string>={
    'select-metric':'Current collection-point chart에 표시할 Metric을 하나 선택하세요.',
    'no-data':'이 Metric에 유효하고 고유한 Current collection point가 없습니다. 누락된 값은 측정값 0이 아닙니다.',
    'mixed-source':'이 Metric이 여러 Source table에 있습니다. Chart 표시를 보류했습니다. 아래 원본 record를 확인하세요.',
    'mixed-unit':'반환된 record의 Source unit이 서로 다릅니다. Chart 표시를 보류했으며 값을 변환하거나 합치지 않습니다.',
    'multiple-owner':'여러 Owner observation이 선택됐습니다. Chart를 확인하려면 완료된 observation을 하나 선택하세요.'};
  return <section aria-label="3FS collection context">
    <h4>3FS collection context</h4>
    <p className="xlt-notice">Shared-service 보고이며 Phase 또는 Run별 사용량이 아닙니다. Source DateTime의 해상도는 1초입니다. Collection interval과 host별 clock coverage는 보고된 값만 사용하고 보간이나 Phase attribution을 추정하지 않습니다.</p>
    <DataStatus provider={state.storageSamples}/>
    <label>Storage metric <select className="xlt-storage-metric" aria-label="Storage metric" value={metric||''} onChange={event=>{variable.setState({value:event.target.value});(sceneGraph.lookupVariable('detail_tab',model) as TextBoxVariable).setState({value:'3FS evidence'});}}>
      <option value="">Choose metric</option>
      {metric&&!options.includes(metric)&&<option value={metric}>선택한 Metric이 반환된 record에 없습니다</option>}
      {options.map(name=><option key={name} value={name}>{name}</option>)}
    </select></label>
    {selection.state==='ready'&&state.storagePlot?<><p className="xlt-muted">Current collection point는 원본 Source timestamp를 유지합니다. Unit: {selection.unit||'Not reported (raw)'} · {selection.points.length} points. Record table의 Baseline timestamp도 변경하지 않습니다.</p><Native panel={state.storagePlot}/></>:<p className="xlt-empty">{messages[selection.state]||'Collection-point panel을 사용할 수 없습니다.'}</p>}
    {state.storageSampleTable&&<details open><summary>Original collection records · Current / Baseline</summary><Native panel={state.storageSampleTable}/></details>}
    {state.storageComparison&&<details><summary>Comparable collection windows</summary><p className="xlt-muted">Delta는 기존 Diagnosis가 보고한 값만 표시합니다. Clock / Host mapping 또는 collection semantics가 Unknown이면 Baseline이 일치한다고 판단하지 않습니다.</p><Native panel={state.storageComparison}/></details>}
    <details><summary>Saved source coverage · {statuses.length} status records</summary><DataStatus provider={state.storageStatus}/>{state.storageStatusTable&&<Native panel={state.storageStatusTable}/>}</details>
    {!rows.length&&sampleData?.state===LoadingState.Done&&<p className="xlt-empty">이 구간에 저장된 Collection point가 없습니다. 선택적인 Source가 미설정·실패·빈 응답·미지원 상태일 수 있으므로 저장된 Coverage를 확인하세요. 정상 상태를 의미하지 않습니다.</p>}
  </section>;
}

function RelatedTabs({panels}:{panels:VizPanel[]}){const[tab,setTab]=useState(0);return <><div className="xlt-chips">{['GPU','vLLM','KV Cache','Storage','Network'].slice(0,panels.length).map((label,i)=><button key={label} aria-pressed={tab===i} onClick={()=>setTab(i)}>{label}</button>)}</div>{panels[tab]&&<Native panel={panels[tab]}/>}</>;}

function RunContext({model,selected,steps,context,policySamples,activeWorkloads,onStep}:{model:Shell;selected?:RecordRow;steps:RecordRow[];context:Context;policySamples:import('./semantics').Sample[];activeWorkloads:import('./semantics').Sample[];onStep:(row:RecordRow)=>void}){
  const range=sceneGraph.getTimeRange(model);const rangeState=range.useState();
  const choices=[...new Map([...steps,...(selected?[selected]:[])].map(r=>[String(r.record_id),r])).values()].sort((a,b)=>Number(b.window_end_ms)-Number(a.window_end_ms)).slice(0,40);
  const control=(name:string)=>{const variable=sceneGraph.lookupVariable(name,model);return variable?<VariableValueSelectWrapper variable={variable} showAlways layout="vertical"/>:null;};
  return <section className="xlt-run-context xlt-context-top" aria-label="Run Context">
    <div className="xlt-context-fields">
      <div className="xlt-context-variable">{control('cluster')}</div><div className="xlt-context-variable">{control('run_id')}</div>
      <label className="xlt-context-step">{boundaryPresentation(selected).label}<select aria-label="Completed Step" value={selected?.record_id?String(selected.record_id):''} onChange={e=>{const row=choices.find(r=>String(r.record_id)===e.target.value);if(row)onStep(row);}}><option value="">완료된 Observation을 선택하세요</option>{choices.map(row=><option key={String(row.record_id)} value={String(row.record_id)}>{boundaryPresentation(row).label} {scalar(row.step)} · {format(row.step_duration_seconds,'s')}</option>)}</select></label>
      <div className="xlt-context-time"><span>Time range</span><div>{model.state.contextControls.map((c,i)=>{const Control=c.Component as React.ComponentType<{model:any}>;return <Control key={i} model={c}/>;})}</div><div className="xlt-visible-range">{dateTimeFormat(rangeState.value.from,{timeZone:range.getTimeZone(),format:'MMM D, HH:mm:ss'})} → {dateTimeFormat(rangeState.value.to,{timeZone:range.getTimeZone(),format:'HH:mm:ss'})}</div></div>
    </div>
    <div className="xlt-context-meta"><span>Policy <b>{scalar(selected?.policy_version,policySamples.length===1?format(policySamples[0].value):'Not reported')}</b> · trainer version (reported)</span><span>Wrapped command <b>{activeWorkloads.length===1?scalar(activeWorkloads[0].labels.state):'Not reported'}</b> · latest report</span><details><summary>Observer / Resource</summary><div>{control('source_node')}{control('node')}{control('worker')}{control('role')}</div></details></div>
  </section>;
}

function compactMatrixValue(value:number,unit:string):string{const formatted=getValueFormat(unit)(value,unit==='short'||unit==='percent'||unit==='percentunit'?0:1);return `${formatted.prefix||''}${formatted.text}${formatted.suffix||''}`;}

function WorkerComparison({model,selected,spans}:{model:Shell;selected:RecordRow;spans:RecordRow[]}){
 const data=useData(model.state.matrix[0]),values=samples(data);const rows=measuredWorkers(spans,selected);
 return <section><h3>Measured Worker Comparison</h3><p className="xlt-muted">행마다 선언된 execution identity를 하나 유지합니다. Peer duration은 operation, scope와 Workload fingerprint가 일치해야 비교합니다. GPU는 연결된 Sampled device이며 Worker 사용량이 아닙니다.</p><div className="xlt-scroll"><table><thead><tr><th>Worker / node</th><th>Phase coverage</th><th>Rollout call</th><th>Peer median / delta</th><th>GPU</th><th>Next</th></tr></thead><tbody>{rows.map(row=>{
  const gpu=row.window.span?.gpu;const device=gpu===undefined?undefined:gaugeSummary(values.filter(value=>value.labels.gpu===String(gpu)),row.window).sample;
  return <tr key={row.key}><td>{scalar(row.row.worker_id)} · {scalar(row.row.node)}<small>{scalar(row.row.producer)} / {scalar(row.row.role)}</small></td><td>{row.phases} observed phase types · {row.count} spans</td><td>{row.duration!==undefined?`${format(row.duration,'s')}${row.window.status==='observed'?'':' · call only / clock unmapped'}`:row.window.status}</td><td>{row.peers>=3?`${format(row.median,'s')} · ${row.delta===undefined?'Δ unavailable':format(row.delta,'%')}`:'No matched peer cohort'}</td><td>{device?`${format(device.value,'percent')} · sampled`:'GPU identity 또는 Sample을 확인할 수 없습니다'}</td><td><button onClick={()=>{const ctx=readContext(window.location.search);locationService.push(appLink('analyze',workerContext(ctx,row.row,row.key)));model.setState({matrixSelectionVersion:(model.state.matrixSelectionVersion||0)+1,matrixView:'phase'});}}>Inspect worker →</button></td></tr>;
 })}</tbody></table></div></section>;
}
function PolicyLifecycle({events,context,catalog}:{events:RecordRow[];context:Context;catalog:Catalog}){
 const rows=appliedPolicies(events).filter(row=>!context.variables.run_id?.length||context.variables.run_id.includes('.*')||context.variables.run_id.includes('$__all')||context.variables.run_id.includes(String(row.run_id)));
 return <details className="xlt-completed-detail"><summary>Policy / KV Lifecycle · {new Set(rows.map(row=>`${row.cluster}/${row.run_id}/${row.node}/${row.worker_id}`)).size} workers with applied-version events</summary><p className="xlt-notice">Producer가 Worker scope로 보고한 weights.applied만 적용 경계로 사용합니다. Trainer version과 KV counter는 실제 적용 Coverage나 Causality를 증명하지 않습니다.</p>{!rows.length?<p className="xlt-empty">이 구간에 Worker의 Policy 적용 Event가 없습니다. Native weight 적용을 확인한 지점에서 명시적인 계측을 활성화하세요.</p>:<div className="xlt-scroll"><table><thead><tr><th>Applied boundary</th><th>Worker</th><th>Version</th><th>Next</th></tr></thead><tbody>{rows.slice(-12).reverse().map((row,index)=><tr key={index}><td>{new Date(eventTime(row)!).toLocaleTimeString()} · {row.boundary_accuracy==='calibrated'?'calibrated':'node clock'}</td><td>{scalar(row.node)} / {scalar(row.worker_id)}</td><td>v{scalar(row.policy_version)} · producer reported</td><td><Link to="timeline" context={{...context,variables:{...context.variables,run_id:[String(row.run_id)],trace_id:['.*'],record_id:[]}}} catalog={catalog}>Timeline / KV</Link></td></tr>)}</tbody></table></div>}</details>;
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
