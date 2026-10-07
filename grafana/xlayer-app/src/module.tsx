import React, { useEffect, useState, useRef } from "react";
import { AppPlugin, LoadingState, getValueFormat, FieldType } from "@grafana/data";
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
} from "./context";
import {
  PHASES,
  SUBSYSTEMS,
  phaseWindow,
  stepEvidenceCell,
  metricCell,
  selectPhaseSample,
  Cell,
  PhaseWindow,
} from "./semantics";
import { MATRIX_SPECS } from "./matrix-contract";
import {
  eventTime,
  latestEntitySamples,
  recordedSpanErrors,
  reportedRollout,
  workerPeers,
} from "./mockup";
import "./style.css";

type Page = "overview" | "analyze" | "investigate" | "timeline" | "deep-dive";
type ShellState = SceneObjectState & {
  page: Page;
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
  workerDurations?: SceneQueryRunner;
  workerSteps?: SceneQueryRunner;
  workerAge?: SceneQueryRunner;
  timeline?: VizPanel;
  approximate?: VizPanel;
  related: VizPanel[];
  detailPanels:VizPanel[];
  pressure: (SceneQueryRunner | undefined)[];
  contextControls:(SceneTimePicker|SceneRefreshPicker)[];
  selectedCell?: {
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
    name: "Storage latency",
    key: "threefs_p99_latency",
    unit: "ms",
    description: "Diagnosis evidence · shared-service",
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
function makeScene(page: Page, catalog: Catalog) {
  const context = readContext(window.location.search);
  const queryCache = new Map<string, SceneQueryRunner>();
  const query = (key: Destination, id: number, refs?: string[]) => {
    const cacheKey = JSON.stringify([key, id, refs || []]);
    if (queryCache.has(cacheKey)) return queryCache.get(cacheKey);
    const panel = findPanel(catalog[key], id);
    if (!panel) return undefined;
    const provider = runner(panel, refs);
    queryCache.set(cacheKey, provider);
    return provider;
  };
  const native = (key: Destination, id: number) => {
    const panel = findPanel(catalog[key], id);
    return panel ? viz(panel) : undefined;
  };
  const body = new Shell({
    page,
    catalog,
    kpis: [],
    matrix: [],
    related: [],
    detailPanels:[],
    pressure: [],
    contextControls:[new SceneTimePicker({}),new SceneRefreshPicker({ intervals: ['5s','10s','30s','1m'] })],
  });
  body.setState({ steps: query("overview", 20), spans: query("timeline", 9) });
  body.setState({mfu:query('overview',40),policy:query('overview',41),workload:query('overview',42)});
  if (page !== "deep-dive"||context.variables.candidate_id?.[0])
    body.setState({
      steps: query("overview", 20),
      summary: query("summary", 2),
      comparison: query("summary", 4),
      candidates: query("summary", 3),
      evidence: query("summary", 6),
      spans: query("timeline", 9),
    });
  if(page==='deep-dive')body.setState({detailPanels:[native('storage',30),native('stage',60),native('compute',2),native('stage',9),native('stage',22),native('stage',12)].filter(Boolean) as VizPanel[]});
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
    ["overview", "analyze", "investigate", "timeline", "deep-dive"] as Page[]
  ).map(
    (page) =>
      new SceneAppPage({
        title: {
          overview: "Run Overview",
          analyze: "Analyze · Phase × Subsystem",
          investigate: "Investigation",
          timeline: "Agent RL Timeline",
          "deep-dive": "Deep Dive",
        }[page],
        renderTitle: (title) => (
          <h1 className="xlt-page-title">
            XLayer Telemetry <span> / {title}</span>
          </h1>
        ),
        url: `${APP_BASE}/${page}`,
        routePath: `${APP_BASE}/${page}`,
        preserveUrlKeys: [
          "from",
          "to",
          "timezone",
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
function Root() {
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
    <Ready />
  ) : (
    <div className="xlt">
      <p>
        {status === "loading"
          ? "Loading provisioned XLayer dashboards…"
          : error}
      </p>
    </div>
  );
}
export const plugin = new AppPlugin().setRootPage(Root);

function useData(provider: SceneQueryRunner | undefined) {
  const [state, setState] = useState(provider?.state.data);
  useEffect(() => {
    setState(provider?.state.data);
    if (!provider) return;
    const deactivate = provider.activate();
    const sub = provider.subscribeToState((s) => setState(s.data));
    return () => {
      sub.unsubscribe();
      deactivate();
    };
  }, [provider]);
  return state;
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
  const stepData = useData(state.steps),
    summaryData = useData(state.summary),
    compareData = useData(state.comparison),
    candidateData = useData(state.candidates),
    evidenceData = useData(state.evidence),
    spanData = useData(state.spans),
    eventData = useData(state.events),
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
  const context = readContext(window.location.search),
    record = context.variables.record_id?.[0];
  const contextKey = window.location.search;
  useEffect(() => {
    model.setState({ selectedCell: undefined });
  }, [model, contextKey]);
  const selected =
    steps.find((s) => s.record_id === record) ||
    summaries.find((s) => s.record_id === record) ||
    (state.page === "overview" && !record?.match(/[^.*]/)
      ? [...summaries].sort(
          (a, b) => Number(b.window_end_ms) - Number(a.window_end_ms),
        )[0]
      : undefined);
  const matching = (rows: RecordRow[]) =>
    selected
      ? rows.filter(
          (r) =>
            r.record_id === selected.record_id &&
            r.run_id === selected.run_id &&
            numeric(r.window_start_ms) === numeric(selected.window_start_ms) &&
            numeric(r.window_end_ms) === numeric(selected.window_end_ms) &&
            (!selected.observer_node ||
              r.observer_node === selected.observer_node),
        )
      : [];
  const current = matching(comparisons),
    diagnosis = matching(candidates),
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
  const workspaceCandidate=diagnosis.find(c=>c.candidate_id===context.variables.candidate_id?.[0]);
  return (
    <div className="xlt-app-layout"><aside className="xlt-run-context" aria-label="Run Context"><div className="xlt-context-brand">XLayer Telemetry</div><h3>Run Context</h3>{['cluster','run_id'].map(name=>{const variable=sceneGraph.lookupVariable(name,model);return variable?<VariableValueSelectWrapper key={name} variable={variable} showAlways layout="vertical"/>:null;})}<label>Step<select value={selected?.record_id?String(selected.record_id):''} onChange={e=>{const row=steps.find(r=>r.record_id===e.target.value);if(row)navigate(state.page,selectStep(row,context));}}><option value="">Select completed Step</option>{[...new Map([...steps,...(selected?[selected]:[])].map(r=>[String(r.record_id),r])).values()].sort((a,b)=>Number(b.window_end_ms)-Number(a.window_end_ms)).slice(0,40).map(r=><option key={String(r.record_id)} value={String(r.record_id)}>{scalar(r.step)} · {format(r.step_duration_seconds,'s')}</option>)}</select></label><label>Policy Version<span className="xlt-context-value">{scalar(selected?.policy_version,policySamples.length===1?format(policySamples[0].value):'N/A')}</span></label><label>Time Range</label>{state.contextControls.map((c,i)=>{const Control=c.Component as React.ComponentType<{model:any}>;return <Control key={i} model={c}/>;})}<details><summary>Observer / Resource</summary>{['source_node','node'].map(name=>{const variable=sceneGraph.lookupVariable(name,model);return variable?<VariableValueSelectWrapper key={name} variable={variable} showAlways layout="vertical"/>:null;})}</details><div className="xlt-context-status">{activeWorkloads.length===1?scalar(activeWorkloads[0].labels.state):'Status not reported'}<small>Wrapped command · latest report</small></div><a href="https://daegyu94.github.io/xlayer-telemetry/">Documentation ↗</a></aside>
    <div className="xlt">
      <header className="xlt-header">
        <div>
          <span className="xlt-eyebrow">
            XLAYER TELEMETRY{" "}
            {synthetic && <span className="xlt-badge">Synthetic demo</span>}
          </span>
          <h2>
            {state.page === "overview"
              ? "Run Overview"
              : state.page === "analyze"
                ? "Phase × Subsystem Analysis"
                : state.page === "investigate"
                  ? "Slow Step Investigation"
                  : state.page === "timeline"
                    ? "Follow the same interval"
                  : workspaceCandidate?`Deep Dive: ${scalar(workspaceCandidate.component)}`:"Choose a subsystem"}
          </h2>
          <p>
            <b>
              {scalar(
                selected?.run_id,
                context.variables.run_id?.join(", ") || "Select a Run",
              )}
            </b>{" "}
            · {state.page === "overview" ? "Latest diagnosed " : ""}Step{" "}
            {scalar(selected?.step, "not selected")} · Policy{" "}
            {scalar(selected?.policy_version,policySamples.length===1?format(policySamples[0].value):policySamples.length>1?'multiple sources':'not reported')} · Wrapped command{" "}
            {activeWorkloads.length===1?scalar(activeWorkloads[0].labels.state):activeWorkloads.length>1?'multiple reports':'not reported'}
          </p>
          {(policySamples.length||activeWorkloads.length)>0&&<p className="xlt-muted">Policy: producer-reported trainer version · Status: latest wrapper node-clock report, not full async Run completion.</p>}
          {selected && (context.variables.run_id?.length!==1||context.variables.run_id[0]!==selected.run_id) && (
            <p className="xlt-muted">
              Run filter: {context.variables.run_id?.join(", ") || "All"} · this
              diagnosed Step belongs to {scalar(selected.run_id)}
            </p>
          )}
        </div>
        <div className="xlt-header-actions">
          {selected && (
            <button
              onClick={() => navigate("analyze", selectStep(selected, context))}
            >
              Analyze Step {scalar(selected.step)} →
            </button>
          )}
          <Link to="overview" context={context} catalog={state.catalog}>
            Detailed Run Overview
          </Link>
        </div>
      </header>
      <nav className="xlt-nav" aria-label="XLayer investigation">
        <>
          {(["overview", "analyze", "investigate", "deep-dive"] as Page[]).map(
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
                {
                  {
                    overview: "Overview",
                    analyze: "Analyze",
                    investigate: "Investigate",
                    timeline: "Timeline",
                    "deep-dive": "Deep Dive",
                  }[p]
                }
              </a>
            ),
          )}
        </>
      </nav>
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
          <div className="xlt-kpis">
            {KPI_SPECS.map((spec, index) => (
              <Kpi
                key={spec.name}
                spec={spec}
                provider={state.kpis[index]}
                history={spec.key?comparisons.filter(r=>r.signal===spec.key&&r.run_id===selected?.run_id&&r.observation_scope===current.find(c=>c.signal===spec.key)?.observation_scope&&r.entity===current.find(c=>c.signal===spec.key)?.entity).map(r=>({value:numeric(r.current),time:numeric(r.window_end_ms)})).filter(r=>r.value!==undefined&&r.time!==undefined) as {value:number;time:number}[]:undefined}
                ages={
                  spec.name === "Reward" ? samples(rewardAgeData) : undefined
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
          {reportedMfu.length>0&&<p className="xlt-mfu-strip">Framework-reported MFU · {reportedMfu.slice(0,3).map(s=>`${s.labels.phase}: ${format(s.value,'percentunit')} (${s.labels.worker_id})`).join(' · ')} · completed logger observation</p>}
          <section className="xlt-overview-top"><div>
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
            <Native panel={state.timeline} />
          </div><div><h3>Recent Events</h3>
              <DataStatus provider={state.events} />
              <RecentEvents
                rows={events}
                steps={steps}
                context={context}
                catalog={state.catalog}
              />
            </div></section>
          <section className="xlt-observe-grid">
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
                Unique measured spans only. Actor update is separate from critic
                update; reported durations do not create a training boundary.
              </p>
              <Matrix
                model={model}
                selected={selected}
                spans={spans}
                evidence={proofs}
              />
            </section>
          )}
          <Pressure model={model} spans={spans} spanData={spanData} />
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
            <>
              <section className="xlt-investigation-section">
                <div className="xlt-section">
                  <h3>What changed?</h3>
                  <Link to="summary" context={context} catalog={state.catalog}>
                    Full Bottleneck Summary
                  </Link>
                </div>
                <p className="xlt-muted">
                  Saved Step window · workload comparability:{" "}
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
                            <td>{scalar(r.signal)}</td>
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
                    No comparable baseline projection in this interval. No
                    comparison is inferred.
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
              <section>
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
            </>
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
        <><DeepWorkspace candidate={workspaceCandidate} evidence={proofs} panels={state.detailPanels} context={context} catalog={state.catalog}/><section><h3>Phase Correlation · measured intervals</h3><Native panel={state.timeline}/><p className="xlt-muted">Execution intervals share the selected Step time range. Overlap does not establish phase resource ownership.</p></section><Pressure model={model} spans={spans} spanData={spanData} /><section><h3>Existing subsystem dashboards</h3><div className="xlt-actions">{(['compute','storage','stage','timeline','logs'] as Destination[]).map(to=><Link key={to} to={to} context={context} catalog={state.catalog}>{to} ↗</Link>)}</div></section></>
      )}
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
      <footer className="xlt-muted">
        Correlation ≠ attribution ≠ causality. No data ≠ measured zero.{" "}
      </footer>
    </div>
    </div>
  );
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
}: {
  spec: (typeof KPI_SPECS)[number];
  provider?: SceneQueryRunner;
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
  const latest = values
    .filter((s) => !spec.phase || s.labels.phase === spec.phase)
    .sort((a, b) => b.time - a.time)[0];
  const appKeys = [
    "cluster",
    "run_id",
    "node",
    "nodename",
    "instance",
    "producer",
    "role",
    "worker_id",
    "local_rank",
  ];
  const age = latestEntitySamples(ages || []).find((a) =>
    appKeys.every((k) => a.labels[k] === latest?.labels[k]),
  )?.value;
  const stale = age !== undefined && age > (maxAge || 300);
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
      <span className="xlt-eyebrow">{spec.name}</span>
      <strong>
        {!projected && data?.state === LoadingState.Error ? (
          "Query error"
        ) : !projected && !data && provider ? (
          "Loading…"
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
      {trend.length>1&&<div className="xlt-spark" title={projected?'Saved Step observations, not a causal model':'Sampled trend for the displayed entity'}>
        <Sparkline theme={theme} width={110} height={25} sparkline={{
          x:{name:'Time',type:FieldType.time,values:trend.map(p=>p.time),config:{}},
          y:{name:spec.name,type:FieldType.number,values:trend.map(p=>p.value),config:{color:{mode:'fixed',fixedColor:'#4566d5'}},state:{range:{min:Math.min(...trend.map(p=>p.value)),max:Math.max(...trend.map(p=>p.value)),delta:Math.max(...trend.map(p=>p.value))-Math.min(...trend.map(p=>p.value))}}}
        }}/>
      </div>}
      {ages && (
        <small>
          {stale
            ? "Stale sample"
            : age === undefined
              ? "Freshness unknown"
              : `Sample age ${format(age, "s")}`}
        </small>
      )}
      {reported !== undefined && (
        <small>Selected completed Step · reported duration</small>
      )}
      {projected && !unit && <small>Unit not reported</small>}
      <small title={spec.description} className="xlt-kpi-scope">
        {comparison || evidence
          ? `Step · ${scalar(comparison?.observation_scope || evidence?.observation_scope)}`
          : reported !== undefined
            ? "Completed Step"
            : spec.name === "KV token hit"
              ? "Rolling · shared engine"
              : spec.name === "GPU utilization"
                ? "Sampled · device"
                : "Sampled · worker"}
      </small>
      {comparison || evidence ? (
        <small>
          {scalar(
            comparison?.entity || evidence?.entity,
            "Entity not reported",
          )}
        </small>
      ) : (
        latest && (
          <small className="xlt-entity" title={JSON.stringify(latest.labels)}>
            {Object.entries(latest.labels)
              .filter(([k]) =>
                [
                  "node",
                  "nodename",
                  "gpu",
                  "engine",
                  "instance",
                  "worker_id",
                  "run_id",
                ].includes(k),
              )
              .filter(([k, v]) => k !== "nodename" || v !== latest.labels.node)
              .map(([k, v]) => `${k}: ${v}`)
              .join(" · ")}
          </small>
        )
      )}
    </article>
  );
}

function Matrix({
  model,
  selected,
  spans,
  evidence,
}: {
  model: Shell;
  selected: RecordRow;
  spans: RecordRow[];
  evidence: RecordRow[];
}) {
  return (
    <div className="xlt-scroll">
      <table className="xlt-matrix">
        <thead>
          <tr>
            <th>System layer</th>
            {PHASES.map((p) => (
              <th key={p}>
                {p.replace("_", " ")}
                <small>
                  {phaseWindow(spans, selected, p).status === "observed"
                    ? `${phaseWindow(spans, selected, p).accuracy} span`
                    : "No unique measured interval"}
                </small>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {SUBSYSTEMS.map((s) => (
            <MatrixRow
              key={s}
              subsystem={s}
              model={model}
              selected={selected}
              spans={spans}
              evidence={evidence}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
function MatrixRow({
  subsystem: s,
  model,
  selected,
  spans,
  evidence,
}: {
  subsystem: string;
  model: Shell;
  selected: RecordRow;
  spans: RecordRow[];
  evidence: RecordRow[];
}) {
  const provider =
      model.state.matrix[SUBSYSTEMS.indexOf(s as (typeof SUBSYSTEMS)[number])],
    data = useData(provider),
    values = samples(data),
    spec = MATRIX_SPECS[s],
    stepCell = stepEvidenceCell(evidence, s);
  return (
    <tr>
      <th>
        {
          (
            {
              gpu: "GPU",
              vllm: "vLLM",
              kv: "KV Cache",
              ray: "Ray",
              network: "Network",
              storage: "Storage",
              sandbox: "Sandbox",
            } as Record<string, string>
          )[s]
        }
        <small>
          {spec.label}
          {s === "gpu" ? " · MFU is a separate reported stage signal" : ""}
        </small>
      </th>
      {PHASES.map((p) => {
        const window = phaseWindow(spans, selected, p),
          choice = selectPhaseSample(values, window);
        let cell = metricCell(
          choice.sample ? { ...choice.sample, unit: spec.unit } : undefined,
          window,
          spec.scope,
          spec.rolling,
        );
        if (choice.entities > 1)
          cell = {
            ...cell,
            state: "ambiguous",
            explanation: `${choice.entities} entities match. Select a GPU / engine; no average or maximum is assigned to this phase.`,
          };
        if (s === "ray")
          cell = {
            ...cell,
            value: undefined,
            state: "missing",
            binding: "unmapped",
            explanation:
              "Canonical Ray task aggregation drops node identity. Session-level context cannot be phase/node mapped.",
          };
        cell = { ...cell, evidence: stepCell.evidence };
        if (cell.value === undefined && stepCell.evidence?.length)
          cell = {
            ...cell,
            binding: "step-window",
            type: "saved diagnosis projection",
            explanation:
              cell.explanation +
              " Saved evidence describes the full Step, not this phase.",
          };
        const measured = cell.value !== undefined;
        return (
          <td key={p}>
            <button
              className="xlt-cell"
              aria-label={`${p} × ${s} evidence`}
              onClick={() => {
                model.setState({
                  selectedCell: { phase: p, subsystem: s, cell, window },
                });
                setTimeout(
                  () =>
                    document
                      .querySelector(".xlt-evidence")
                      ?.scrollIntoView({ behavior: "smooth", block: "start" }),
                  0,
                );
              }}
            >
              <b>
                {data?.state === LoadingState.Error
                  ? "Query error"
                  : measured
                    ? format(cell.value, cell.unit)
                    : choice.entities > 1
                      ? "Select entity"
                      : window.status !== "observed"
                        ? "N/A"
                        : s === "ray" && values.length
                          ? "Session context"
                          : !values.length
                            ? "No data"
                            : spec.rolling
                              ? "Rolling context"
                              : "Scope unmatched"}
              </b>
              <small title={cell.explanation}>
                {measured
                  ? `${cell.scope} · sampled`
                  : window.status === "ambiguous"
                    ? "Ambiguous phase"
                    : window.status === "missing"
                      ? ""
                      : cell.scope}
              </small>
              {p === "rollout" && !!stepCell.evidence?.length && (
                <span className="xlt-step-evidence">Step evidence →</span>
              )}
            </button>
          </td>
        );
      })}
    </tr>
  );
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
  context,
  catalog,
}: {
  rows: RecordRow[];
  steps: RecordRow[];
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
            const time = eventTime(r),
              step = steps.find(
                (s) =>
                  s.run_id === r.run_id && String(s.step) === String(r.step),
              );
            let target = context;
            try {
              if (step) target = selectStep(step, context);
            } catch {}
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
  spans,
  spanData,
}: {
  model: Shell;
  spans: RecordRow[];
  spanData?: import("@grafana/data").PanelData;
}) {
  const errors = recordedSpanErrors(spans);
  return (
    <section className="xlt-pressure">
      <div className="xlt-section">
        <h3>System Pressure</h3>
        <span>Range-end observations · no cause verdict</span>
      </div>
      <div className="xlt-pressure-grid">
        {PRESSURE_SPECS.map((spec, index) => (
          <PressureCard
            key={spec.name}
            spec={spec}
            provider={model.state.pressure[index]}
            context={readContext(window.location.search)}
            catalog={model.state.catalog}
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
}: {
  spec: (typeof PRESSURE_SPECS)[number];
  provider?: SceneQueryRunner;
  context: Context;
  catalog: Catalog;
}) {
  const data = useData(provider),
    entities = latestEntitySamples(samples(data));
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
        entities.slice(0, 2).map((s, i) => (
          <div className="xlt-pressure-value" key={i}>
            <strong>{format(s.value * (spec.scale || 1), spec.unit)}</strong>
            <small title={JSON.stringify(s.labels)}>
              {Object.entries(s.labels)
                .filter(([k]) =>
                  [
                    "node",
                    "nodename",
                    "gpu",
                    "instance",
                    "State",
                    "SessionName",
                    "device",
                    "port",
                    "worker_id",
                  ].includes(k),
                )
                .map(([k, v]) => `${k}=${v}`)
                .join(" · ")}
            </small>
          </div>
        ))
      )}
      {entities.length > 2 && (
        <small>+{entities.length - 2} other entities · open details</small>
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
function DeepWorkspace({candidate,evidence,panels,context,catalog}:{candidate?:RecordRow;evidence:RecordRow[];panels:VizPanel[];context:Context;catalog:Catalog}) {
 const[tab,setTab]=useState(0);const proofs=candidate?evidence.filter(e=>e.candidate_id===candidate.candidate_id):[];
 return <section className="xlt-workspace"><div className="xlt-workspace-grid"><div><h3>Key Findings</h3>{candidate?<><span className="xlt-badge xlt-badge-warning">{scalar(candidate.state).replace(/_/g,' ')}</span><p>{scalar(candidate.summary)}</p>{proofs.filter(e=>e.evidence_type==='supporting').slice(0,3).map((e,i)=><p key={i}><b>{i+1}. {scalar(e.signal)}</b><br/>{format(e.baseline,scalar(e.unit,''))} → {format(e.current,scalar(e.unit,''))}<br/><small>{scalar(e.observation_scope)} · {scalar(e.entity,'Entity not reported')}</small></p>)}<h4>Against / Missing</h4>{proofs.filter(e=>e.evidence_type==='missing'||e.evidence_type==='counter').map((e,i)=><p key={i}>{scalar(e.signal)} · {scalar(e.observation_scope)}</p>)}</>:<p className="xlt-empty">Choose a candidate in Investigate to keep its supporting, against and missing evidence in this workspace.</p>}<p className="xlt-notice">Shared evidence is correlation; per-run ownership and a causal path are not established.</p><Link to="timeline" context={context} catalog={catalog}>Detailed Timeline</Link></div><div><h3>Detailed Metrics</h3><div className="xlt-chips">{['Local I/O mean','KV RPC p95','GPU','vLLM','Ray','Sandbox'].map((name,i)=><button key={name} aria-pressed={tab===i} onClick={()=>setTab(i)}>{name}</button>)}</div>{panels[tab]&&<Native panel={panels[tab]}/>}<p className="xlt-muted">Native canonical panel · independent units/scopes. Local mean I/O and connector RPC p95 are not 3FS service p99.</p><div className="xlt-actions"><Link to="logs" context={context} catalog={catalog}>Logs</Link><Link to="timeline" context={context} catalog={catalog}>Events / Spans</Link><Link to="storage" context={context} catalog={catalog}>Full Storage</Link></div></div></div></section>;
}

function RelatedTabs({panels}:{panels:VizPanel[]}){const[tab,setTab]=useState(0);return <><div className="xlt-chips">{['GPU','vLLM','KV Cache','Storage','Network'].slice(0,panels.length).map((label,i)=><button key={label} aria-pressed={tab===i} onClick={()=>setTab(i)}>{label}</button>)}</div>{panels[tab]&&<Native panel={panels[tab]}/>}</>;}
