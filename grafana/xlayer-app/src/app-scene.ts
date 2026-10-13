import { resourceMatrixProfile } from './resource-matrix';
import { relatedPanels } from './related-panels';
import { PageLayoutType } from '@grafana/data';
import { SceneApp, SceneAppPage, EmbeddedScene, SceneTimeRange, SceneTimePicker, SceneRefreshPicker, SceneObjectBase, SceneObjectState, SceneQueryRunner, VizPanel } from '@grafana/scenes';
import { Catalog, findPanel, runner, viz, variables } from './catalog';
import { APP_BASE, VARIABLE_NAMES, readContext, Destination, sceneTime } from './context';
import { SUBSYSTEMS, Cell, PhaseWindow } from './semantics';
import { DEEP_DIVE_SPECS } from './presentation';
import { canonicalRefs } from './data';
import { INFRA_PANELS, compactResourceLegend, logPanel } from './infrastructure';
import { MATRIX_SPECS } from './matrix-contract';
import { RUN_SOURCE_PANELS } from './RunSources';
import { WORKSPACE_PAGES } from './navigation';
import { NATIVE_PAGES, buildNativeWorkspace, NativeWorkspaceModel } from './native-workspace';
import { ShellView } from './InvestigationView';
import { PRESSURE_SPECS, KPI_SPECS } from './scene-specs';

export type Page = typeof WORKSPACE_PAGES[number]['route'] | typeof NATIVE_PAGES[number]['route'];

export type ShellState = SceneObjectState & {
  page: Page;
  nativeWorkspace?:NativeWorkspaceModel;
  nativePanels?:VizPanel[];
  catalog: Catalog;
  runSources?: (VizPanel|undefined)[];
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
  relatedLabels:string[];
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

export class Shell extends SceneObjectBase<ShellState> {
  static Component = ShellView;
}

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
  const native = (key: Destination, id: number, enableMatrix=true, compactNodeLegend=false, compactIdentity=false, title?:string) => {
    const panel = findPanel(catalog[key], id);
    if(!panel)return undefined;
    let component:string|undefined;
    try{const identity=JSON.parse(context.variables.infra_component?.[0]||'null');component=Array.isArray(identity)?identity[2]:undefined;}catch{}
    const keepNode=typeof component==='string'&&component.endsWith('-fabric');
    const rendered=title||compactNodeLegend?{...panel,...(title?{title}:{}),targets:panel.targets?.map(target=>({...target,
      legendFormat:compactNodeLegend&&typeof target.legendFormat==='string'?compactResourceLegend(target.legendFormat,keepNode):target.legendFormat}))}:panel;
    const pressure=PRESSURE_SPECS.find(s=>s.dashboard===key&&s.panel===id&&
      JSON.stringify(canonicalRefs(panel.targets||[],s.refs))===JSON.stringify(canonicalRefs(panel.targets||[])));
    return viz(rendered,pressure&&(page==='analyze'||page==='investigate'||page==='deep-dive')?query(key,id,pressure.refs):undefined,enableMatrix?resourceMatrixProfile(key,id):undefined,compactIdentity);
  };
  const body = new Shell({
    page,
    catalog,
    kpis: [],
    matrix: [],
    related: [], relatedLabels:[],
    detailPanels:[],
    pressure: [],
    contextControls:[new SceneTimePicker({isOnCanvas:false}),new SceneRefreshPicker({ intervals: ['5s','10s','30s','1m'] })],
  });
  const nativePage=NATIVE_PAGES.find(p=>p.route===page);
  if(nativePage){const workspace=buildNativeWorkspace(catalog,nativePage.destination);body.setState({nativeWorkspace:workspace,nativePanels:workspace.panels.map(panel=>viz(panel,undefined,resourceMatrixProfile(nativePage.destination,panel.id)))});}
  const resourcePage=page==='demo'||!!nativePage||page==='infrastructure'||page==='logs'||page==='runs';
  if(page==='runs')body.setState({steps:query('overview',20),runSources:RUN_SOURCE_PANELS.map(source=>native(source.source,source.panel,false,false,false,source.title)),
    infraComponents:query('compute',INFRA_PANELS.components),infraEdges:query('compute',INFRA_PANELS.edges),infraAvailability:query('compute',INFRA_PANELS.availability)});
  if(!resourcePage){
    body.setState({ steps: query("overview", 20), spans: query("timeline", 9) });
    body.setState({mfu:query('overview',40),policy:query('overview',41),workload:query('overview',42)});
  } else if(context.variables.record_id?.some(id=>id!=='.*'&&id!=='$__all'))body.setState({steps:query('overview',20),summary:query('summary',2)});
  // SceneAppPage caches this scene across visits; provider availability must
  // not depend on the candidate selected at the first entry.
  if (!resourcePage)
    body.setState({
      steps: query("overview", 20),
      summary: query("summary", 2),
      comparison: query("summary", 4),
      candidates: query("summary", 3),
      evidence: query("summary", 6),
      spans: query("timeline", 9),
    });
  if(page==='infrastructure')body.setState({infraComponents:query('compute',INFRA_PANELS.components),infraEdges:query('compute',INFRA_PANELS.edges),infraAvailability:query('compute',INFRA_PANELS.availability),
    infraMetrics:[native('compute',30,true,true,true),native('overview',2,true,true,true),native('compute',2,true,true,true),native('compute',6,true,true,true),native('compute',8,true,true,true),native('compute',9,true,true),native('storage',1,true,true,true),native('storage',2,true,true),native('storage',30,true,true),native('storage',31,true,true)]});
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
      ...relatedPanels([{label:'GPU',panel:native('signals',5,false)},{label:'vLLM',panel:native('signals',2)},
        {label:'KV Cache',panel:native('stage',28)},{label:'Storage',panel:native('stage',60)},
        {label:'Network',panel:native('compute',9,false)}]),
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
      ...relatedPanels([{label:'Reward',panel:native('timeline',4)},{label:'Events',panel:native('timeline',5)}]),
    });
  return new EmbeddedScene({
    $timeRange: new SceneTimeRange({
      from: sceneTime(context.from),
      to: sceneTime(context.to),
      timeZone: context.timezone,
    }),
    $variables: page==='demo'?undefined:variables(catalog, context),
    body,
    controls: [],
  });
}

export function createApp(catalog: Catalog) {
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
        getScene: () => makeScene(page, catalog),
      }),
  );
  return new SceneApp({
    pages,
    urlSyncOptions: { updateUrlOnInit: true, createBrowserHistorySteps: true },
  });
}
