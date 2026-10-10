import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { test } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { buildNativeWorkspace, NATIVE_PAGES, NativeWorkspace } from '../src/native-workspace';
import type { NativeDestination } from '../src/native-workspace';
import type { Catalog, Panel } from '../src/catalog';
import { WORKSPACE_PAGES } from '../src/navigation';

function sourcePanels(panels: Panel[]): Panel[] {
  return panels.flatMap(panel => [
    ...(panel.type === 'row' ? [] : [panel]),
    ...sourcePanels(panel.panels || []),
  ]);
}

function visibleIndices(workspace: ReturnType<typeof buildNativeWorkspace>): number[] {
  return [
    ...workspace.statIndices,
    ...workspace.primaryIndices,
    ...workspace.groups.flatMap(group => group.panelIndices),
  ];
}

test('native routes preserve every canonical panel and its query/unit/transform contract exactly once', () => {
  const files: Partial<Record<NativeDestination, string>> = {
    start: 'start-here.json', stage: 'agent-rl-stages.json', summary: 'bottleneck-summary.json',
    compute: 'compute-communication.json', storage: 'data-storage.json',
  };
  for (const [destination, file] of Object.entries(files)) {
    const dashboard = JSON.parse(readFileSync(resolve(process.cwd(), '../../examples/dashboards', file), 'utf8'));
    const before = JSON.stringify(dashboard);
    const workspace = buildNativeWorkspace({ [destination]: dashboard }, destination as NativeDestination);
    const indices = visibleIndices(workspace);
    const canonical = sourcePanels(dashboard.panels);
    assert.equal(workspace.available, true);
    assert.equal(indices.length, canonical.length, destination);
    assert.equal(new Set(indices).size, canonical.length, destination);
    assert.deepEqual(indices.map(index => workspace.panels[index].id).sort((a, b) => a - b), canonical.map(panel => panel.id).sort((a, b) => a - b));
    for (const panel of canonical) {
      const retained = workspace.panels.find(candidate => candidate === panel);
      assert.ok(retained, `${destination}: source panel ${panel.id} remains the canonical object`);
      assert.strictEqual(retained.targets, panel.targets);
      assert.strictEqual(retained.fieldConfig, panel.fieldConfig);
      assert.strictEqual(retained.transformations, panel.transformations);
      assert.strictEqual(retained.options, panel.options);
    }
    assert.equal(JSON.stringify(dashboard), before, 'layout does not mutate the dashboard');
  }
});

test('expanded flat rows and nested collapsed rows retain distinct groups and future source panels', () => {
  const panel = (id: number): Panel => ({ id, title: `source ${id}`, type: 'timeseries', targets: [{ refId: 'A', expr: `native_${id}` }] });
  const catalog: Catalog = {
    signals: {
      templating: { list: [] },
      panels: [
        { id: 200, type: 'row', title: 'Serving · native row', panels: [], collapsed: false },
        panel(1), panel(999),
        { id: 202, type: 'row', title: 'Optional native source', panels: [panel(7)], collapsed: true },
        panel(1000),
      ],
    },
  };
  const workspace = buildNativeWorkspace(catalog, 'signals');
  assert.deepEqual(workspace.groups.find(group => group.title === 'Serving · native row')?.panelIndices.map(index => workspace.panels[index].id), [999]);
  assert.deepEqual(workspace.groups.find(group => group.title === 'Optional native source')?.panelIndices.map(index => workspace.panels[index].id), [7]);
  assert.deepEqual(workspace.groups.find(group => group.id === 'additional')?.panelIndices.map(index => workspace.panels[index].id), [1000]);
  assert.deepEqual(visibleIndices(workspace).map(index => workspace.panels[index].id).sort((a, b) => a - b), [1, 7, 999, 1000]);
  const markup = renderToStaticMarkup(React.createElement(NativeWorkspace, { workspace, panels: [] }));
  assert.match(markup, /Optional native source/);
  assert.doesNotMatch(markup, /data-canonical-panel="7"/, 'closed optional groups do not mount or activate their query panels');
});

test('optional missing diagnosis dashboard remains unavailable without substituting live or other-source evidence', () => {
  const workspace = buildNativeWorkspace({}, 'summary');
  assert.equal(workspace.available, false);
  assert.deepEqual(workspace.panels, []);
  assert.deepEqual(visibleIndices(workspace), []);
  const markup = renderToStaticMarkup(React.createElement(NativeWorkspace, { workspace, panels: [] }));
  assert.match(markup, /기존 Dashboard를 사용할 수 없습니다/);
  assert.match(markup, /측정값 0/);
});

test('native navigation has one route per canonical destination and no UI-version paths', () => {
  assert.equal(NATIVE_PAGES.length, 6);
  assert.equal(new Set(NATIVE_PAGES.map(page => page.route)).size, 6);
  assert.equal(new Set(NATIVE_PAGES.map(page => page.destination)).size, 6);
  assert.ok(NATIVE_PAGES.every(page => !/^v[12]\//.test(page.route)));
});

test('workspace and native page guidance is Korean while technical titles and routes remain English', () => {
  for (const page of [...WORKSPACE_PAGES, ...NATIVE_PAGES]) {
    assert.match(page.description, /[가-힣]/);
    assert.doesNotMatch(page.title, /[가-힣]/);
    assert.match(page.route, /^[a-z-]+$/);
  }
  for (const page of NATIVE_PAGES) assert.match(page.notice, /[가-힣]/);
});
