import React, { useEffect, useState, useRef } from "react";
import { AppPlugin, LoadingState, getValueFormat, dateTimeFormat, FieldType,ThemeContext,createTheme,PageLayoutType } from "@grafana/data";
import { locationService } from "@grafana/runtime";
import { Router } from "react-router-dom";
import {Sparkline,useTheme2} from '@grafana/ui';
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
import { resolveEventStep, resolveKpiEntity, APPLICATION_AGE_IDENTITY_KEYS } from "./selection";
import {boundaryPresentation,entitySelectionHint,compactEntity,DEEP_DIVE_SPECS,detailTabIndex} from './presentation';
import { storageMetrics, storagePlotSelection } from './storage-series';
import {parseStorageOverview,storageDetailGroups,storageSourceContext} from './storage-overview';
import {canonicalRefs} from './data';
import { executionChoices, executionKey, selectExecution, observedPhases, measuredWorkers, pressureOrder, appliedPolicies, workerContext, resourceContext, stepProjection } from "./distributed";
import { ComparisonWindow } from "./comparison-window";
import { baselineBounds, matrixEntities, matrixEntityKey, filterMatrixEntity, matrixLookback, PHASE_COLORS, SUBSYSTEM_COLORS } from "./matrix-presentation";
import { MATRIX_SPECS } from "./matrix-contract";
import {Page,UiVersion,PAGES,PAGE_LABELS,sceneRoutes,versionFromPath,switchVersion,INFRASTRUCTURE_PANELS,LOGS_PANELS} from './pages';
import {infrastructureModel,selectInfrastructure,ResourceNode} from './infrastructure';
import {timelineCalls} from "./workspace-model";
import {WorkspaceIcon,WorkspaceMark,WORKSPACE_COPY,signalTitle} from "./workspace-design";
import {TopologyView} from './topology-view';
import {
  eventTime,
  latestEntitySamples,
  recordedSpanErrors,
  reportedRollout,
  workerPeers,
} from "./mockup";
import "./style.css";
import './workspace.css';
import './mockup.css';

type ShellState = SceneObjectState & {
  page: Page;
  uiVersion:UiVersion;
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
  components?:SceneQueryRunner;
  relationships?:SceneQueryRunner;
  availability?:SceneQueryRunner;
  resourceGpus?:SceneQueryRunner;
  resourceDevices?:SceneQueryRunner;
  infraPanels?:(VizPanel|undefined)[];
  referencePanels?:(VizPanel|undefined)[];
  analysisRelated?:(VizPanel|undefined)[];
  logPanels?:Array<VizPanel|undefined>;
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
    description: "Run / worker sample · freshness below",
  },
  {
    name: "Step time",
    key: "step_duration_seconds",
    source: "overview",
    id: 30,
    unit: "s",
    description: "Completed observation · run / worker",
  },
  {
    name: "Worker throughput",
    source: "overview",
    id: 31,
    unit: "tok/s",
    description: "SDK sample · worker",
  },
  {
    name: "Reported rollout",
    source: "stage",
    id: 4,
    refs: ["A"],
    phase: "rollout",
    unit: "s",
    description: "Reported completed stage · not an execution boundary",
  },
  {
    name: "GPU utilization",
    key: "gpu_utilization_percent",
    source: "overview",
    id: 33,
    unit: "%",
    description: "Busiest sampled device · node scope",
  },
  {
    name: "KV token hit",
    source: "stage",
    id: 28,
    refs: ["A"],
    scale: 100,
    unit: "%",
    description: "Local prefix TOKEN hit · rolling / shared engine",
  },
  {
    name: "3FS latency",
    key: "threefs_p99_latency",
    unit: "ms",
    description: "Maximum reported per-entity p99 · shared-service diagnosis evidence; not RPC p95 or disk mean",
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
    scope: "Shared engine · sampled queue",
  },
  {
    name: "Ray task states",
    dashboard: "stage",
    panel: 22,
    refs: ["A"],
    unit: "tasks",
    scope: "Session aggregation · node identity unavailable",
  },
  {
    name: "Sandbox I/O PSI",
    dashboard: "stage",
    panel: 12,
    refs: ["A"],
    unit: "%",
    scale: 100,
    scope: "Worker/cgroup sample · no phase attribution",
  },
  {
    name: "RDMA tx wait",
    dashboard: "compute",
    panel: 43,
    refs: ["A"],
    unit: "ticks/s",
    scope: "Port counter rate · not latency",
  },
  {
    name: "Storage busy",
    dashboard: "storage",
    panel: 3,
    refs: ["A"],
    unit: "%",
    scale: 100,
    scope: "Node/device · rolling busy ratio",
  },
  {
    name: "GPU utilization",
    dashboard: "compute",
    panel: 2,
    refs: ["A"],
    unit: "%",
    scope: "Node/device · sampled",
  },
];
function makeScene(page: Page, catalog: Catalog,uiVersion:UiVersion='classic') {
  const context = readContext(window.location.search,uiVersion);
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
    const display=uiVersion==='workspace'&&panel.type==='timeseries'?{...panel,options:{...panel.options,legend:{...panel.options?.legend,displayMode:'list',placement:'bottom'},tooltip:{...panel.options?.tooltip,mode:'multi'}},fieldConfig:{...panel.fieldConfig,defaults:{...panel.fieldConfig?.defaults,custom:{...panel.fieldConfig?.defaults?.custom,lineWidth:1,fillOpacity:8,showPoints:'never',axisBorderShow:false}}}}:panel;
    return viz(display,page==='infrastructure'&&(key==='storage'&&id===1||key==='compute'&&id===2)?query(key,id):pressure&&(page==='analyze'||page==='investigate'||page==='deep-dive')?query(key,id,pressure.refs):undefined);
  };
  const body = new Shell({
    page,
    uiVersion,
    catalog,
    kpis: [],
    matrix: [],
    related: [],
    detailPanels:[],
    pressure: [],
    contextControls:[new SceneTimePicker({isOnCanvas:false}),new SceneRefreshPicker({ intervals: ['5s','10s','30s','1m'] })],
  });
  body.setState({ steps: query("overview", 20), spans: query("timeline", 9) });
  body.setState({mfu:query('overview',40),policy:query('overview',41),workload:query('overview',42)});
  if (!['infrastructure','logs'].includes(page)&&(page !== "deep-dive"||context.variables.candidate_id?.[0]))
    body.setState({
      steps: query("overview", 20),
      summary: query("summary", 2),
      comparison: query("summary", 4),
      candidates: query("summary", 3),
      evidence: query("summary", 6),
      spans: query("timeline", 9),
    });
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
  if(page==='overview'||page==='infrastructure')body.setState({components:query('compute',70),relationships:query('compute',71),availability:query('compute',72)});
  if(page==='infrastructure')body.setState({referencePanels:[native('compute',2),native('compute',8),native('storage',1),native('compute',42)],resourceGpus:query('compute',2),resourceDevices:query('storage',1),infraPanels:INFRASTRUCTURE_PANELS.map(s=>native(s.dashboard,s.panel))});
  if(page==='logs'){
    const logSource=findPanel(catalog.logs,1);
    // The canonical log page calls its directory run_id. Scene Run context
    // retains telemetry ownership, so alias only this template variable.
    const logPanel=logSource?{...logSource,targets:logSource.targets?.map(target=>({...target,expr:target.expr?.replace(/\$run_id\b/g,'$log_run_id')}))}:undefined;
    body.setState({events:query('timeline',10),logPanels:[logPanel?viz(logPanel):undefined,...LOGS_PANELS.slice(1).map(s=>native(s.dashboard,s.panel))]});
  }
  // Scene objects must be direct state properties / array elements. A plain
  // dictionary is not parented by Scenes and silently loses variable/time scope.
  if (page === "analyze")
    body.setState({
      matrix: SUBSYSTEMS.map((key) => {
        const spec = MATRIX_SPECS[key];
        return query(spec.dashboard, spec.panel, spec.refs);
      }),
    });
  if (page === "analyze")body.setState({analysisRelated:[native("stage",2),native("overview",30),native("overview",31),native("stage",4)]});
  if (page === "analyze")
    body.setState({
      workerDurations: query("stage", 4, ["A"]),
      workerSteps: query("overview", 44, ["A"]),
      workerAge: query("stage", 3, ["A"]),
    });
  if(page==='investigate')body.setState({related:[native('signals',5),native('stage',60),native('compute',9)].filter(Boolean) as VizPanel[]});
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
    controls: uiVersion==='workspace'?undefined:[],
  });
}
let loadedCatalog: Catalog;
function createApp() {
  const pages = sceneRoutes().map(
    ({page,version,path}) =>
      new SceneAppPage({
        title: PAGE_LABELS[page],
        layout: version==='workspace'?PageLayoutType.Custom:PageLayoutType.Standard,
        renderTitle: (title) => version==='workspace'?null:(
          <h1 className="xlt-page-title">
            XLayer Telemetry <span> / {title}</span>
          </h1>
        ),
        url: path,
        routePath: path,
        preserveUrlKeys: [
          "from",
          "to",
          "timezone", "kiosk",
          ...VARIABLE_NAMES.map((n) => `var-${n}`),
        ],
        getScene: () => makeScene(page, loadedCatalog,version),
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
function Root() {
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [uiVersion,setUiVersion]=useState(versionFromPath(window.location.pathname));
  useEffect(()=>locationService.getHistory().listen(()=>setUiVersion(versionFromPath(window.location.pathname))),[]);
  useEffect(() => {
    let active = true;
    if (
      window.location.pathname === APP_BASE ||
      window.location.pathname === `${APP_BASE}/`||window.location.pathname===`${APP_BASE}/v2`||window.location.pathname===`${APP_BASE}/v2/`
    ) {
      locationService.replace(
        appLink("overview", readContext(window.location.search,versionFromPath(window.location.pathname))),
      );
    }
    if(versionFromPath(window.location.pathname)==='workspace'&&!new URLSearchParams(window.location.search).has('kiosk'))window.location.replace(appLink(window.location.pathname.split('/').pop()||'overview',readContext(window.location.search,'workspace')));
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
  const theme=React.useMemo(()=>createTheme({colors:{mode:uiVersion==='workspace'?'dark':'light',...(uiVersion==='workspace'?{background:{canvas:'#00111c',primary:'#041b2b',secondary:'#082438',elevated:'#0b2c40'},text:{primary:'#edf5fd',secondary:'#a9c7df',link:'#00c8ef'},border:{weak:'#123c53',medium:'#19516c',strong:'#0084b4'}}:{})}}),[uiVersion]);
  return <ThemeContext.Provider value={theme}><div className="xlt-frame" data-ui={uiVersion}>{status === "ready" ? (
    <Ready />
  ) : (
    <div className="xlt" >
      <p>
        {status === "loading"
          ? "Loading provisioned XLayer dashboards…"
          : error}
      </p>
    </div>
  )}</div></ThemeContext.Provider>;
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
      Loki timeline unavailable. Open Stage Correlation for completed durations.
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
          ? "No usable relative delta in the saved comparable-window projection"
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
        Loki / diagnosis projection is not provisioned. Metrics and Deep Dive
        remain available.
      </p>
    );
  if (data?.state === LoadingState.Error)
    return (
      <p role="alert" className="xlt-error">
        Query failed:{" "}
        {data.error?.message || data.errors?.map((e) => e.message).join(";")}.
        No data is not a measured zero.
      </p>
    );
  if (!data || data.state === LoadingState.Loading)
    return <p className="xlt-muted">Loading…</p>;
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
  const [location, setLocation] = useState(locationService.getLocation());
  useEffect(() => locationService.getHistory().listen(() => setLocation(locationService.getLocation())), []);
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
  const steps = records(stepData),
    summaries = records(summaryData),
    comparisons = records(compareData),
    candidates = records(candidateData),
    evidence = records(evidenceData),
    spans = records(spanData),
    events = records(eventData);
  const context = readContext(location.search,state.uiVersion),
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
    <div className="xlt-app-layout" data-ui={state.uiVersion} data-page={state.page}>
    {state.uiVersion==='workspace'&&<WorkspaceNavigation page={state.page} context={context}/>}
    <div className="xlt" data-page={state.page}>
      <header className="xlt-header">
        <div>
          <span className="xlt-eyebrow">
            XLAYER TELEMETRY{" "}
            {synthetic && <span className="xlt-badge">Synthetic demo</span>}
          </span>
          <h2>
            {state.uiVersion==='workspace'?WORKSPACE_COPY[state.page].title:state.page === "overview"
              ? "Run Overview"
              : state.page === "analyze"
                ? "Phase × Subsystem Analysis"
                : state.page === "investigate"
                  ? `${boundary.label} Investigation`
                  : state.page === "timeline"
                    ? "Follow the same interval"
                  : state.page==='infrastructure'?'Cluster Infrastructure':state.page==='logs'?'Logs & Events':workspaceCandidate?`Deep Dive: ${scalar(workspaceCandidate.component)}`:"Choose a subsystem"}
          </h2>
          {state.uiVersion==='workspace'&&<p className="xlt-reference-subtitle">{WORKSPACE_COPY[state.page].description}</p>}
          <p className="xlt-header-context">
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
          {(policySamples.length||activeWorkloads.length)>0&&<p className="xlt-muted">Policy: producer-reported trainer version · Status: latest wrapper node-clock report, not full async Run completion.</p>}
          {selected&&boundary.note&&<p className="xlt-muted">{boundary.note}</p>}
          {selected && (context.variables.run_id?.length!==1||context.variables.run_id[0]!==selected.run_id) && (
            <p className="xlt-muted">
              Run filter: {context.variables.run_id?.join(", ") || "All"} · this
              diagnosed Step belongs to {scalar(selected.run_id)}
            </p>
          )}
        </div>
        <div className="xlt-header-actions">
          <a className="xlt-version-switch" href={appLink(state.page,switchVersion(context,state.uiVersion==='classic'?'workspace':'classic'))}>{state.uiVersion==='classic'?'V2 Workspace':'V1 Classic'}</a>
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
      <nav className="xlt-nav xlt-reference-tabs" aria-label="XLayer investigation">
        <>
          {PAGES.filter(p=>state.uiVersion!=='workspace'||p!=='logs').map(
            (p) => (
              <a
                key={p}
                aria-current={state.page === p ? "page" : undefined}
                href={appLink(p, context)}
                onClick={(e) => {
                  e.preventDefault();
                  navigate(p);
                }}
              >
                {state.uiVersion==='workspace'&&<WorkspaceIcon name={WORKSPACE_COPY[p].icon}/>}
                <span>{
                  {
                    overview: "Overview",
                    analyze: "Analyze",
                    investigate: "Investigate",
                    timeline: "Timeline",
                    "deep-dive": "Deep Dive",
                    infrastructure:'Infrastructure',logs:'Logs & Events',
                  }[p]
                }{state.uiVersion==='workspace'&&<small>{({overview:'전체 현황',analyze:'성능 분석',investigate:'상세 분석','deep-dive':'스토리지 심층 분석',infrastructure:'인프라 현황',logs:'로그 검색'} as Record<string,string>)[p]}</small>}</span>
              </a>
            ),
          )}
        </>
      </nav>
      <RunContext model={model} selected={selected} steps={steps} context={context} policySamples={policySamples} activeWorkloads={activeWorkloads} onStep={row=>navigate(state.page,selectStep(row,context))}/>
      {selected&&!['infrastructure','logs'].includes(state.page)&&<p className="xlt-muted xlt-clock-quality" aria-label="Correlation clock quality">Clock quality: <b>{correlationClock.replace(/_/g,' ')}</b> · {scalar(comparisonMeta?.correlation_clock_scope,'scope not reported').replace(/_/g,' ')} · {scalar(comparisonMeta?.correlation_clock_method,'method not reported').replace(/_/g,' ')}{correlationClock!=='aligned'&&' · precise phase correlation and deltas withheld; raw metrics remain available'}</p>}

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
      {state.page === "overview" && (
        <>
          {stepData?.state===LoadingState.Done&&!steps.length&&<p className="xlt-empty">No completed Step in this interval. Select a Run/time range; missing history is not a healthy verdict.</p>}
          {state.uiVersion==='workspace'&&<ReferenceOverview model={model} context={context} selected={selected} events={events} steps={steps} spans={spans}/>}
          <details className="xlt-reference-additional" open={state.uiVersion!=='workspace'}><summary>Agent RL KPIs · all shared Classic / Workspace values</summary><div className="xlt-kpis">
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
          </div></details>
          {reportedMfu.length>0&&<p className="xlt-mfu-strip">Framework-reported MFU · {reportedMfu.slice(0,3).map(s=>`${s.labels.phase}: ${format(s.value,'percentunit')} (${s.labels.worker_id})`).join(' · ')} · completed logger observation</p>}
          <section className="xlt-overview-top xlt-classic-overview"><div>
            <div className="xlt-section">
              <h3>Agent RL Timeline</h3>
              <button onClick={() => navigate("timeline")}>
                Open timeline →
              </button>
            </div>
            <p className="xlt-muted">
              Exact / calibrated application spans. Events without an interval
              are not phase durations.
            </p>
            <div className="xlt-phase-legend">{Object.entries(PHASE_COLORS).map(([name,color])=><span key={name}><i style={{background:color}}/>{name.replace(/_/g," ")}</span>)}</div>
            <Native panel={state.timeline} />
          </div><div><h3>Recent Events</h3>
              <DataStatus provider={state.events} />
              <RecentEvents
                rows={events}
                steps={steps}
                spans={spans}
                context={context}
                catalog={state.catalog}
              />
            </div></section>
          <section className="xlt-observe-grid xlt-classic-overview">
            <div>
              <h3>Related Metrics</h3>
              <p className="xlt-muted">
                Same time range · sampled/shared signals are correlation, not
                attribution.
              </p>
              <RelatedTabs panels={state.related}/>
            </div>
            <div>
              <h3>System Signals</h3><HealthSummary candidates={diagnosis}/><p className="xlt-muted">Saved diagnosis signals, not collector-UP health.</p></div>
          </section>
          <div className="xlt-classic-overview"><ClusterSummary model={model} context={context}/></div>
          <PolicyLifecycle events={events} context={context} catalog={state.catalog}/>
          <details className="xlt-completed-detail">
            <summary>Completed Steps · choose another investigation</summary>{" "}
            <section>
              <div className="xlt-section">
                <h3>Find a slow Step</h3>
                <span>Completed observations · select a Step to compare</span>
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
        <div className="xlt-analyze-layout">
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
            <section className="xlt-phase-matrix-section">
              <div className="xlt-section">
                <h3>Phase × Subsystem</h3>
                <button onClick={() => navigate("investigate")}>
                  Compare baseline & candidates →
                </button>
              </div>
              <p className="xlt-muted">
                Observations during measured phases. Sampled means and shared rolling windows are context, not phase resource consumption.
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
          {state.uiVersion==='workspace'&&<ReferenceStepContext selected={selected} context={context} clock={correlationClock}/>}
          <Pressure model={model} selected={selected} spans={spans} spanData={spanData} />
          <section className="xlt-analysis-signals"><h3>Subsystem Signals</h3><HealthSummary candidates={diagnosis}/></section><div className="xlt-analysis-extra"><TopChanges rows={current}/></div><div className="xlt-analysis-extra"><WorkerOutliers model={model} selected={selected}/></div>{state.uiVersion==='workspace'&&selected&&<div className="xlt-reference-workers"><WorkerComparison model={model} selected={selected} spans={spans}/></div>}<section className="xlt-analysis-related"><h3>Related Metrics</h3>{state.uiVersion==='workspace'?<div className="xlt-four-charts">{state.analysisRelated?.map((panel,i)=><Native key={i} panel={panel}/>)}</div>:<RelatedTabs panels={state.analysisRelated?.filter(Boolean) as VizPanel[]||[]}/>}</section>
        </div>
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
            <div className="xlt-investigation-grid">
              <section className="xlt-investigation-section">
                <div className="xlt-section">
                  <h3>What changed?</h3>
                  <Link to="summary" context={context} catalog={state.catalog}>
                    Full Bottleneck Summary
                  </Link>
                </div>
                <p className="xlt-muted">
                  Saved {boundary.label} window · workload comparability:{" "}
                  {scalar(
                    matching(summaries)[0]?.workload_comparability,
                    "unverified",
                  )}
                  . Units / statistic / entity missing from the projection stay
                  unknown.
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
                            <td title={scalar(r.signal)}>{r.signal==='step_duration_seconds'?boundary.timeLabel:state.uiVersion==='workspace'?signalTitle(scalar(r.signal)):scalar(r.signal)}</td>
                            <td data-label="Current">{format(r.current, scalar(r.unit, ""))}</td>
                            <td data-label="Baseline">{format(r.baseline, scalar(r.unit, ""))}</td>
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
                    No comparable baseline projection in this interval. No
                    comparison is inferred.
                  </p>
                )}
              </section>
              <p className="xlt-investigation-actions">
                <button onClick={() => navigate("analyze")}>
                  Open Phase × Subsystem →
                </button>{" "}
                <button onClick={() => navigate("timeline")}>
                  Inspect measured timeline →
                </button>
              </p>
              <section className="xlt-investigation-candidates">
                <h3>Bottleneck Candidates</h3>
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
                        <p>{scalar(c.summary)}</p>
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
                          {scalar(c.observation_scope)} · confidence is not a
                          probability of causality
                        </p>
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
                    No saved candidate. A missing diagnosis is not a healthy
                    verdict.
                  </p>
                )}
              </section>
              <section className="xlt-investigation-timeline"><h3>Related Cross-Layer Timeline</h3>{state.uiVersion==='workspace'?<ReferenceTimeline selected={selected} spans={spans} context={context}/>:<Native panel={state.timeline}/>}<p className="xlt-muted">Measured spans / approximate Step boundaries; overlap is not resource ownership.</p>{state.uiVersion==='workspace'&&<div className="xlt-investigation-related"><h4>Related Metrics · same interval</h4>{state.related.slice(0,3).map((panel,i)=><Native key={i} panel={panel}/>)}</div>}</section>
              <section className="xlt-investigation-proof"><h3>Evidence Summary</h3>{(['supporting','counter','missing'] as const).map(type=><details key={type} open={state.uiVersion!=='workspace'&&type==='supporting'}><summary>{type==='counter'?'Counter':type==='missing'?'Missing':'Supporting'} ({proofs.filter(row=>row.evidence_type===type).length})</summary>{proofs.filter(row=>row.evidence_type===type).map((row,index)=><p key={index}><b>{scalar(row.signal)}</b><small>{scalar(row.observation_scope)} · {scalar(row.reason,row.detail?scalar(row.detail):'See candidate evidence')}</small></p>)}</details>)}{!proofs.length&&<p className="xlt-empty">No saved evidence. No causal conclusion is inferred.</p>}</section>
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
                  Native dashboard · current Run / Step / time context
                </p>
              </article>
            ))}
          </div>
        </details>
      )}
      {state.page === "deep-dive" && (
        <><DeepWorkspace model={model} summary={comparisonMeta} candidate={workspaceCandidate} evidence={proofs} panels={state.detailPanels} context={context} catalog={state.catalog}/><section className="xlt-phase-correlation"><h3>Phase Correlation · measured intervals</h3>{state.uiVersion==='workspace'?<ReferenceTimeline selected={selected} spans={spans} context={context}/>:<Native panel={state.timeline}/>}<p className="xlt-muted">Execution intervals share the selected Step time range. Overlap does not establish phase resource ownership.</p></section><Pressure model={model} selected={selected} spans={spans} spanData={spanData} /><section className="xlt-related-dashboards"><h3>Existing subsystem dashboards</h3><div className="xlt-actions">{(['compute','storage','stage','timeline','logs'] as Destination[]).map(to=><Link key={to} to={to} context={context} catalog={state.catalog}>{to} ↗</Link>)}</div></section></>
      )}
      {state.page==='infrastructure'&&<Infrastructure model={model} context={context}/>}
      {state.page==='logs'&&<LogsWorkspace model={model} context={context} events={events} steps={steps} spans={spans}/>}
      {state.selectedCell && (
        <div className="xlt-evidence-layout">
          <div>
            <Native panel={state.timeline} />
            <p className="xlt-muted">
              Measured span boundaries in this interval; resource signals remain
              sampled.
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
        Correlation ≠ attribution ≠ causality. No data ≠ measured zero.{" "}
      </footer>
    </div>
    </div>
  );
}
function WorkspaceNavigation({page,context}:{page:Page;context:Context}){
 const labels={overview:'Run Overview',analyze:'Analyze',investigate:'Investigate','deep-dive':'Deep Dive',infrastructure:'Infrastructure',logs:'Logs'};
 const descriptions={overview:'실행 현황 및 인프라',analyze:'성능 분석',investigate:'상세 분석','deep-dive':'스토리지 심층 분석',infrastructure:'클러스터 인프라',logs:'로그 검색'};
 return <aside className="xlt-workspace-nav"><a className="xlt-workspace-brand" href={appLink('overview',context)}><WorkspaceMark/><span><em>XLayer</em> Telemetry<small>Observe · Understand · Scale Agent RL</small></span></a><nav aria-label="Workspace pages">{PAGES.map(p=><a key={p} href={appLink(p,context)} aria-current={page===p?'page':undefined}><WorkspaceIcon name={WORKSPACE_COPY[p].icon}/><span>{labels[p]}<small>{descriptions[p]}</small></span></a>)}</nav><div className="xlt-workspace-version"><b>Better Agents<br/>Through Better Data</b><span>XLayer Telemetry</span><a href={appLink(page,switchVersion(context,'classic'))}>V1 Classic</a></div></aside>;
}
function ReferenceMetric({panel}:{panel?:VizPanel}){
 const data=useData(panel?.state.$data),values=latestEntitySamples(samples(data));
 const state=data?.state===LoadingState.Error?'Query error':data?.state===LoadingState.Loading?'Loading…':!values.length?'No data':values.length>1?`${values.length} series`:format(values[0].value,panel?.state.fieldConfig?.defaults?.unit||values[0].unit||'');
 return <><div className="xlt-reference-metric-value"><strong>{state}</strong><small>{values.length===1?'Range-end query · sampled / rolling':'Multiple source series · no aggregation'}</small></div><Native panel={panel}/></>;
}
function ReferenceTimeline({selected,spans,context,compact=false}:{selected?:RecordRow;spans:RecordRow[];context:Context;compact?:boolean}){
 const start=numeric(selected?.window_start_ms),end=numeric(selected?.window_end_ms);
 if(!selected||start===undefined||end===undefined||end<=start)return <p className="xlt-empty">Select a completed observation for an execution-linked timeline.</p>;
 const mapped=timelineCalls(spans,selected);
 const phases=[...new Set(mapped.map(row=>String(row.span.phase)))].slice(0,10);
 if(!phases.length)return <p className="xlt-empty">No clock-qualified measured intervals. Native detailed Timeline retains raw/approximate observations.</p>;
 const width=compact?1000:650,left=compact?0:110,rows=compact?1:phases.length,height=compact?48:rows*24+25;
 return <div className="xlt-reference-timeline"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Clock-qualified measured call intervals; workers remain distinct">
 {!compact&&phases.map((phase,index)=><g key={phase}><text x="0" y={index*24+16}>{phase.replace(/_/g,' ')}</text><path className="xlt-timeline-grid" d={`M${left} ${index*24+23}H${width}`}/></g>)}
 {mapped.map(({span,window},index)=>{const phase=String(span.phase),row=phases.indexOf(phase);if(row<0||window.start===undefined||window.end===undefined)return null;const peers=mapped.filter(item=>item.span.phase===phase),lane=peers.indexOf(mapped[index]);const x=left+(window.start-start)/(end-start)*(width-left),right=left+(window.end-start)/(end-start)*(width-left),y=compact?3+index*2:row*24+3+lane*Math.min(4,16/peers.length);return <a key={index} href={appLink('timeline',{...context,variables:{...context.variables,trace_id:span.trace_id?[String(span.trace_id)]:context.variables.trace_id}})}><rect x={x} y={y} width={Math.max(2,right-x)} height={compact?1.5:Math.max(2,Math.min(9,16/peers.length))} rx="1.5" fill={PHASE_COLORS[phase as keyof typeof PHASE_COLORS]||'#00c8ef'}/><title>{scalar(span.name)} · {scalar(span.node)} / {scalar(span.worker_id)} · {window.accuracy} · {format((window.end-window.start)/1000,'s')} · uncertainty {format(window.uncertainty,'s')}</title></a>;})}
 <text x={left} y={height-3}>{new Date(start).toLocaleTimeString()}</text><text textAnchor="end" x={width-2} y={height-3}>{new Date(end).toLocaleTimeString()}</text>
 </svg><small>{mapped.length} linked calls · separate worker tracks · exact/calibrated boundaries, not phase resource ownership</small></div>;
}

function ReferenceNodeFacts({node,catalog,context}:{node?:ResourceNode;catalog:Catalog;context:Context}){
 return <section className="xlt-reference-resource"><div className="xlt-section"><h3><WorkspaceIcon name="user"/>Selected Resource</h3><a href={appLink('infrastructure',context)}>상세 정보 →</a></div>{node?<><h4><WorkspaceIcon name={node.kind==='compute'?'cpu':'database'}/>{node.component}<span className="xlt-badge">{node.status.replace(/_/g,' ')}</span></h4><dl><dt>Cluster / kind</dt><dd>{node.cluster} · {node.kind}</dd><dt>Role</dt><dd>{node.role} · configured</dd><dt>Resource owner</dt><dd>{node.resourceNode||'Unknown mapping'}</dd><dt>Device / GPU</dt><dd>{node.device||node.gpu||'Not reported'}</dd><dt>Exporter</dt><dd>{node.targets.join(', ')||'Not registered'}</dd></dl>{node.resourceNode&&<Link to={node.kind==='storage'?'storage':'compute'} context={selectInfrastructure(context,node)} catalog={catalog}>Resource metrics →</Link>}</>:<p className="xlt-empty">Topology에서 resource를 선택하세요. Owner·hardware 정보는 추정하지 않습니다.</p>}<small>Configured identity / exporter observation, not health.</small></section>;
}
function ReferenceOverview({model,context,selected,events,steps,spans}:{model:Shell;context:Context;selected?:RecordRow;events:RecordRow[];steps:RecordRow[];spans:RecordRow[]}){
 const state=model.useState(),components=useData(state.components),edges=useData(state.relationships),availability=useData(state.availability),range=sceneGraph.getTimeRange(model).useState();
 const topology=infrastructureModel(samples(components),samples(edges),samples(availability),range.value.to.valueOf());
 const hosts=topology.nodes.filter(node=>!node.device&&node.gpu===undefined),compute=hosts.filter(node=>node.kind==='compute'&&!/network|fabric/i.test(node.role)),storage=hosts.filter(node=>node.kind==='storage'&&!/network|fabric/i.test(node.role)),network=hosts.filter(node=>/network|fabric/i.test(node.role));
 const resource=topology.nodes.find(node=>node.key===context.variables.infra_component?.[0]);
 const tiles=[{label:'GPU / Compute Cluster',icon:'cpu',rows:compute},{label:'Storage Cluster',icon:'database',rows:storage},{label:'Network Inventory',icon:'topology',rows:network}];
 return <div className="xlt-reference-overview"><div className="xlt-overview-clusters">{tiles.map(tile=>{const observed=tile.rows.filter(row=>row.status==='observed').length;return <section key={tile.label}><WorkspaceIcon name={tile.icon} size={34}/><div><h4>{tile.label}</h4><strong>{components?.state===LoadingState.Error||availability?.state===LoadingState.Error?'Query error':tile.rows.length?`${observed} / ${tile.rows.length}`:'No inventory'}</strong><small>Exporter observed / configured</small><div className="xlt-coverage-meter"><i style={{width:`${tile.rows.length?observed/tile.rows.length*100:0}%`}}/></div></div></section>;})}<section><WorkspaceIcon name="pulse" size={34}/><div><h4>Completed observations</h4><strong>{steps.length||'No data'}</strong><small>Returned Step history · not active runs</small><a href={appLink('analyze',context)}>Analyze {selected?`Step ${scalar(selected.step)}`:'a Step'} →</a></div></section></div>
 <section className="xlt-overview-topology"><div className="xlt-section"><h3><WorkspaceIcon name="topology"/>Cluster Topology</h3><a href={appLink('infrastructure',context)}>클러스터 구성 →</a></div><DataStatus provider={state.components}/><DataStatus provider={state.availability}/><TopologyView variant="workspace" nodes={topology.nodes} edges={topology.edges} selected={resource?.key} onSelect={node=>locationService.push(appLink('overview',selectInfrastructure(context,node)))}/></section>
 <div className="xlt-overview-inspector"><ReferenceNodeFacts node={resource} catalog={state.catalog} context={context}/><section><h3><WorkspaceIcon name="logs"/>Recent Events</h3><DataStatus provider={state.events}/><RecentEvents rows={events} steps={steps} spans={spans} context={context} catalog={state.catalog}/></section><section className="xlt-next-actions"><h3><WorkspaceIcon name="arrow"/>Next Investigation</h3>{(['analyze','investigate','deep-dive','logs'] as const).map((page,i)=><a key={page} href={appLink(page,context)}><b>{i+1}</b><span>{WORKSPACE_COPY[page].title}<small>{WORKSPACE_COPY[page].description}</small></span><WorkspaceIcon name="arrow" size={16}/></a>)}</section></div>
 <section className="xlt-overview-run-timeline"><div className="xlt-section"><h3><WorkspaceIcon name="topology"/>Run Timeline</h3><a href={appLink('timeline',context)}>정밀 timeline →</a></div><ReferenceTimeline selected={selected} spans={spans} context={context} compact/><small>Exact / calibrated spans. Approximate Step and unlinked events remain separate.</small></section>
 <div className="xlt-overview-metric-strip">{state.related.slice(0,4).map((panel,index)=><section key={index}><h3><WorkspaceIcon name={['cpu','database','database','topology'][index]}/>{['GPU observations','vLLM queue','KV hit / reuse','Storage connector'][index]}</h3><ReferenceMetric panel={panel}/></section>)}</div>
 </div>;
}
function ReferenceStepContext({selected,context,clock}:{selected?:RecordRow;context:Context;clock:string}){
 return <section className="xlt-selected-step-context"><h3><WorkspaceIcon name="logs"/>Selected Step Context</h3><dl><dt>Run / Step</dt><dd>{scalar(selected?.run_id,context.variables.run_id?.join(', '))} / {scalar(selected?.step)}</dd><dt>Boundary</dt><dd>{boundaryPresentation(selected).label} · {scalar(selected?.boundary_scope)}</dd><dt>Node / worker</dt><dd>{scalar(selected?.node)} / {scalar(selected?.worker_id)}</dd><dt>Record</dt><dd title={scalar(selected?.record_id)}>{scalar(selected?.record_id)}</dd><dt>Policy</dt><dd>{scalar(selected?.policy_version,'Not reported')} · trainer report</dd><dt>Clock quality</dt><dd>{clock.replace(/_/g,' ')}</dd></dl><small>Explicit execution identity; overlap does not establish resource ownership.</small></section>;
}

function ClusterSummary({model,context}:{model:Shell;context:Context}){
 const state=model.useState(),components=useData(state.components),relationships=useData(state.relationships),availability=useData(state.availability);
 const range=sceneGraph.getTimeRange(model).useState();
 const topology=infrastructureModel(samples(components),samples(relationships),samples(availability),range.value.to.valueOf());
 return <section className="xlt-cluster-summary"><div className="xlt-section"><h3>Cluster Summary</h3><a href={appLink('infrastructure',context)}>Open Infrastructure →</a></div><div className="xlt-health-grid">{[['Configured components',topology.configuredNodes],['Exporter-observed resources',topology.observedNodes],['Unknown mapping / source',topology.unknownNodes]].map(([label,count])=><article className="xlt-card" key={String(label)}><small>{label}</small><b>{components?.state===LoadingState.Error||availability?.state===LoadingState.Error?'Query failed':components?.state===LoadingState.Loading||availability?.state===LoadingState.Loading?'Loading':topology.nodes.length?count:'No data'}</b></article>)}</div><p className="xlt-muted">Configured inventory and sampled exporter reachability · no cluster-health verdict or observed path.</p></section>;
}
function Infrastructure({model,context}:{model:Shell;context:Context}){
 const state=model.useState(),componentData=useData(state.components),edgeData=useData(state.relationships),availabilityData=useData(state.availability);
 const gpuData=useData(state.resourceGpus),diskData=useData(state.resourceDevices);
 const range=sceneGraph.getTimeRange(model).useState(),topology=infrastructureModel(samples(componentData),samples(edgeData),samples(availabilityData),range.value.to.valueOf(),30000,[...samples(gpuData),...samples(diskData)]);
 const [search,setSearch]=useState(''),[tab,setTab]=useState('compute'),[inventoryMode,setInventoryMode]=useState('hosts'),[limit,setLimit]=useState(12),[detailOpen,setDetailOpen]=useState(false);
 const selected=topology.nodes.find(node=>node.key===context.variables.infra_component?.[0]);
 useEffect(()=>{if(selected)setTab(selected.kind==='storage'?'storage':/network|fabric/i.test(selected.role)?'network':'compute');},[selected?.key]);
 const select=(node:ResourceNode)=>locationService.push(appLink('infrastructure',selectInfrastructure(context,node)));
 const nodes=topology.nodes.filter(node=>(inventoryMode==='all'||!node.device&&node.gpu===undefined)&&`${node.component} ${node.role} ${node.resourceNode||''}`.toLowerCase().includes(search.toLowerCase()));
 const visible=INFRASTRUCTURE_PANELS.map((spec,index)=>({spec,panel:state.infraPanels?.[index]})).filter(({spec})=>tab==='storage'?spec.dashboard==='storage':tab==='network'?spec.dashboard==='compute'&&[8,9,42].includes(spec.panel):spec.dashboard==='compute'&&![8,9,42].includes(spec.panel));
 return <><div className="xlt-infrastructure-top"><section><div className="xlt-section"><h3>Cluster Topology</h3><span className="xlt-badge">Configured / Observed / Unknown</span></div><DataStatus provider={state.components}/><DataStatus provider={state.availability}/><TopologyView variant={state.uiVersion==='workspace'?'workspace':undefined} nodes={topology.nodes} edges={topology.edges} selected={selected?.key} onSelect={select}/></section><section className="xlt-resource-detail"><h3>Selected Resource</h3>{selected?<><h4>{selected.component}</h4><dl><dt>Role</dt><dd>{selected.role} · configured</dd><dt>Resource node</dt><dd>{selected.resourceNode||'Unknown mapping'}</dd><dt>Observation</dt><dd>{selected.status.replace(/_/g,' ')} · exporter, not health</dd><dt>Device / GPU</dt><dd>{selected.device||selected.gpu||'Not reported'}</dd><dt>Targets</dt><dd>{selected.targets.join(', ')||'Not registered'}</dd></dl>{selected.issues.map(issue=><p className="xlt-notice" key={issue}>{issue}</p>)}{selected.resourceNode?<Link to={selected.kind==='storage'?'storage':'compute'} context={context} catalog={state.catalog}>Detailed resource dashboard</Link>:<p className="xlt-notice">Resource metrics require an explicit owner mapping. No node is inferred.</p>}<p><a href={appLink('investigate',context)}>Open Step investigation →</a></p></>:<p className="xlt-empty">Choose a configured component or an observed exporter. Owner mapping is required for resource drill-down.</p>}</section></div>
 <section className="xlt-resource-metrics-section"><div className="xlt-section"><h3>Resource Metrics</h3><div className="xlt-chips">{['compute','network','storage'].map(kind=><button key={kind} aria-pressed={tab===kind} onClick={()=>{setTab(kind);if(state.uiVersion==='workspace')setDetailOpen(true);}}>{kind}</button>)}</div></div><div className="xlt-infrastructure-metrics">{selected&&!selected.resourceNode?<p className="xlt-empty">Resource metrics withheld: selected component has no unique owner mapping. Current Run context remains available.</p>:state.uiVersion==='workspace'?state.referencePanels?.map((panel,i)=><Native key={i} panel={panel}/>):visible.map(({spec,panel})=><Native key={`${spec.dashboard}/${spec.panel}`} panel={panel}/>)}</div>{state.uiVersion==='workspace'&&<details className="xlt-resource-full-panels" open={detailOpen} onToggle={event=>setDetailOpen(event.currentTarget.open)}><summary>All resource panels · {tab}</summary>{detailOpen&&(!selected||selected.resourceNode)&&<div className="xlt-infrastructure-metrics">{visible.map(({spec,panel})=><Native key={`${spec.dashboard}/${spec.panel}`} panel={panel}/>)}</div>}</details>}<p className="xlt-notice">Storage DS/MDS inventory is separate from GPU-host sandbox local I/O. Device means, service distributions and connector RPC p95 are distinct statistics. 3FS evidence is implemented in Deep Dive; pNFS is TBD.</p></section>
 <section className="xlt-component-inventory"><div className="xlt-section"><h3>Component Inventory</h3><select aria-label="Inventory resource kind" value={inventoryMode} onChange={e=>{setInventoryMode(e.target.value);setLimit(12);}}><option value="hosts">Hosts / services</option><option value="all">All devices / components</option></select><input aria-label="Search component inventory" placeholder="Node, role or device" value={search} onChange={e=>setSearch(e.target.value)}/></div><div className="xlt-scroll"><table><thead><tr><th>Component</th><th>Role</th><th>Resource owner</th><th>Source / scope</th><th>Observation</th><th>Next</th></tr></thead><tbody>{nodes.slice(0,limit).map(node=><tr key={node.key}><td>{node.component}<small>{node.cluster} · {node.kind}</small></td><td>{node.role}</td><td>{node.resourceNode||'Unknown'}<small>{node.device?`Device ${node.device}`:node.gpu!==undefined?`GPU ${node.gpu}`:''}</small></td><td>{node.configured?'Configured inventory':'Registered exporter'}<small>Node/device · shared resources</small></td><td>{node.status.replace(/_/g,' ')}</td><td><button onClick={()=>select(node)}>Inspect resource</button></td></tr>)}</tbody></table></div>{!nodes.length&&<p className="xlt-empty">No matching configured or observed resources. Missing is not measured zero.</p>}<p className="xlt-muted">{nodes.length} matching resources · {Math.min(limit,nodes.length)} shown. No observed service/resource edges are currently instrumented.</p>{nodes.length>limit&&<button onClick={()=>setLimit(Math.min(limit+20,200))}>Show more resources</button>}</section>
 <details><summary>Configured relationship inventory · {topology.edges.length} edges</summary><DataStatus provider={state.relationships}/><div className="xlt-scroll"><table><thead><tr><th>Source</th><th>Destination</th><th>Relation</th><th>Evidence</th></tr></thead><tbody>{topology.edges.slice(0,200).map(edge=><tr key={edge.key}><td>{edge.source}</td><td>{edge.destination}</td><td>{edge.relation}</td><td>Configured · operation relationship not observed</td></tr>)}</tbody></table></div></details></>;
}
function LogsWorkspace({model,context,events,steps,spans}:{model:Shell;context:Context;events:RecordRow[];steps:RecordRow[];spans:RecordRow[]}){
 const state=model.useState(),[selected,setSelected]=useState<RecordRow>(),[approximateOpen,setApproximateOpen]=useState(false);
 useEffect(()=>setSelected(undefined),[investigationKey(context)]);
 const search=(sceneGraph.lookupVariable('event_search',model) as TextBoxVariable),searchState=search.useState();
 const needle=String(searchState.value||'').toLowerCase(),filtered=events.filter(event=>`${event.name||''} ${event.phase||''} ${JSON.stringify(event.attributes||{})}`.toLowerCase().includes(needle));
 const step=selected?resolveEventStep(selected,steps,spans):undefined;
 return <>{state.uiVersion==='workspace'&&<section className="xlt-logs-context-ribbon"><WorkspaceIcon name="topology"/><span>Cluster<b>{context.variables.cluster?.join(', ')||'Not selected'}</b></span><WorkspaceIcon name="pulse"/><span>Run<b>{context.variables.run_id?.join(', ')||'Not selected'}</b></span><WorkspaceIcon name="database"/><span>Step<b>{scalar(steps.find(row=>row.record_id===context.variables.record_id?.[0])?.step,'Not selected')}</b></span><WorkspaceIcon name="cpu"/><span>Resource<b>{context.variables.node?.join(', ')||'Not selected'}</b></span><small>Same native context · no independent filters</small></section>}<div className="xlt-logs-workspace"><section className="xlt-log-filter-rail"><h3>Log / Event Filters</h3><div className="xlt-log-filters">{['workload','log_run_id','node','log_search','log_severity','event_search'].map(name=>{const variable=sceneGraph.lookupVariable(name,model);return variable?<VariableValueSelectWrapper key={name} variable={variable} showAlways/>:null;})}</div><p className="xlt-muted">Log directory and telemetry Run are independent. Text severity is a literal raw-log filter, not inferred structured severity. Event search filters returned records only.</p></section>
 <div className="xlt-logs-grid"><section><div className="xlt-section"><h3>Raw Logs</h3><Link to="logs" context={context} catalog={state.catalog}>Full Run Logs</Link></div>{state.logPanels?.[0]?<Native panel={state.logPanels[0]}/>:<p className="xlt-empty">Loki / Run Logs not provisioned. No log data is not an error count of zero.</p>}</section><section className="xlt-event-detail"><h3>Event Details</h3>{selected?<><b>{scalar(selected.name)}</b>{state.uiVersion==='workspace'?<><dl>{[['Event',selected.name],['Producer',selected.producer],['Node / worker',`${scalar(selected.node)} / ${scalar(selected.worker_id)}`],['Run / Step',`${scalar(selected.run_id)} / ${scalar(selected.step)}`],['Boundary',selected.boundary_accuracy],['Clock reference',selected.clock_reference],['Trace',selected.trace_id],['Event time',eventTime(selected)!==undefined?new Date(eventTime(selected)!).toISOString():'Unknown']].map(([label,value])=><React.Fragment key={String(label)}><dt>{String(label)}</dt><dd>{scalar(value,'Not reported')}</dd></React.Fragment>)}</dl><details open><summary>Original record / attributes</summary><pre>{JSON.stringify(selected,null,2)}</pre></details></>:<pre>{JSON.stringify(selected,null,2)}</pre>}{step?.state==='matched'?<a href={appLink('investigate',selectStep(step.step!,context))}>Investigate matching Step →</a>:<p className="xlt-notice">No unique execution-linked Step. Timestamp coincidence does not establish ownership.</p>}</>:<p className="xlt-empty">Select an actual returned event to inspect its source, Run/Step and trace fields.</p>}</section></div>
 <section className="xlt-event-list"><h3>Events · returned records</h3><DataStatus provider={state.events}/><div className="xlt-scroll"><table><thead><tr><th>Time</th><th>Event</th><th>Phase</th><th>Node / worker</th><th>Run / Step</th><th>Next</th></tr></thead><tbody>{filtered.slice(-100).reverse().map((event,i)=><tr key={i}><td>{eventTime(event)!==undefined?new Date(eventTime(event)!).toLocaleTimeString():'Unknown source time'}</td><td>{scalar(event.name)}</td><td>{scalar(event.phase)}</td><td>{scalar(event.node)} / {scalar(event.worker_id)}</td><td>{scalar(event.run_id)} / {scalar(event.step)}</td><td><button onClick={()=>setSelected(event)}>Inspect event</button></td></tr>)}</tbody></table></div>{!filtered.length&&<p className="xlt-empty">No matching event records in the selected range. Uncollected events are not invented.</p>}<p className="xlt-muted">First/last returned query records only · no global severity total or synthetic event-volume histogram.</p></section><section className="xlt-log-timeline"><h3>Related Agent RL Timeline</h3>{state.uiVersion==='workspace'?<ReferenceTimeline selected={steps.find(step=>step.record_id===context.variables.record_id?.[0])} spans={spans} context={context}/>:<Native panel={state.logPanels?.[2]}/>}<details onToggle={event=>setApproximateOpen(event.currentTarget.open)}><summary>Approximate Step intervals</summary>{approximateOpen&&<Native panel={state.logPanels?.[3]}/>}</details><p className="xlt-muted">Exact/calibrated spans and approximate Step intervals retain their precision and ownership boundaries.</p><a href={appLink('timeline',context)}>Detailed Timeline →</a></section></div></>;
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
  return (
    <div className="xlt-scroll">
      <table>
        <thead>
          <tr>
            <th>Step</th>
            <th>Duration</th>
            <th>Boundary</th>
            <th>Observer / worker</th>
            <th>Next</th>
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
          No completed Step in this range. Select a Run/time range; Loki step
          history is optional.
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
      {(projected||selection.state==='observed')&&trend.length>1&&<div className="xlt-spark" title={projected?'Saved Step observations, not a causal model':'Sampled trend for the displayed entity'}>
        <Sparkline theme={theme} width={110} height={25} sparkline={{
          x:{name:'Time',type:FieldType.time,values:trend.map(p=>p.time),config:{}},
          y:{name:spec.name,type:FieldType.number,values:trend.map(p=>p.value),config:{color:{mode:'fixed',fixedColor:theme.isDark?'#00c8ef':'#4566d5'}},state:{range:{min:Math.min(...trend.map(p=>p.value)),max:Math.max(...trend.map(p=>p.value)),delta:Math.max(...trend.map(p=>p.value))-Math.min(...trend.map(p=>p.value))}}}
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
      {!projected&&reported===undefined&&selection.state!=='observed'&&<small title={selection.reason}>{selection.state==='multiple'?`${selection.entities.length} entities · ${entitySelectionHint(selection.entities.map(s=>s.labels))}`:selection.state==='freshness-unknown'?'Matching age unavailable':selection.state==='stale'?'Producer age exceeds limit':selection.state==='invalid'?'Conflicting source data':'No matching observation'}</small>}
      {reported !== undefined && (
        <small>Selected {boundary.label} · reported duration</small>
      )}
      {projected && !unit && <small>Unit not reported</small>}
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
 const ctx=readContext(window.location.search,versionFromPath(window.location.pathname)),workerKey=ctx.variables.phase_worker?.[0];
 const choices=executionChoices(spans,selected),ownSpans=selectExecution(spans,workerKey),ownBaseline=selectExecution(baselineSpans,workerKey),phases=observedPhases(spans,selected,workerKey);
 const phaseName=(name:string)=>({actor_update:'Training · actor',weight_sync:'Weight Sync',checkpoint_save:'Checkpoint',critic_update:'Training · critic',reference_log_prob:'Reference log prob',reference:'Reference',checkpoint_load:'Checkpoint load',rollout:'Rollout',reward:'Reward'}[name]||name);
 return <><div className="xlt-matrix-toolbar"><div className="xlt-chips"><button aria-pressed={model.state.matrixView!=="workers"} onClick={()=>model.setState({matrixView:'phase'})}>Phase Matrix</button><button aria-pressed={model.state.matrixView==="workers"} onClick={()=>model.setState({matrixView:'workers'})}>Worker Comparison</button></div><label>Execution worker<select aria-label="Execution worker" value={workerKey||''} onChange={event=>{const choice=choices.find(row=>row.key===event.target.value);locationService.push(appLink('analyze',choice?workerContext(ctx,choice.row,choice.key):{...ctx,variables:{...ctx.variables,phase_worker:[]}}));model.setState({matrixSelectionVersion:(model.state.matrixSelectionVersion||0)+1});}}><option value="">Execution path · choose worker if ambiguous</option>{workerKey&&!choices.some(choice=>choice.key===workerKey)&&<option value={workerKey}>Selected worker outside range</option>}{choices.map(choice=><option key={choice.key} value={choice.key}>{scalar(choice.row.node)} / {scalar(choice.row.worker_id)} · {scalar(choice.row.role)} / {scalar(choice.row.producer)}</option>)}</select></label></div>{model.state.matrixView==='workers'?<WorkerComparison model={model} selected={selected} spans={spans}/>:<div className="xlt-scroll"><table className="xlt-matrix"><thead><tr><th>Subsystem</th>{phases.map(phase=><th key={phase} style={{borderTop:`3px solid ${PHASE_COLORS[phase]}`}}>{phaseName(phase)}<small>{phaseWindow(ownSpans,selected,phase).status==='observed'?`${phaseWindow(ownSpans,selected,phase).accuracy} span`:phaseWindow(ownSpans,selected,phase).status==='ambiguous'?'Ambiguous interval':'No comparable interval'}</small></th>)}</tr></thead><tbody>{(model.state.uiVersion==='workspace'?(['gpu','vllm','kv','storage','network','sandbox','ray'] as Array<typeof SUBSYSTEMS[number]>):SUBSYSTEMS).map(name=><MatrixRow key={name} subsystem={name} phases={phases} model={model} selected={selected} spans={ownSpans} evidence={evidence} baselineStep={baselineStep} baselineSpans={ownBaseline} comparability={comparability} clockStatus={clockStatus} baselineClockStatus={baselineClockStatus} clockProof={clockProof}
                baselineClockProof={baselineClockProof}/>)}</tbody></table></div>}{!phases.length&&<p className="xlt-empty">No comparable phase window for this worker. Check Step/span clock reference and uncertainty; call duration remains available in Worker Comparison.</p>}<div className="xlt-matrix-legend"><span>Sampled = query observations</span><span>Shared / Session = context, not ownership</span><span>Rolling = lookback beyond phase</span><span>— = no linked observation</span></div><p className="xlt-muted">Delta compares gauge window means only when declared workload fields, instrumented phase and entity match. Rolling/session values have no phase delta. Actor and critic updates remain separate.</p></>;
}
function MatrixRow({subsystem:s,phases,model,selected,spans,evidence,baselineStep,baselineSpans,comparability,clockStatus,baselineClockStatus,clockProof,baselineClockProof}:{subsystem:string;phases:string[];model:Shell;selected:RecordRow;spans:RecordRow[];evidence:RecordRow[];baselineStep?:RecordRow;baselineSpans:RecordRow[];comparability:string;clockStatus:string;baselineClockStatus:string;clockProof:ClockProof;baselineClockProof:ClockProof}){
 const index=SUBSYSTEMS.indexOf(s as (typeof SUBSYSTEMS)[number]);
 const data=useData(model.state.matrix[index]),baseData=useData(model.state.baselineMatrix?.[index]),spec=MATRIX_SPECS[s];
 const panel=findPanel(model.state.catalog[spec.dashboard],spec.panel),unit=panel?.fieldConfig?.defaults?.unit||spec.unit;
 const values=samples(data).map(p=>({...p,unit})),baseValues=samples(baseData).map(p=>({...p,unit}));
 const context=readContext(window.location.search,versionFromPath(window.location.pathname)),variable=`matrix_${s}_entity` as typeof VARIABLE_NAMES[number],selectedKey=context.variables[variable]?.[0];
 const entities=matrixEntities(values);const activeKey=selectedKey|| (entities.length===1?entities[0].key:undefined);
 const chosen=filterMatrixEntity(values,activeKey),baselineChosen=filterMatrixEntity(baseValues,activeKey),stepCell=stepEvidenceCell(evidence,s);
 const title=({gpu:'GPU',vllm:'vLLM',kv:'KV Cache',ray:'Ray',network:'Network',storage:'Storage',sandbox:'Sandbox'} as Record<string,string>)[s];
 const describe=(labels:Record<string,string>)=>s==='gpu'?`GPU ${labels.gpu||labels.gpu_uuid||'?'} · ${labels.nodename||labels.node||''}`:s==='ray'?`${labels.SessionName||'Session'} · ${labels.State||'State'}`:[labels.operation,labels.status,labels.engine_id||labels.engine,labels.device,labels.port,labels.worker_id].filter(Boolean).join(' · ')||labels.instance||'Observed entity';
 const expr=(data?.series||[]).map(frame=>String(frame.meta?.executedQueryString||''));const lookback=spec.rolling?matrixLookback(expr):0;
 return <tr><th><i className="xlt-subsystem-dot" style={{background:SUBSYSTEM_COLORS[s]}}/>{title}<small>{spec.label}</small>{entities.length>1&&<select className="xlt-entity-select" aria-label={`${title} entity`} value={selectedKey||''} onChange={event=>{locationService.push(appLink(model.state.page,{...context,variables:{...context.variables,[variable]:event.target.value?[event.target.value]:[]}}));model.setState({matrixSelectionVersion:(model.state.matrixSelectionVersion||0)+1});}}><option value="">Choose entity ({entities.length})</option>{selectedKey&&!entities.some(e=>e.key===selectedKey)&&<option value={selectedKey}>Selection outside range</option>}{entities.map(e=><option key={e.key} value={e.key}>{describe(e.labels)}</option>)}</select>}</th>{phases.map(phase=>{
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
  return <td key={phase}><button className="xlt-cell" disabled={data?.state===LoadingState.Loading} aria-busy={data?.state===LoadingState.Loading} aria-label={`${phase} × ${s} evidence`} title={cell.explanation} onClick={()=>{model.setState({selectedCell:{contextKey:investigationKey(context),phase,subsystem:s,cell,window}});setTimeout(()=>document.querySelector('.xlt-evidence')?.scrollIntoView({behavior:'smooth',block:'start'}),0);}}><b>{label}</b>{comparison.comparable&&<span className={`xlt-matrix-delta ${comparison.delta===0?"xlt-delta-flat":comparison.delta&&comparison.delta>0?"xlt-delta-up":"xlt-delta-down"}`} title={comparison.reason}>{comparison.delta===undefined?'Δ unavailable · baseline 0':comparison.delta===0?(model.state.uiVersion==='workspace'?'Δ 0%':'No change vs baseline'):`${comparison.delta>0?'↑ +':'↓ '}${Math.abs(comparison.delta).toFixed(1)}%${model.state.uiVersion==='workspace'?'':' vs baseline'}`}</span>}<small>{observed?quality:window.status==='ambiguous'?'Ambiguous span':s==='sandbox'?'No linked worker call':cell.scope}</small>{!spec.rolling&&s!=='ray'&&observed&&<small>{cell.observations} query observations · mean</small>}{s==='sandbox'&&window.span!==parent.span&&<small>Linked tool call</small>}{phase==='rollout'&&!!stepCell.evidence?.length&&<span className="xlt-step-evidence">Step evidence →</span>}</button></td>;
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
      <p>{cell.explanation}</p>
      <p className="xlt-muted">
        Phase boundary: {window.status}
        {window.accuracy
          ? ` · ${window.accuracy} · ${window.reference} · uncertainty ${format(window.uncertainty, "s")}`
          : ""}
        . MFU, phase-specific p99 and phase baselines are not inferred.
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
            <p className="xlt-muted">Not reported in the saved projection.</p>
          )}
        </div>
      ))}
      <p className="xlt-notice">
        Shared / node-wide evidence is time correlation. Per-run ownership and a
        causal path are not established.
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
      <span className="xlt-muted">Observed span records</span>
      <small>
        Status coverage {status.observed}/{status.total} · limit 5,000
        {status.limited ? " reached" : ""}
      </small>
      <small title="Returned records do not establish the full workload error rate">
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
                  <small className="xlt-muted" title={resolution.reason}>{resolution.state==='matched'?`Linked · ${scalar(resolution.step?.worker_id)}`:resolution.state==='ambiguous'?`${resolution.candidates.length} candidates`:'Step link unavailable'}</small>
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
          No EventRecorder events in this interval. Counter changes are not
          event records.
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
        <span>Range-end observations · no cause verdict</span>
      </div>
      <div className="xlt-pressure-grid">
        {PRESSURE_SPECS.map((spec, index) => (
          <PressureCard
            key={spec.name}
            spec={spec}
            provider={model.state.pressure[index]}
            context={readContext(window.location.search,versionFromPath(window.location.pathname))}
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
        · known status {errors.observed}/{errors.total} · 5,000 record query
        limit. Missing records are not zero errors.
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
        ranked.slice(0,context.uiVersion==='workspace'?1:2).map((row, i) => {const s=row.sample;return (
          <div className="xlt-pressure-value" key={i}>
            <strong>{format(s.value * (spec.scale || 1), spec.unit)}</strong>
            <small className="xlt-entity" title={JSON.stringify(s.labels)}>{compactEntity(s.labels)}</small>
            <small>{row.supporting?'Explicit Step evidence match':spec.name==='GPU utilization'?'Utilization range · no fault verdict':spec.name==='Ray task states'?'State priority · no aggregation':'Highest observed signal · not cause'}</small>
            <Link to={spec.dashboard} context={resourceContext(context,{...s.labels,...(spec.name==='vLLM waiting'&&s.labels.instance?{engine:s.labels.instance}:{})})} catalog={catalog}>Inspect entity →</Link>
          </div>
        );})
      )}
      {entities.length > (context.uiVersion==='workspace'?1:2) && (
        <small>+{entities.length - (context.uiVersion==='workspace'?1:2)} other entities · open details</small>
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
      readContext(window.location.search,versionFromPath(window.location.pathname)).variables.training_max_age?.[0] ||
        300,
    ),
  );
  return (
    <section>
      <h3>Outlier worker snapshots</h3>
      <p className="xlt-muted">
        Same Run / producer / role / phase / node / reported step cohort. Peer
        difference is an investigation candidate; workload comparability remains
        unverified. Snapshot step may differ from selected Step{" "}
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
            No worker-level rollout observations. Driver phase totals are not
            worker outliers.
          </p>
        )}
      </div>
    </section>
  );
}

function HealthSummary({candidates}:{candidates:RecordRow[]}) {
  const definitions=[['GPU','compute'],['vLLM','rollout'],['Storage','storage'],['Network','communication'],['Ray','ray'],['Sandbox','sandbox']];
  return <div className="xlt-health-grid">{definitions.map(([name,component])=>{const rows=candidates.filter(c=>c.component===component),strong=rows.some(c=>c.state==='strong_signal');return <article className="xlt-card" key={name}><b>{name}</b><span className={strong?'xlt-attention':'xlt-muted'}>{strong?'Strong signal':rows.some(c=>c.state==='supporting_signal')?'Supporting signal':rows.length?'Weak signal':'Not assessed'}</span><small>{rows.length?'Saved Step candidate':'No subsystem verdict'}</small></article>;})}</div>;
}
function TopChanges({rows}:{rows:RecordRow[]}) {
  return <section><h3>Top Changes · Step evidence</h3><p className="xlt-muted">Saved comparison window, not a phase resource attribution. Workload comparability remains source-defined.</p><div className="xlt-scroll"><table><thead><tr><th>Signal</th><th>Current</th><th>Baseline</th><th>Delta</th><th>Scope</th></tr></thead><tbody>{[...rows].sort((a,b)=>Math.abs(Number(b.delta_percent)||0)-Math.abs(Number(a.delta_percent)||0)).slice(0,4).map((r,i)=><tr key={i}><td>{scalar(r.signal)}</td><td>{format(r.current,scalar(r.unit,''))}</td><td>{format(r.baseline,scalar(r.unit,''))}</td><td><Delta row={r}/></td><td>{scalar(r.observation_scope)}</td></tr>)}</tbody></table></div></section>;
}
function CommonStorageOverview({model,summary,context,onThreeFS}:{model:Shell;summary?:RecordRow;context:Context;onThreeFS:()=>void}){
 const state=model.useState(),value=parseStorageOverview(summary?.storage_overview);
 const groups=[['connector','Connector RPC'],['dfs_client','Mooncake DFS client'],['master_memory','Master memory']] as const;
 return <div className="xlt-common-storage"><div className="xlt-section"><h3>Common Storage Overview</h3><span className="xlt-badge">Backend / adapter not reported</span></div>
 <DataStatus provider={state.summary}/><p className="xlt-muted">Step / Phase → KV operation → Connector / DFS client → backend is an investigation path, not an observed execution dependency.</p>
 {!value?<p className="xlt-empty">No saved common-storage coverage for this selection. Native metric tabs remain available; a configured 3FS source does not identify the workload backend.</p>:<>
 <div className="xlt-health-grid">{groups.map(([layer,title])=>{const entries=value.signals.filter(row=>row.layer===layer),observed=entries.filter(row=>row.current!==null);return <article className="xlt-card" key={layer}><b>{title}</b><span>{observed.length} / {entries.length} reported signals</span><small>{entries.every(row=>row.status==='not_configured')?'Profile not configured':'Shared service · sampled / rolling'}</small></article>;})}</div>
 <details><summary>Source coverage · values, scope, entity and quality</summary><div className="xlt-scroll"><table><thead><tr><th>Signal</th><th>Current / Baseline</th><th>State / Scope</th><th>Entity / Quality</th></tr></thead><tbody>{value.signals.map(row=><tr key={row.signal}><td>{row.signal}<small>{row.unit} · {row.statistic}</small></td><td>{format(row.current,row.unit==='seconds'?'s':row.unit)} / {format(row.baseline,row.unit==='seconds'?'s':row.unit)}</td><td>{row.status.replace(/_/g,' ')}<small>{row.scope}</small></td><td title={JSON.stringify(row.entity)}>{compactEntity(row.entity)}<small>{row.quality_issues.join(', ')||'No additional quality annotations'}</small>{row.entity.node&&<Link to="stage" context={storageSourceContext(context,row)} catalog={state.catalog}>Source metrics</Link>}</td></tr>)}</tbody></table></div></details>
 <p className="xlt-muted">3FS source: {value.threefs.status.replace(/_/g,' ')} · shared-service. Connection to this Mooncake client is not established.</p></>}
 <button onClick={onThreeFS}>3FS Deep Dive →</button><p className="xlt-notice">Missing native metrics may be disabled, idle, unsupported or unavailable. Delivered DFS keys/bytes can overlap checksum failures; rates are not physical IOPS or an operation failure probability.</p>
 </div>;
}
function DeepWorkspace({model,summary,candidate,evidence,panels,context,catalog}:{model:Shell;summary?:RecordRow;candidate?:RecordRow;evidence:RecordRow[];panels:(VizPanel|undefined)[];context:Context;catalog:Catalog}) {
 const[tab,setTab]=useState(()=>detailTabIndex(context.variables.detail_tab?.[0])),proofs=evidence.filter(e=>e.candidate_id===candidate?.candidate_id),spec=DEEP_DIVE_SPECS[tab];
 const[clusterOpen,setClusterOpen]=useState(false),state=model.useState();
 const storageProofs=evidence.filter(e=>String(e.signal||'').startsWith('threefs_'));
 const groups=storageDetailGroups(DEEP_DIVE_SPECS);
 return <section className="xlt-workspace"><details className="xlt-storage-overview-disclosure" open={state.uiVersion!=='workspace'}><summary>Common Storage · source / backend coverage</summary><CommonStorageOverview model={model} summary={summary} context={context} onThreeFS={()=>setTab(detailTabIndex('3FS evidence'))}/></details><details className="xlt-storage-cluster" onToggle={event=>setClusterOpen(event.currentTarget.open)}><summary>Storage Cluster Resources · declared DS/MDS inventory</summary><p className="xlt-notice">Explicit resource-node mappings only. Exporter availability is not node health or an observed service-to-device path. GPU-host sandbox local I/O remains separate from backend DS/MDS resources.</p>{clusterOpen&&<><div className="xlt-storage-cluster-controls">{["storage_system","storage_node"].map(name=>{const variable=sceneGraph.lookupVariable(name,model);return variable?<VariableValueSelectWrapper key={name} variable={variable} showAlways/>:null;})}</div><div className="xlt-storage-cluster-panels">{state.storageCluster?.map((panel,index)=><Native key={index} panel={panel}/>)}</div></>}</details><div className="xlt-workspace-grid"><div className="xlt-key-findings">
  <h3>Key Findings</h3>{candidate?<><span className="xlt-badge xlt-badge-warning">{scalar(candidate.state).replace(/_/g,' ')}</span><p>{scalar(candidate.summary)}</p>
  {proofs.filter(e=>e.evidence_type==='supporting').slice(0,3).map((e,i)=><p key={i}><b>{i+1}. {scalar(e.signal)}</b><br/>{format(e.baseline,scalar(e.unit,''))} → {format(e.current,scalar(e.unit,''))}<br/><small className="xlt-entity" title={scalar(e.entity,'Entity not reported')}>{scalar(e.observation_scope)} · {scalar(e.entity,'Entity not reported')}</small></p>)}
  <details className="xlt-key-missing" open={state.uiVersion!=='workspace'}><summary>Against / Missing evidence</summary>{proofs.filter(e=>e.evidence_type==='missing'||e.evidence_type==='counter').map((e,i)=><p key={i}>{scalar(e.signal)} · {scalar(e.observation_scope)}</p>)}</details></>:<p className="xlt-empty">Choose a candidate in Investigate to keep its supporting, against and missing evidence in this workspace.</p>}
  <p className="xlt-notice">Shared evidence is correlation; per-run ownership and a causal path are not established.</p><Link to="timeline" context={context} catalog={catalog}>Detailed Timeline</Link>
 </div>{state.uiVersion==='workspace'&&<><section className="xlt-diagnosis-target"><h3><WorkspaceIcon name="user"/>진단 대상</h3><dl><dt>Candidate</dt><dd>{scalar(candidate?.component,'Not selected')}</dd><dt>Scope</dt><dd>{scalar(candidate?.observation_scope,'Unknown')}</dd><dt>Run / record</dt><dd title={scalar(summary?.record_id)}>{scalar(summary?.run_id)} / {scalar(summary?.record_id)}</dd><dt>Resource node</dt><dd>{context.variables.node?.join(', ')||'Not selected'}</dd></dl><small>No hardware / IP / uptime is inferred.</small></section><section className="xlt-diagnosis-summary"><h3><WorkspaceIcon name="logs"/>진단 요약</h3><b>{scalar(candidate?.state,'No saved candidate').replace(/_/g,' ')}</b><p>{scalar(candidate?.summary,'Choose an actual saved candidate')}</p><div className="xlt-evidence-counts">{['supporting','counter','missing'].map(type=><span key={type}>{type} <b>{proofs.filter(row=>row.evidence_type===type).length}</b></span>)}</div><small>Supporting evidence is not causal proof.</small></section></>}<div className="xlt-detailed-metrics"><h3>Detailed Metrics</h3>{([['Common storage',groups.common],['Backend-specific · implemented',groups.backend],['Related subsystem context',groups.context]] as const).map(([title,items])=><div className="xlt-detail-tab-group" key={title}><h4>{title}</h4><div className="xlt-chips">{items.map(s=>{const index=detailTabIndex(s.label);return <button key={s.label} aria-pressed={tab===index} onClick={()=>setTab(index)}>{s.label}</button>;})}</div></div>)}
  {spec.label==='3FS evidence'?<div className="xlt-storage-evidence"><StorageCollectionView model={model} context={context}/><details><summary>Saved aggregate evidence</summary><h4>3FS · saved service observations</h4>{storageProofs.length?storageProofs.map((e,i)=><p key={i}><b>{scalar(e.signal)}</b> · {scalar(e.evidence_type)}<br/>{format(e.baseline,scalar(e.unit,''))} → {format(e.current,scalar(e.unit,''))}{!e.unit&&<small>Unit not reported</small>}<small className="xlt-entity" title={scalar(e.entity,'Entity not reported')}>{scalar(e.entity,'Entity not reported')}</small></p>):<p className="xlt-empty">No saved 3FS evidence in this interval. RPC p95 and disk mean cannot replace it.</p>}</details></div>:panels[tab]?<Native panel={panels[tab]}/>:<p className="xlt-empty">Canonical panel unavailable for this source.</p>}
  <p className="xlt-muted">{spec.note}</p><p className="xlt-muted">Connector / DFS client observations are backend independent. 3FS service evidence is an optional separate source; node-device observations remain context without a verified path.</p>
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
    'select-metric':'Choose one literal metric for the current collection-point plot.',
    'no-data':'No finite unambiguous current collection points for this metric. Missing values are not measured zero.',
    'mixed-source':'This metric occurs in multiple source tables. Plot withheld; inspect the original records below.',
    'mixed-unit':'Returned records disagree on source unit. Plot withheld; values are not converted or combined.',
    'multiple-owner':'Multiple owner observations are selected. Choose one completed observation before plotting.'};
  return <section aria-label="3FS collection context">
    <h4>3FS collection context</h4>
    <p className="xlt-notice">Shared-service reports, not phase or Run usage. Source DateTime resolution is 1 second. Collection interval and per-host clock coverage remain source-reported; no interpolation or phase attribution is inferred.</p>
    <DataStatus provider={state.storageSamples}/>
    <label>Storage metric <select className="xlt-storage-metric" aria-label="Storage metric" value={metric||''} onChange={event=>{variable.setState({value:event.target.value});(sceneGraph.lookupVariable('detail_tab',model) as TextBoxVariable).setState({value:'3FS evidence'});}}>
      <option value="">Choose metric</option>
      {metric&&!options.includes(metric)&&<option value={metric}>Selected metric outside returned records</option>}
      {options.map(name=><option key={name} value={name}>{name}</option>)}
    </select></label>
    {selection.state==='ready'&&state.storagePlot?<><p className="xlt-muted">Current collection points · original source timestamp · unit {selection.unit||'not reported (raw)'} · {selection.points.length} returned points. Baseline timestamps remain unchanged in the records table.</p><Native panel={state.storagePlot}/></>:<p className="xlt-empty">{messages[selection.state]||'Collection-point panel unavailable.'}</p>}
    {state.storageSampleTable&&<details open><summary>Original collection records · Current / Baseline</summary><Native panel={state.storageSampleTable}/></details>}
    {state.storageComparison&&<details><summary>Comparable collection windows</summary><p className="xlt-muted">Delta is reported only by the existing diagnosis. Unknown clock/host mapping or collection semantics are not a baseline match.</p><Native panel={state.storageComparison}/></details>}
    <details><summary>Saved source coverage · {statuses.length} status records</summary><DataStatus provider={state.storageStatus}/>{state.storageStatusTable&&<Native panel={state.storageStatusTable}/>}</details>
    {!rows.length&&sampleData?.state===LoadingState.Done&&<p className="xlt-empty">No saved collection points in this interval. The optional source may be unconfigured, failed, empty or unsupported; inspect saved coverage. This is not a healthy verdict.</p>}
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
      <label className="xlt-context-step">{boundaryPresentation(selected).label}<select aria-label="Completed Step" value={selected?.record_id?String(selected.record_id):''} onChange={e=>{const row=choices.find(r=>String(r.record_id)===e.target.value);if(row)onStep(row);}}><option value="">Select completed observation</option>{choices.map(row=><option key={String(row.record_id)} value={String(row.record_id)}>{boundaryPresentation(row).label} {scalar(row.step)} · {format(row.step_duration_seconds,'s')}</option>)}</select></label>
      {model.state.uiVersion==='workspace'&&<div className="xlt-context-variable">{control('node')}</div>}
      <div className="xlt-context-time"><span>Time range</span><div>{model.state.contextControls.map((c,i)=>{const Control=c.Component as React.ComponentType<{model:any}>;return <Control key={i} model={c}/>;})}</div><div className="xlt-visible-range">{dateTimeFormat(rangeState.value.from,{timeZone:range.getTimeZone(),format:'MMM D, HH:mm:ss'})} → {dateTimeFormat(rangeState.value.to,{timeZone:range.getTimeZone(),format:'HH:mm:ss'})}</div></div>
    </div>
    <div className="xlt-context-meta"><span title="Producer-reported trainer version, not worker-applied policy">Policy <b>{scalar(selected?.policy_version,policySamples.length===1?format(policySamples[0].value):'Not reported')}</b> · trainer report</span><span>Wrapped command <b>{activeWorkloads.length===1?scalar(activeWorkloads[0].labels.state):'Not reported'}</b> · latest report</span><details><summary>Observer / Resource</summary><div>{control('source_node')}{control('node')}{control('worker')}{control('role')}</div></details></div>
  </section>;
}

function compactMatrixValue(value:number,unit:string):string{const formatted=getValueFormat(unit)(value,unit==='short'||unit==='percent'||unit==='percentunit'?0:1);return `${formatted.prefix||''}${formatted.text}${formatted.suffix||''}`;}

function WorkerComparison({model,selected,spans}:{model:Shell;selected:RecordRow;spans:RecordRow[]}){
 const data=useData(model.state.matrix[0]),values=samples(data);const rows=measuredWorkers(spans,selected);
 return <section className="xlt-worker-comparison"><h3>Measured Worker Comparison</h3><p className="xlt-muted">One declared execution identity per row. Peer duration requires matching operation, scope and workload fingerprint. GPU is a linked sampled device, not worker consumption.</p><div className="xlt-scroll"><table><thead><tr><th>Worker / node</th><th>Phase coverage</th><th>Rollout call</th><th>Peer median / delta</th><th>GPU</th><th>Next</th></tr></thead><tbody>{rows.map(row=>{
  const gpu=row.window.span?.gpu;const device=gpu===undefined?undefined:gaugeSummary(values.filter(value=>value.labels.gpu===String(gpu)),row.window).sample;
  return <tr key={row.key}><td>{scalar(row.row.worker_id)} · {scalar(row.row.node)}<small>{scalar(row.row.producer)} / {scalar(row.row.role)}</small></td><td>{row.phases} observed phase types · {row.count} spans</td><td>{row.duration!==undefined?`${format(row.duration,'s')}${row.window.status==='observed'?'':' · call only / clock unmapped'}`:row.window.status}</td><td>{row.peers>=3?`${format(row.median,'s')} · ${row.delta===undefined?'Δ unavailable':format(row.delta,'%')}`:'No matched peer cohort'}</td><td>{device?`${format(device.value,'percent')} · sampled`:'GPU identity / sample unavailable'}</td><td><button onClick={()=>{const ctx=readContext(window.location.search,versionFromPath(window.location.pathname));locationService.push(appLink('analyze',workerContext(ctx,row.row,row.key)));model.setState({matrixSelectionVersion:(model.state.matrixSelectionVersion||0)+1,matrixView:'phase'});}}>Inspect worker →</button></td></tr>;
 })}</tbody></table></div></section>;
}
function PolicyLifecycle({events,context,catalog}:{events:RecordRow[];context:Context;catalog:Catalog}){
 const rows=appliedPolicies(events).filter(row=>!context.variables.run_id?.length||context.variables.run_id.includes('.*')||context.variables.run_id.includes('$__all')||context.variables.run_id.includes(String(row.run_id)));
 return <details className="xlt-completed-detail"><summary>Policy / KV Lifecycle · {new Set(rows.map(row=>`${row.cluster}/${row.run_id}/${row.node}/${row.worker_id}`)).size} workers with applied-version events</summary><p className="xlt-notice">Only weights.applied with producer-reported worker scope is an application boundary. Trainer version and KV counters do not establish applied coverage or causality.</p>{!rows.length?<p className="xlt-empty">No worker-applied policy events in this range. Enable explicit instrumentation after native weight application is confirmed.</p>:<div className="xlt-scroll"><table><thead><tr><th>Applied boundary</th><th>Worker</th><th>Version</th><th>Next</th></tr></thead><tbody>{rows.slice(-12).reverse().map((row,index)=><tr key={index}><td>{new Date(eventTime(row)!).toLocaleTimeString()} · {row.boundary_accuracy==='calibrated'?'calibrated':'node clock'}</td><td>{scalar(row.node)} / {scalar(row.worker_id)}</td><td>v{scalar(row.policy_version)} · producer reported</td><td><Link to="timeline" context={{...context,variables:{...context.variables,run_id:[String(row.run_id)],trace_id:['.*'],record_id:[]}}} catalog={catalog}>Timeline / KV</Link></td></tr>)}</tbody></table></div>}</details>;
}
function Coverage({model}:{model:Shell}) {
  const state=model.useState();
  const providers=React.useMemo(()=>[state.steps,state.spans,state.events,state.comparison,state.evidence,
    ...(state.page==='infrastructure'||state.page==='overview'?[state.components,state.relationships,state.availability]:[]),
    ...(state.page==='deep-dive'?[state.storageSamples,state.storageStatus]:[]),
    ...state.kpis,...state.matrix,...(state.baselineMatrix||[]),...state.pressure]
    .filter((provider,index,all)=>!provider||all.indexOf(provider)===index),
    [state.steps,state.spans,state.events,state.comparison,state.evidence,state.kpis,state.matrix,state.baselineMatrix,state.pressure,state.page,state.storageSamples,state.storageStatus,state.components,state.relationships,state.availability]);
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
    <p>{loading} loading · {providers.filter(Boolean).length} native providers. Presence is not freshness or complete telemetry coverage.</p>
    <div className="xlt-scroll"><table><thead><tr><th>Native provider</th><th>Status</th><th>Targets</th><th>Latest query elapsed</th></tr></thead><tbody>{providers.filter(Boolean).map((provider,index)=>{
      const value=data[providers.indexOf(provider)],request=value?.request;
      const elapsed=request?.endTime!==undefined&&request.startTime!==undefined&&request.endTime>=request.startTime?request.endTime-request.startTime:undefined;
      return <tr key={index}><td>{provider!.state.key||`Provider ${index+1}`}</td><td>{value?.state||'Not active'}{value?.state===LoadingState.Error&&<small>{value.errors?.map(error=>error.message).join(' · ')||value.error?.message||'Native datasource error'}</small>}</td><td>{provider!.state.queries.length}</td><td>{elapsed===undefined?'Not reported':format(elapsed,'ms')}</td></tr>;
    })}</tbody></table></div>
    <p className="xlt-muted">Latest Grafana request timing, not backend CPU cost or a cumulative request count. Identical GPU/Ray detail targets share their pressure provider. Additional native panels activate on demand and are not counted in this custom-provider list. Matrix queries use at most 600 points; baseline intervals stay bounded to one hour.</p>
    <p className="xlt-muted">Ambiguous phases and worker links stay explicit in Matrix. Stale/unknown application age withholds KPI values; measured zero remains a value. Inspect and Explore remain Grafana features.</p>
  </details>;
}
