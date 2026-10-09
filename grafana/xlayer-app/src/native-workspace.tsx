import React, { useState } from 'react';
import type { VizPanel } from '@grafana/scenes';
import type { Catalog, Panel } from './catalog';
import { dashboardLink } from './context';
import type { Context, Destination } from './context';

/** Native views borrow the provisioned dashboard, including optional source rows. */
export const NATIVE_PAGES = [
  {
    route: 'start-here', destination: 'start', title: 'Start Here · Collection Health',
    kicker: 'GET STARTED',
    description: 'Verify connected sources and sample freshness before investigating a Run.',
    notice: 'Collector availability is exporter reachability, not application health. Missing metrics and stale samples remain distinct from measured zero.',
  },
  {
    route: 'stage-correlation', destination: 'stage', title: 'Agent RL Stage Correlation',
    kicker: 'SUBSYSTEM / TRAINING',
    description: 'Compare reported stages with serving, orchestration, KV storage and optional Sandbox observations.',
    notice: 'Completed stage duration is a reported scalar. vLLM, Ray and Mooncake retain their own engine, session and service scope; overlap does not establish phase ownership.',
  },
  {
    route: 'bottleneck-summary', destination: 'summary', title: 'Bottleneck Summary',
    kicker: 'NATIVE / DIAGNOSIS',
    description: 'Review saved Current / Baseline comparisons, candidates and the evidence ledger.',
    notice: 'These are saved diagnosis projections for the selected investigation. Current live resource metrics do not replace historical Step evidence or missing clock and baseline proof.',
  },
  {
    route: 'compute', destination: 'compute', title: 'Compute & Communication',
    kicker: 'SUBSYSTEM / COMPUTE',
    description: 'Inspect device activity, host pressure and interface / RDMA observations.',
    notice: 'GPU observations have device scope; host pressure has node scope; NIC / RDMA observations have interface or port scope. They do not establish per-Run resource ownership or endpoint-pair traffic.',
  },
  {
    route: 'storage', destination: 'storage', title: 'Data & Storage',
    kicker: 'SUBSYSTEM / STORAGE',
    description: 'Keep declared Storage Cluster resources, local device I/O and service collection evidence separate.',
    notice: 'DS / MDS mappings describe declared deployment. Device I/O is host / device-wide. Local mean latency and maximum reported 3FS p99 have different statistics and populations; pNFS Deep Dive remains TBD.',
  },
  {
    route: 'signals', destination: 'signals', title: 'Cross-Layer Signals',
    kicker: 'CROSS-LAYER / RESOURCE',
    description: 'Compare workload and serving signals alongside shared hardware observations.',
    notice: 'Each panel preserves its producer, entity and observation scope. Related signals are neither Run attribution nor a causal conclusion.',
  },
] as const;

export type NativePage = (typeof NATIVE_PAGES)[number];
export type NativeRoute = NativePage['route'];
export type NativeDestination = NativePage['destination'];

export type NativeWorkspaceGroup = {
  id: string;
  title: string;
  panelIndices: number[];
  sourceCollapsed?: boolean;
};

export type NativeWorkspaceModel = {
  destination: NativeDestination;
  available: boolean;
  /** Source references only: queries, units, options and transformations are untouched. */
  panels: Panel[];
  statIndices: number[];
  primaryIndices: number[];
  groups: NativeWorkspaceGroup[];
};

const PRIMARY: Record<NativeDestination, { stats: number[]; panels: number[] }> = {
  start: { stats: [2, 3, 4, 5], panels: [6, 7] },
  stage: { stats: [1, 2, 3], panels: [4, 21, 9, 22, 60, 12] },
  summary: { stats: [], panels: [2, 4, 3, 6] },
  compute: { stats: [], panels: [2, 8, 6, 30, 9, 40] },
  storage: { stats: [], panels: [1, 30, 111, 112, 31, 4] },
  signals: { stats: [], panels: [1, 2, 5, 3, 6, 4] },
};

/**
 * A layout plan, rather than another query catalog. Root places viz(model.panels)
 * directly in Scene state so every panel inherits native variable/time scope.
 * Only mounted panel components activate their query runners.
 */
export function buildNativeWorkspace(
  catalog: Catalog,
  destination: NativeDestination,
): NativeWorkspaceModel {
  const dashboard = catalog[destination as Destination];
  const model: NativeWorkspaceModel = {
    destination,
    available: !!dashboard,
    panels: [],
    statIndices: [],
    primaryIndices: [],
    groups: [],
  };
  if (!dashboard) return model;

  const assignments = new Map<number, NativeWorkspaceGroup>();
  const additional: NativeWorkspaceGroup = {
    id: 'additional', title: 'Additional canonical panels', panelIndices: [],
  };

  function collect(panels: Panel[], inherited?: NativeWorkspaceGroup, path = '') {
    let current = inherited;
    panels.forEach((panel, position) => {
      if (panel.type === 'row') {
        const group: NativeWorkspaceGroup = {
          id: `${path}/${panel.id}:${position}`,
          title: panel.title,
          panelIndices: [],
          sourceCollapsed: panel.collapsed,
        };
        model.groups.push(group);
        if (panel.panels?.length) {
          // Collapsed native rows contain their own children. Subsequent source
          // siblings remain outside them, unlike expanded rows with flat panels.
          collect(panel.panels, group, group.id);
          current = inherited;
        } else {
          current = group;
        }
        return;
      }
      const index = model.panels.push(panel) - 1;
      assignments.set(index, current || additional);
      // Preserve unusual nested source containers rather than losing panels.
      if (panel.panels?.length) collect(panel.panels, current, `${path}/${panel.id}`);
    });
  }
  collect(dashboard.panels);

  const selected = new Set<number>();
  const choose = (ids: number[], stats: boolean) => ids.flatMap(id => {
    const index = model.panels.findIndex(panel => panel.id === id);
    if (index < 0 || selected.has(index) || (stats && model.panels[index].type !== 'stat')) return [];
    selected.add(index);
    return [index];
  });
  model.statIndices = choose(PRIMARY[destination].stats, true);
  model.primaryIndices = choose(PRIMARY[destination].panels, false);
  for (const [index, group] of assignments) {
    if (!selected.has(index)) group.panelIndices.push(index);
  }
  if (additional.panelIndices.length) model.groups.push(additional);
  return model;
}

function NativePanel({ source, panel }: { source: Panel; panel?: VizPanel }) {
  const stat = source.type === 'stat';
  const text = source.type === 'text';
  return <div
    className={`xlt-native-panel${stat ? ' xlt-native-stat' : ''}${text ? ' xlt-native-text' : ''}`}
    data-canonical-panel={source.id}
    data-panel-type={source.type}
    style={{ height: stat ? 126 : text ? undefined : source.type === 'table' ? 290 : 270 }}
  >
    {panel ? <panel.Component model={panel} /> : <p className="xlt-empty">Canonical panel unavailable · {source.title}</p>}
  </div>;
}

function NativeGroup({ group, workspace, panels }: {
  group: NativeWorkspaceGroup;
  workspace: NativeWorkspaceModel;
  panels: Array<VizPanel | undefined>;
}) {
  const [open, setOpen] = useState(false);
  if (!group.panelIndices.length) return null;
  return <details className="xlt-native-group" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{group.title}<span className="xlt-native-group-count">{group.panelIndices.length} panels</span></summary>
    {open && <div className="xlt-native-grid">
      {group.panelIndices.map(index => <NativePanel key={index} source={workspace.panels[index]} panel={panels[index]} />)}
    </div>}
  </details>;
}

export function NativeWorkspace({ workspace, panels, context }: {
  workspace: NativeWorkspaceModel;
  panels: Array<VizPanel | undefined>;
  context?: Context;
}) {
  if (!workspace.available) return <section className="xlt-native-workspace">
    <p className="xlt-empty">Canonical dashboard unavailable. An optional source may be disabled or its dashboard may not be provisioned; this is not a measured zero or an empty successful query.</p>
  </section>;
  return <div className="xlt-native-workspace" data-native-destination={workspace.destination}>
    {!!workspace.statIndices.length && <div className="xlt-native-kpis">
      {workspace.statIndices.map(index => <NativePanel key={index} source={workspace.panels[index]} panel={panels[index]} />)}
    </div>}
    <div className="xlt-native-grid">
      {workspace.primaryIndices.map(index => <NativePanel key={index} source={workspace.panels[index]} panel={panels[index]} />)}
    </div>
    <div className="xlt-native-catalog">
      {workspace.groups.map(group => <NativeGroup key={`${workspace.destination}:${group.id}`} group={group} workspace={workspace} panels={panels} />)}
    </div>
    {context && <div className="xlt-actions xlt-native-footer">
      <a className="xlt-link" href={dashboardLink(workspace.destination as Destination, context)}>Open full Grafana dashboard ↗</a>
      <span className="xlt-muted">Canonical queries, units, transformations and data links are preserved.</span>
    </div>}
  </div>;
}
