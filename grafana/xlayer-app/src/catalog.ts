import { PHASE_COLORS } from "./matrix-presentation";
import { map } from "rxjs";
import { timelineLanes } from "./mockup";
import { getBackendSrv } from "@grafana/runtime";
import {
  SceneQueryRunner,
  SceneDataTransformer,
  VizPanel,
  SceneVariableSet,
  QueryVariable,
  CustomVariable,
  TextBoxVariable,
  ConstantVariable,
  DataProviderProxy,
} from "@grafana/scenes";
import { selectTargets } from "./data";
export { records, samples } from "./data";
import {
  DASHBOARD_UIDS,
  Destination,
  RecordRow,
  decodeRecord,
  VARIABLE_NAMES,
  Context,
} from "./context";
import { Sample } from "./semantics";
import {preserveIdentity} from './identity-variable';

class IdentityQueryVariable extends QueryVariable {
  protected interceptStateUpdateAfterValidation(update:any){
    super.interceptStateUpdateAfterValidation(update);
    preserveIdentity(this.state.name,this.state.value,this.state.text,update);
  }
}

// Dashboard JSON is the query/units/source contract. This app contains no PromQL,
// LogQL or datasource UID defaults; it reads the provisioned, potentially remapped copy.
export type Panel = {
  id: number;
  type: string;
  title: string;
  description?: string;
  targets?: any[];
  datasource?: any;
  options?: any;
  fieldConfig?: any;
  transformations?: any[];
  panels?: Panel[];
  gridPos?: {x: number; y: number; w: number; h: number};
  collapsed?: boolean;
};
export type Dashboard = { title?: string; description?: string; panels: Panel[]; templating: { list: any[] } };
export type Catalog = Partial<Record<Destination, Dashboard>>;
export async function loadCatalog(): Promise<Catalog> {
  const entries = await Promise.all(
    Object.entries(DASHBOARD_UIDS).map(async ([key, uid]) => {
      try {
        const result = await getBackendSrv().get(
          `/api/dashboards/uid/${uid}`,
          undefined,
          undefined,
          { showErrorAlert: false },
        );
        return [key, result.dashboard];
      } catch (error: any) {
        if (error.status === 404) return [key, undefined];
        throw error;
      }
    }),
  );
  const catalog = Object.fromEntries(entries) as Catalog;
  if (
    !catalog.overview ||
    !catalog.stage ||
    !catalog.compute ||
    !catalog.storage
  )
    throw new Error(
      "Canonical XLayer dashboards are not provisioned. Run scripts/provision_dashboards.py first.",
    );
  return catalog;
}
export function findPanel(
  dashboard: Dashboard | undefined,
  id: number,
): Panel | undefined {
  const visit = (items: Panel[]): Panel | undefined => {
    for (const p of items) {
      if (p.id === id) return p;
      const child = visit(p.panels || []);
      if (child) return child;
    }
    return undefined;
  };
  return visit(dashboard?.panels || []);
}
export function runner(panel: Panel, refs?: string[]): SceneQueryRunner {
  return new SceneQueryRunner({
    datasource: panel.datasource,
    queries: selectTargets(panel.targets || [], refs) as any[],
    maxDataPoints: 600,
    minInterval: "2s",
  });
}
export function viz(panel: Panel, shared?:SceneQueryRunner): VizPanel {
  // A proxy preserves the owner's parent/time/variable scope. Reparenting a
  // shared runner under this panel would break the pressure consumer.
  const data = shared?new DataProviderProxy({source:shared.getRef()}):runner(panel);
  // Styling only: collection-point charts keep their original no-line contract.
  const fields=panel.type==='timeseries'&&panel.fieldConfig?.defaults?.custom?.drawStyle!=='points'
    ? {...panel.fieldConfig,defaults:{...panel.fieldConfig?.defaults,custom:{...panel.fieldConfig?.defaults?.custom,fillOpacity:0,lineWidth:1.5}}}
    : panel.fieldConfig;
  return new VizPanel({
    pluginId: panel.type,
    title: panel.title,
    description: panel.description,
    options: panel.type === "timeseries" ? {...panel.options,legend:{...panel.options?.legend,displayMode:"list",placement:"bottom",calcs:[]}} : panel.options || {},
    fieldConfig: panel.type==="state-timeline"?{...panel.fieldConfig,defaults:{...panel.fieldConfig?.defaults,mappings:Object.entries(PHASE_COLORS).map(([phase,color])=>({type:"regex",options:{pattern:`^${phase}\\b.*`,result:{color}}}))},overrides:panel.fieldConfig?.overrides||[]}:fields || { defaults: {}, overrides: [] },
    $data: panel.transformations?.length
      ? new SceneDataTransformer({
          $data: data,
          transformations: [...panel.transformations,...(panel.type === "state-timeline" ? [((_context:any)=>map((frames:any)=>timelineLanes(frames)))] : [])],
        })
      : data,
  });
}
export function variables(
  catalog: Catalog,
  context: Context,
): SceneVariableSet {
  const definitions = new Map<string, any>();
  // Overview owns Run + observer semantics, then preserve additional native filters.
  for (const key of [
    "overview",
    "stage",
    "summary",
    "timeline",
    "storage",
    "compute",
    "logs",
    "start",
  ] as Destination[])
    for (const v of catalog[key]?.templating.list || [])
      if (!definitions.has(v.name)) definitions.set(v.name, v);
  return new SceneVariableSet({
    variables: VARIABLE_NAMES.filter(name=>!name.startsWith("matrix_")&&name!=="phase_worker").map((name) => {
      const definition = definitions.get(name);
      const rawSelected = context.variables[name];
      const selected = definitions.get(name)?.includeAll&&rawSelected?.some(value=>value==='.*')?['$__all']:rawSelected;
      const visible = ["cluster", "run_id", "source_node", "node"].includes(
        name,
      );
      if (!definition)
        return new ConstantVariable({
          name,
          value: selected?.[0] || ".*",
          hide: 2,
        });
      if (["phase", "role", "worker"].includes(name)) {
        // Navigation regexes must not inherit a canonical page's first option.
        return new TextBoxVariable({
          name,
          value: selected?.[0] || ".*",
          hide: 2,
        });
      }
      const current = ["phase", "role", "worker"].includes(name)
        ? ".*"
        : (definition.current?.value ?? definition.query ?? ".*");
      const state = {
        ...definition,
        isMulti: definition.multi,
        allowCustomValue: true,
        value: selected ? (definition.multi ? selected : selected[0]) : current,
        text: selected
          ? definition.multi
            ? selected
            : selected[0]
          : definition.current?.text,
        hide: visible ? 0 : 2,
        skipUrlSync: false,
      };
      delete state.current;
      delete state.options;
      if (definition.type === "query") return new IdentityQueryVariable(state);
      if (definition.type === "custom") return new CustomVariable(state);
      return new TextBoxVariable({ ...state, value: String(state.value) });
    }),
  });
}
