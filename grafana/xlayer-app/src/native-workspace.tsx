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
    description: 'Run을 조사하기 전에 연결된 source와 sample freshness를 확인합니다.',
    notice: 'Collector availability는 exporter에 접근할 수 있는지를 뜻하며 Application 정상 상태를 보장하지 않습니다. Missing metric과 stale sample은 측정값 0과 구분합니다.',
  },
  {
    route: 'stage-correlation', destination: 'stage', title: 'Agent RL Stage Correlation',
    kicker: 'SUBSYSTEM / TRAINING',
    description: '보고된 stage를 Serving, Orchestration, KV Storage, optional Sandbox 관측과 비교합니다.',
    notice: '완료 stage duration은 reported scalar입니다. vLLM / Ray / Mooncake의 engine / session / service scope는 구분하며, 시간 중첩만으로 phase의 resource ownership을 판단하지 않습니다.',
  },
  {
    route: 'bottleneck-summary', destination: 'summary', title: 'Bottleneck Summary',
    kicker: 'NATIVE / DIAGNOSIS',
    description: '저장된 Current / Baseline 비교, Candidate와 Evidence Ledger를 확인합니다.',
    notice: '선택한 조사에 저장된 diagnosis 결과입니다. 현재 live metric으로 과거 Step의 Evidence를 대체하거나, 확인되지 않은 clock / baseline 조건을 보완하지 않습니다.',
  },
  {
    route: 'compute', destination: 'compute', title: 'Compute & Communication',
    kicker: 'SUBSYSTEM / COMPUTE',
    description: 'Device activity, host pressure와 interface / RDMA 관측을 확인합니다.',
    notice: 'GPU는 device, host pressure는 node, NIC / RDMA는 interface 또는 port scope로 관측합니다. 이 값만으로 Run별 resource ownership이나 endpoint pair 간 traffic을 판단하지 않습니다.',
  },
  {
    route: 'storage', destination: 'storage', title: 'Data & Storage',
    kicker: 'SUBSYSTEM / STORAGE',
    description: '선언된 Storage Cluster, local device I/O와 service collection Evidence를 구분해 확인합니다.',
    notice: 'DS / MDS mapping은 선언된 배치이며 device I/O는 host / device 전체 관측입니다. Local mean latency와 maximum reported 3FS p99는 통계와 관측 모집단이 다릅니다. pNFS Deep Dive는 TBD입니다.',
  },
  {
    route: 'signals', destination: 'signals', title: 'Cross-Layer Signals',
    kicker: 'CROSS-LAYER / RESOURCE',
    description: 'Workload / Serving signal과 shared hardware 관측을 함께 비교합니다.',
    notice: '각 panel의 producer, entity와 observation scope를 유지합니다. Signal의 연관성을 Run attribution이나 causality로 해석하지 않습니다.',
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
    {panel ? <panel.Component model={panel} /> : <p className="xlt-empty">기존 panel을 사용할 수 없습니다 · {source.title}</p>}
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
    <summary>{group.title}<span className="xlt-native-group-count">panel {group.panelIndices.length}개</span></summary>
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
    <p className="xlt-empty">기존 Dashboard를 사용할 수 없습니다. Optional source가 비활성화되어 있거나 Dashboard가 provisioning되지 않았을 수 있습니다. 측정값 0이나 성공한 query의 빈 결과가 아닙니다.</p>
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
      <a className="xlt-link" href={dashboardLink(workspace.destination as Destination, context)}>전체 Grafana Dashboard 열기 ↗</a>
      <span className="xlt-muted">기존 query, unit, transformation과 data link를 유지합니다.</span>
    </div>}
  </div>;
}
