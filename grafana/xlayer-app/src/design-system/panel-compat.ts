/** Scoped compatibility for measured matrix/topology and native panel geometry.
 * Product tokens and primitives live in tokens.ts / styles.ts / components.tsx. */
export const panelCompatibility = `
/* Dashboard design tokens shared by App and canonical Scenes panels. */

.xlt-sidebar-foot {
  border-top:1px solid var(--xl-border);
  margin:30px 10px 0;
  padding:15px 0;
  color:var(--xl-secondary);
  font-size:12px
}

.xlt-sidebar-foot small {
  display:block;
  font-size:12px;
  margin-top:5px;
  color:var(--xl-muted)
}

.xlt-main {
  min-width:0
}

.xlt-native-heading h2 {
  font-size:27px;
  font-weight:730;
  letter-spacing:-.8px;
  margin:5px 0 7px;
  line-height:1.3
}

& a:focus-visible,& summary:focus-visible {
  outline:2px solid var(--xl-primary);
  outline-offset:3px
}

.xlt-workspace section {
  background:var(--xl-bg);
  border:1px solid var(--xl-border);
  border-radius:9px;
  padding:16px 17px
}

.xlt-workspace h3 {
  font-size:13px;
  font-weight:700;
  margin:0 0 12px
}

.xlt-evidence h4 {
  font-size:13px;
  margin:10px 0 6px
}

.xlt-spark {
  height:25px;
  margin:3px 0;
  overflow:hidden;
  max-width:100%
}

.xlt-mfu-strip {
  font-size:12px;
  color:var(--xl-muted)
}

.xlt-overview-top,.xlt-observe-grid {
  display:grid;
  grid-template-columns:minmax(0,1.65fr) minmax(300px,1fr);
  gap:14px;
  background:none;
  border:0;
  padding:0
}

.xlt-overview-top>div,.xlt-observe-grid>div {
  min-width:0;
  background:var(--xl-bg);
  border:1px solid var(--xl-border);
  border-radius:9px;
  padding:16px 17px
}

.xlt-overview-top h3,.xlt-observe-grid h3 {
  font-size:13px;
  margin:0 0 12px;
  font-weight:700
}

.xlt-related {
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:14px
}

.xlt-panel {
  height:270px;
  min-width:0;
  overflow:hidden;
  display:flex;
  flex-direction:column;
  border-radius:6px
}

.xlt-panel>div {
  height:100%;
  min-height:0
}

.xlt-events table {
  min-width:380px;
  font-size:12px
}

.xlt-events small {
  display:block;
  font-size:12px;
  color:var(--xl-muted)
}

.xlt-scroll {
  max-width:100%;
  overflow:auto;
  border:1px solid var(--xl-border);
  border-radius:6px
}

& tbody tr:last-child td {
  border-bottom:0
}

& tbody tr:hover {
  background:var(--xl-soft)
}

.xlt-entity {
  overflow-wrap:anywhere
}

.xlt-matrix {
  min-width:760px;
  table-layout:fixed
}

.xlt-matrix th:first-child {
  width:166px;
  position:sticky;
  left:0;
  z-index:1
}

.xlt-matrix td,.xlt-matrix th {
  padding:8px
}

.xlt-matrix .xlt-cell {
  width:100%;
  text-align:left;
  border:0;
  padding:4px;
  white-space:normal;
  min-height:60px;
  color:var(--xl-text)
}

.xlt-cell b,.xlt-cell small,.xlt-cell span {
  display:block
}

.xlt-matrix .xlt-cell b {
  font-size:15px;
  font-weight:600
}

.xlt-matrix .xlt-cell small {
  font-size:12px;
  margin:3px 0;
  color:var(--xl-muted)
}

.xlt-matrix .xlt-cell:hover {
  background:var(--xl-selected)
}

.xlt-matrix-delta {
  font-size:12px;
  margin:3px 0;
  color:var(--xl-muted)
}

.xlt-matrix-toolbar {
  display:flex;
  flex-wrap:wrap;
  gap:15px;
  justify-content:space-between;
  align-items:center;
  margin:10px 0
}

.xlt-matrix-toolbar label {
  font-size:12px;
  color:var(--xl-muted)
}

.xlt-matrix-toolbar select {
  max-width:420px;
  display:block;
  margin-top:4px
}

.xlt-matrix-legend,.xlt-phase-legend {
  display:flex;
  flex-wrap:wrap;
  gap:12px;
  margin:10px 0;
  font-size:12px;
  color:var(--xl-muted)
}

.xlt-phase-legend i,.xlt-subsystem-dot {
  display:inline-block;
  width:8px;
  height:8px;
  border-radius:3px;
  margin-right:5px
}

.xlt-step-evidence {
  border-top:1px solid var(--xl-border);
  padding-top:4px;
  margin-top:4px;
  font-size:12px;
  color:var(--xl-primary)
}

.xlt-entity-select {
  width:100%;
  max-width:155px;
  margin-top:7px
}

.xlt-storage-metric {
  display:block;
  width:100%;
  margin:7px 0 12px;
  height:32px
}

.xlt-run-context {
  margin:0;
  padding:13px 30px;
  background:var(--xl-bg);
  border-bottom:1px solid var(--xl-border);
  color:var(--xl-text)
}

.xlt-context-fields {
  display:grid;
  grid-template-columns:minmax(110px,1fr) minmax(140px,1.2fr) minmax(160px,1fr) minmax(280px,1.7fr);
  gap:12px;
  align-items:end
}

.xlt-context-fields>* {
  min-width:0
}

.xlt-context-step {
  display:block;
  color:var(--xl-muted);
  font-size:12px
}

.xlt-context-step select {
  display:block;
  width:100%;
  margin-top:4px;
  height:32px
}

.xlt-context-time>span {
  display:block;
  color:var(--xl-muted);
  font-size:12px;
  margin-bottom:4px
}

.xlt-context-time>div {
  display:flex;
  gap:5px;
  align-items:center;
  min-width:0
}

.xlt-context-time>div>div {
  min-width:0
}

.xlt-context-time button {
  height:32px;
  font-size:12px;
  padding:3px 6px
}

.xlt-context-time button span {
  white-space:nowrap;
  max-width:25ch;
  overflow:hidden;
  text-overflow:ellipsis
}

.xlt-context-meta {
  display:flex;
  flex-wrap:wrap;
  gap:8px 16px;
  align-items:center;
  font-size:12px;
  color:var(--xl-muted);
  margin-top:8px
}

.xlt-context-meta b {
  color:var(--xl-text);
  font-weight:550
}

.xlt-context-meta details {
  margin:0
}

.xlt-context-meta details[open] {
  width:100%
}

.xlt-context-meta details>div {
  display:flex;
  gap:12px;
  flex-wrap:wrap;
  padding:8px 0
}

.xlt-visible-range {
  display:block;
  color:var(--xl-muted);
  font-size:12px
}

.xlt-filters,.xlt-completed-detail {
  font-size:12px;
  color:var(--xl-muted);
  margin:14px 0
}

& summary {
  cursor:pointer
}

.xlt-filters>div {
  display:flex;
  flex-wrap:wrap;
  gap:10px;
  padding:12px 0
}

.xlt-completed-detail {
  background:var(--xl-bg);
  border:1px solid var(--xl-border);
  border-radius:8px;
  padding:13px 16px
}

.xlt-completed-detail>summary {
  color:var(--xl-text);
  font-size:12px;
  font-weight:550
}

.xlt-chips {
  display:flex;
  flex-wrap:wrap;
  gap:5px;
  margin:8px 0 12px
}

.xlt-chips button {
  font-size:12px
}

.xlt-chips [aria-pressed=true] {
  background:var(--xl-selected);
  color:var(--xl-primary);
  border-color:var(--xl-primary)
}

.xlt-pressure-grid {
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:10px
}

.xlt-pressure-value strong {
  display:block;
  font-size:22px;
  font-weight:650;
  margin:7px 0
}

.xlt-pressure-value small {
  display:block;
  color:var(--xl-muted);
  font-size:12px
}

.xlt-pressure-value .xlt-entity,.xlt-storage-evidence .xlt-entity {
  display:block;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis
}

.xlt-health-grid {
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:8px
}

.xlt-health-grid small,.xlt-health-grid span {
  display:block;
  margin-top:5px
}

.xlt-health-grid small {
  font-size:12px;
  color:var(--xl-muted)
}

.xlt-candidates {
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:10px
}

.xlt-candidates article:has(.xlt-badge-warning) {
  border-left:3px solid var(--xl-warning)
}

.xlt-candidate-actions {
  display:flex;
  flex-wrap:wrap;
  gap:12px;
  font-size:12px;
  margin-top:10px
}

.xlt-evidence {
  border:1px solid var(--xl-primary);
  border-radius:9px;
  padding:16px 17px;
  background:var(--xl-bg);
  margin:14px 0;
  scroll-margin-top:90px;
  font-size:12px
}

.xlt-evidence-layout {
  display:grid;
  grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);
  gap:14px;
  margin:18px 0
}

.xlt-evidence-layout>div {
  min-width:0
}

.xlt-evidence-layout .xlt-evidence {
  margin:0
}

.xlt-evidence:focus-visible {
  outline:2px solid var(--xl-primary)
}

.xlt-investigation-section .xlt-scroll {
  max-height:460px
}

.xlt-deep {
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:12px
}

.xlt-workspace-grid {
  display:grid;
  grid-template-columns:minmax(0,1fr) minmax(0,1.5fr);
  gap:14px
}

.xlt-workspace-grid>div {
  min-width:0
}

.xlt-common-storage {
  border-bottom:1px solid var(--xl-border);
  padding-bottom:12px;
  margin-bottom:12px
}

.xlt-common-storage td small {
  display:block;
  color:var(--xl-muted);
  margin-top:3px
}

.xlt-storage-cluster-panels,.xlt-infra-metrics {
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:14px
}

.xlt-storage-cluster-controls {
  display:flex;
  flex-wrap:wrap;
  gap:12px;
  margin:10px 0
}

.xlt-topology svg {
  display:block;
  color:var(--xl-muted)
}

.xlt-topology-node {
  cursor:pointer
}

.xlt-topology-node rect {
  fill:var(--xl-bg);
  stroke:var(--xl-border);
  stroke-width:1
}

.xlt-topology-node rect.xlt-topology-selected {
  fill:var(--xl-selected);
  stroke:var(--xl-primary);
  stroke-width:2
}

.xlt-topology-node text {
  fill:var(--xl-text);
  font-size:13px
}

.xlt-topology-node text.xlt-topology-meta {
  fill:var(--xl-muted);
  font-size:12px
}

.xlt-topology-node:focus rect {
  stroke:var(--xl-primary);
  stroke-width:2
}

.xlt-device-map {
  margin:12px 0;
  padding:12px;
  border:1px solid var(--xl-border);
  border-radius:6px
}

.xlt-device-map>div {
  display:flex;
  flex-wrap:wrap;
  gap:8px;
  margin-top:10px;
  padding-top:10px;
  border-top:1px dashed var(--xl-border)
}

.xlt-device-map small {
  display:block;
  color:var(--xl-muted);
  font-size:12px
}

.xlt-native-heading {
  margin-bottom:18px
}

.xlt-native-heading p {
  font-size:12px;
  color:var(--xl-muted)
}

.xlt-native-kpis {
  display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:14px;
  margin:14px 0
}

.xlt-native-grid {
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:14px;
  margin:14px 0
}

.xlt-native-panel {
  min-width:0;
  background:var(--xl-bg);
  border:1px solid var(--xl-border);
  border-radius:9px;
  overflow:hidden
}

.xlt-native-panel>.xlt-panel {
  height:290px
}

.xlt-native-stat>.xlt-panel {
  height:126px
}

.xlt-native-text>.xlt-panel {
  height:200px
}

.xlt-native-group {
  background:var(--xl-bg);
  border:1px solid var(--xl-border);
  border-radius:8px;
  padding:13px 16px;
  margin:14px 0
}

.xlt-native-group summary {
  font-size:12px;
  font-weight:600
}

.xlt-native-group-count {
  font-size:12px;
  color:var(--xl-muted);
  margin-left:8px
}

@media(max-width:1260px) {

  .xlt-context-fields {
    grid-template-columns:repeat(3,minmax(0,1fr))
  }

  .xlt-context-time {
    grid-column:1/-1
  }

  .xlt-context-time>div {
    justify-content:flex-end
  }

  .xlt-context-time>span {
    position:absolute;
    margin-top:9px
  }

  .xlt-run-context {
    padding:13px 20px
  }

}

@media(max-width:1000px) {
  .xlt-overview-top,.xlt-observe-grid,.xlt-workspace-grid {
    grid-template-columns:1fr
  }

  .xlt-pressure-grid {
    grid-template-columns:repeat(2,minmax(0,1fr))
  }

}

@media(max-width:900px) {

  .xlt-evidence-layout,.xlt-storage-cluster-panels,.xlt-infra-metrics {
    grid-template-columns:1fr
  }

}

@media(max-width:640px) {

  .xlt-native-heading h2 {
    font-size:26px
  }

  .xlt-context-fields {
    grid-template-columns:repeat(2,minmax(0,1fr))
  }

  .xlt-context-step {
    grid-column:1/-1
  }

  .xlt-context-time>span {
    position:static;
    margin-top:0
  }

  .xlt-context-time>div {
    justify-content:flex-start;
    flex-wrap:wrap
  }

  .xlt-visible-range {
    display:block
  }

  .xlt-run-context {
    padding:11px 15px
  }

  .xlt-native-kpis {
    grid-template-columns:repeat(2,minmax(0,1fr));
    gap:10px
  }

  .xlt-related,.xlt-candidates,.xlt-deep,.xlt-native-grid {
    grid-template-columns:1fr
  }

  .xlt-health-grid {
    grid-template-columns:repeat(2,minmax(0,1fr))
  }

  .xlt-matrix-toolbar,.xlt-matrix-toolbar label {
    width:100%
  }

  .xlt-matrix-toolbar select {
    width:100%;
    max-width:100%
  }

  .xlt-topology svg {
    min-width:800px
  }

}

/* Respond to the available App width, including Grafana's optional docked menu. */
.xlt-main {
  container-type:inline-size
}

@container(max-width:1050px) {
  .xlt-context-fields {
    grid-template-columns:repeat(3,minmax(0,1fr))
  }

  .xlt-context-time {
    grid-column:1/-1
  }

  .xlt-context-time>div {
    justify-content:flex-end;
    flex-wrap:wrap
  }

  .xlt-context-time>span {
    position:absolute;
    margin-top:9px
  }

  .xlt-overview-top,.xlt-observe-grid {
    grid-template-columns:minmax(0,1.65fr) minmax(260px,1fr)
  }

}

@container(max-width:760px) {
  .xlt-context-fields {
    grid-template-columns:repeat(2,minmax(0,1fr))
  }

  .xlt-context-step {
    grid-column:1/-1
  }

  .xlt-context-time>span {
    position:static;
    margin-top:0
  }

  .xlt-context-time>div {
    justify-content:flex-start
  }

  .xlt-overview-top,.xlt-observe-grid,.xlt-workspace-grid {
    grid-template-columns:1fr
  }

}

.xlt-native-highlights {
  margin:14px 0
}

.xlt-investigate-grid {
  display:grid;
  grid-template-columns:minmax(0,1fr) minmax(0,1.25fr);
  gap:14px;
  align-items:start
}

.xlt-investigate-grid>section {
  margin:0;
  background:var(--xl-bg);
  border:1px solid var(--xl-border);
  border-radius:9px;
  padding:16px 17px
}

.xlt-investigate-grid>.xlt-investigation-section {
  grid-column:1/-1;
  grid-row:1
}

.xlt-investigate-grid>.xlt-candidate-section {
  grid-column:1/-1;
  grid-row:3
}

.xlt-investigate-grid>.xlt-candidate-section .xlt-candidates {
  grid-template-columns:repeat(2,minmax(0,1fr))
}

.xlt-investigate-grid>p {
  grid-column:1/-1
}

.xlt-investigate-grid>section>h3 {
  margin-bottom:12px
}

@container(max-width:760px) {
  .xlt-investigate-grid {
    grid-template-columns:1fr
  }

  .xlt-investigate-grid>.xlt-candidate-section {
    grid-column:1;
    grid-row:auto
  }

}

/* Scenes owns the custom page body;
Grafana's global navigation stays available. */

.xlt-investigation-section table {
  min-width:520px;
  font-size:12px
}

.xlt-investigation-section td,.xlt-investigation-section th {
  padding:8px 9px
}

.xlt-investigation-section td:nth-child(2),.xlt-investigation-section td:nth-child(3) {
  white-space:nowrap
}

/* KPI values and mini trends occupy separate rows, including narrow cards. */

/* Readable hierarchy shared by custom components and borrowed native panels. */
& .xlt-workspace h3{font-size:16px}
& .xlt-evidence h4{font-size:15px}
& .xlt-events table,& .xlt-investigation-section table{font-size:13px}
& .xlt-context-step,& .xlt-context-time>span,& .xlt-context-meta{font-size:12px}
& .xlt-pressure-value small{font-size:12px}
& [data-testid="data-testid Panel header"] h2{font-size:15px;color:var(--xl-text)}
& [data-testid^="data-testid VizLegend series"] button{font-size:12px;color:var(--xl-secondary);border:0;background:none;padding:0}
& .xlt-entity-select{max-width:190px;font-size:12px;height:34px}
& .xlt-matrix{min-width:920px}& .xlt-matrix th:first-child{width:190px}
& .xlt-matrix .xlt-cell{min-height:82px}& .xlt-matrix .xlt-cell small,& .xlt-step-evidence{font-size:12px}
& .xlt-context-time button span{max-width:25ch}& .xlt-context-variable{min-width:0}
& .xlt-matrix-toolbar select{font-size:13px;max-width:100%}
& .xlt-native-stat{min-height:140px}
@media(max-width:640px){& .xlt-native-heading h2{font-size:26px}}

/* B2 Compact Layered Cluster Map: layout groups are not observed links. */
.xlt-infrastructure{min-width:0}
.xlt-cluster-map-overview{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:12px 0 16px}
.xlt-cluster-map-overview>div{background:var(--xl-bg);border:1px solid var(--xl-border);border-radius:9px;padding:13px 16px;min-width:0}
.xlt-cluster-map-overview small{display:block;font-size:13px;color:var(--xl-secondary)}
.xlt-cluster-map-overview strong{display:block;font-size:22px;line-height:1.3;margin:5px 0}
.xlt-cluster-map-overview span{font-size:12px;color:var(--xl-muted);display:block}
.xlt-infra-workspace{display:grid;grid-template-columns:minmax(0,1fr) 318px;gap:14px;align-items:start}
& .xlt-cluster-map,& .xlt-resource-inspector{border:1px solid var(--xl-border);border-radius:10px;background:var(--xl-bg);margin:0;min-width:0}
.xlt-cluster-map-header{padding:17px 19px 11px;display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}
.xlt-cluster-map-header h3{font-size:16px;margin:0 0 5px}.xlt-cluster-map-header p{font-size:12px;color:var(--xl-muted);margin:0}
.xlt-cluster-map-mode{display:flex;gap:6px}.xlt-cluster-map-mode [aria-pressed=true]{color:var(--xl-primary);border-color:var(--xl-primary);background:var(--xl-selected)}
.xlt-cluster-map-body{margin:0 18px 16px;padding:12px;border:1px solid var(--xl-border);border-radius:10px;background:var(--xl-soft);max-width:870px}
.xlt-cluster-map-layer{border:1px solid var(--xl-border);border-radius:9px;padding:13px 14px}
.xlt-cluster-map-layer[data-layer=compute]{background:var(--xl-soft);border-color:var(--xl-border)}
.xlt-cluster-map-layer[data-layer=network]{background:var(--xl-soft);border-color:var(--xl-border)}
.xlt-cluster-map-layer[data-layer=storage]{background:var(--xl-soft);border-color:var(--xl-border)}
.xlt-cluster-map-layer-header{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.xlt-cluster-map-layer-header h4{font-size:13px;font-weight:750;margin:0;color:var(--xl-text)}
.xlt-cluster-map-layer-header span{font-size:12px;color:var(--xl-muted)}
.xlt-cluster-map-layer[data-layer=network] h4{color:var(--xl-text)}.xlt-cluster-map-layer[data-layer=storage] h4{color:var(--xl-text)}
.xlt-cluster-map-nodes{display:grid;gap:9px}.xlt-cluster-map-compute-grid,.xlt-cluster-map-network-grid,.xlt-cluster-map-storage-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
& button.xlt-cluster-map-node{display:flex;align-items:center;gap:10px;padding:10px 11px;min-height:70px;min-width:0;width:100%;white-space:normal;color:var(--xl-text);border:1px solid var(--xl-border);border-radius:8px;background:var(--xl-bg);text-align:left}
& button.xlt-cluster-map-node:hover{border-color:var(--xl-primary);background:var(--xl-selected)}
& button.xlt-cluster-map-node[aria-pressed=true]{border-color:var(--xl-primary);outline:2px solid color-mix(in srgb,var(--xl-primary) 18%,transparent);background:var(--xl-selected)}
.xlt-cluster-map-icon{flex:none;width:33px;height:33px;display:grid;place-items:center;background:var(--xl-soft);color:var(--xl-text);border-radius:7px}
.xlt-cluster-map-node[data-kind=storage] .xlt-cluster-map-icon{background:var(--xl-soft);color:var(--xl-text)}
.xlt-cluster-map-node[data-kind=network] .xlt-cluster-map-icon{background:var(--xl-soft);color:var(--xl-text)}
.xlt-cluster-map-node-text{min-width:0;flex:1;overflow-wrap:anywhere}
.xlt-cluster-map-node-text strong{display:block;font-size:13px;font-weight:650;line-height:1.35}
.xlt-cluster-map-node-text small{display:block;font-size:12px;color:var(--xl-muted);line-height:1.4}
.xlt-cluster-map-state{flex:none;font-size:12px;font-weight:600;color:var(--xl-text);background:var(--xl-soft);border-radius:50px;padding:3px 7px;display:inline-flex;gap:4px;align-items:center;line-height:1.4}
.xlt-cluster-map-state[data-state=available]{color:var(--xl-teal);background:var(--xl-soft)}.xlt-cluster-map-state[data-state=available]:before{content:'✓'}
.xlt-cluster-map-state[data-state=stale]{color:var(--xl-warning);background:var(--xl-soft)}.xlt-cluster-map-state[data-state=stale]:before{content:'!'}
.xlt-cluster-map-state[data-state=down]{color:var(--xl-danger);background:var(--xl-soft)}.xlt-cluster-map-state[data-state=down]:before{content:'×'}
.xlt-cluster-map-state[data-state=unknown]:before,.xlt-cluster-map-state[data-state=ambiguous]:before{content:'?'}
.xlt-cluster-map-grouping{height:32px;display:flex;align-items:center;justify-content:center;position:relative}
.xlt-cluster-map-grouping:before{content:'';position:absolute;left:50%;top:2px;height:28px;border-left:2px dashed var(--xl-border)}
.xlt-cluster-map-grouping span{z-index:1;background:var(--xl-bg);border:1px solid var(--xl-border);border-radius:30px;padding:2px 9px;margin-left:80px;font-size:12px;color:var(--xl-muted)}
.xlt-cluster-map-storage-groups{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.65fr);gap:12px}
.xlt-cluster-map-storage-groups h5,.xlt-cluster-map-unclassified h5,.xlt-cluster-map-selected-device h5{font-size:12px;color:var(--xl-secondary);margin:0 0 8px;font-weight:650}
.xlt-cluster-map-unclassified,.xlt-cluster-map-selected-device{margin-top:14px}.xlt-cluster-map-empty{font-size:12px;color:var(--xl-muted)}
& .xlt-cluster-map-more{margin-top:9px;background:transparent;border:1px dashed var(--xl-border);color:var(--xl-text);width:100%;white-space:normal}
.xlt-cluster-map-scope{font-size:12px;line-height:1.5;color:var(--xl-muted);margin:10px 0 0}
.xlt-cluster-map-boundary{border-top:1px solid var(--xl-border);padding:12px 19px 15px;font-size:12px;color:var(--xl-muted);margin:0;line-height:1.55}
.xlt-cluster-map-boundary strong{color:var(--xl-text)}
.xlt-resource-inspector{position:sticky;top:18px;overflow:hidden}.xlt-resource-inspector-header{padding:17px;border-bottom:1px solid var(--xl-border)}
.xlt-resource-inspector-header>small{font-size:12px;color:var(--xl-muted);font-weight:650}.xlt-resource-inspector-header h3{font-size:18px;margin:8px 0 10px;overflow-wrap:anywhere}
.xlt-resource-inspector-body{padding:13px 17px 17px}.xlt-resource-inspector-identity{margin:0}
.xlt-resource-inspector-identity>div{display:grid;grid-template-columns:95px minmax(0,1fr);gap:9px;padding:9px 0;border-bottom:1px solid var(--xl-border);font-size:13px}
.xlt-resource-inspector-identity dt{color:var(--xl-muted)}.xlt-resource-inspector-identity dd{margin:0;text-align:right;font-weight:550;overflow-wrap:anywhere}
.xlt-resource-inspector-select{display:block;margin:14px 0;font-size:12px;color:var(--xl-secondary)}.xlt-resource-inspector-select select{display:block;width:100%;margin-top:5px}
.xlt-resource-inspector-links{display:flex;flex-direction:column;gap:8px}.xlt-resource-inspector-links>a,.xlt-resource-inspector-inventory-link{display:block;border:1px solid var(--xl-border);padding:8px 10px;border-radius:6px;font-size:13px;color:var(--xl-primary);text-align:center;overflow-wrap:anywhere}
.xlt-resource-inspector-inventory-link{margin-top:12px}
& .xlt-infra-resource-metrics{background:var(--xl-bg);border:1px solid var(--xl-border);border-radius:10px;padding:17px 19px;margin:16px 0}
.xlt-infra-resource-metrics h3{font-size:16px}.xlt-infra-inventory-search{display:block;margin:14px 0;font-size:13px;color:var(--xl-secondary)}
.xlt-infra-inventory-search input{display:block;width:min(100%,480px);padding:8px 10px;border:1px solid var(--xl-border);background:var(--xl-bg);color:var(--xl-text);border-radius:6px;margin-top:6px;font:inherit}
@container(max-width:1060px){.xlt-infra-workspace{grid-template-columns:minmax(0,1fr) 290px}.xlt-cluster-map-storage-groups{grid-template-columns:1fr}.xlt-cluster-map-node{flex-wrap:wrap}.xlt-cluster-map-node>.xlt-cluster-map-state{margin-left:43px}}
@container(max-width:870px){.xlt-infra-workspace{grid-template-columns:1fr}.xlt-resource-inspector{position:static}.xlt-cluster-map-storage-groups{grid-template-columns:minmax(0,1fr) minmax(0,1.65fr)}}
@media(max-width:720px){.xlt-cluster-map-overview{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.xlt-cluster-map-overview>div{padding:11px 12px}.xlt-cluster-map-overview strong{font-size:20px}.xlt-cluster-map-header{padding:13px}.xlt-cluster-map-body{margin:0 9px 12px;padding:9px}.xlt-cluster-map-layer{padding:10px}.xlt-cluster-map-storage-groups{grid-template-columns:1fr}.xlt-cluster-map-network-grid{grid-template-columns:1fr}.xlt-cluster-map-grouping span{margin-left:0}& .xlt-cluster-map-node{padding:8px;gap:7px;flex-wrap:wrap}.xlt-cluster-map-node>.xlt-cluster-map-state{margin-left:0}.xlt-cluster-map-icon{width:28px;height:28px}.xlt-cluster-map-node-text strong{font-size:13px}.xlt-cluster-map-mode{width:100%}}
@media(max-width:390px){.xlt-cluster-map-compute-grid,.xlt-cluster-map-storage-grid{grid-template-columns:1fr}}
/* Preserve a readable identity column instead of squeezing words between badges. */
& button.xlt-cluster-map-node{flex-wrap:wrap}
.xlt-cluster-map-node-text{flex:1 1 110px;min-width:110px}
.xlt-cluster-map-node>.xlt-cluster-map-state{margin-left:43px}
@container(max-width:1150px){.xlt-infra-workspace{grid-template-columns:1fr}.xlt-resource-inspector{position:static}.xlt-cluster-map-storage-groups{grid-template-columns:minmax(0,1fr) minmax(0,1.65fr)}}
@media(max-width:520px){.xlt-cluster-map-compute-grid,.xlt-cluster-map-storage-grid{grid-template-columns:1fr}.xlt-cluster-map-node>.xlt-cluster-map-state{margin-left:35px}}
& .xlt-cluster-map-header h3{font-weight:700}& .xlt-resource-inspector-header h3{font-weight:650}
.xlt-run-search{align-items:end;gap:12px;flex-wrap:wrap}.xlt-run-search label{display:flex;flex-direction:column;gap:6px}.xlt-run-search input:not([type=checkbox]){min-height:34px;min-width:200px;background:var(--xl-bg);color:var(--xl-text);border:1px solid var(--xl-border);border-radius:6px;padding:6px 9px}.xlt-run-explorer section+section{margin-top:18px}.xlt-run-explorer input[type=checkbox]{width:16px;height:16px}.xlt-run-explorer small{display:block}.xlt-run-explorer details a{overflow-wrap:anywhere}

/* Resource matrices use the existing Scenes query and a bounded scroll area. */
& .xlt-resource-panel{height:auto;min-width:0;padding:12px;background:var(--xl-bg);border-radius:8px}
.xlt-resource-panel>header{display:flex;flex-wrap:wrap;gap:8px;justify-content:space-between;align-items:center}
.xlt-resource-panel h3{margin:0;font-size:15px}
.xlt-resource-controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}
.xlt-resource-controls label{display:flex;gap:6px;align-items:center;font-size:12px}
.xlt-resource-controls button,.xlt-resource-controls select{font:inherit;color:var(--xl-text);background:var(--xl-bg);border:1px solid var(--xl-border);border-radius:5px;padding:5px 8px}
.xlt-resource-controls button[aria-pressed=true]{border-color:#4566d5}
.xlt-resource-note{font-size:12px;line-height:1.5;margin:8px 0}
.xlt-resource-scroll{max-height:320px;overflow:auto}
.xlt-resource-scroll table{width:100%;border-collapse:separate;border-spacing:4px;font-size:13px}
.xlt-resource-scroll th{white-space:nowrap;text-align:left;font-weight:500;color:var(--xl-text)}
.xlt-resource-scroll td{padding:0;min-width:72px}
.xlt-resource-cell{width:100%;min-height:42px;border:1px solid var(--xl-border);border-radius:5px;color:var(--xl-text);font:inherit;background:var(--xl-bg);padding:6px;cursor:pointer}
.xlt-resource-cell:disabled{cursor:default;opacity:1;color:var(--xl-muted)}
.xlt-resource-cell b,.xlt-resource-cell small{display:block}
.xlt-resource-cell[aria-pressed=true]{outline:2px solid #4566d5;outline-offset:-2px}
.xlt-resource-detail{height:260px;min-width:0}
.xlt-resource-selection{border-top:1px solid var(--xl-border);margin-top:12px}
.xlt-panel:has(>.xlt-resource-panel),.xlt-native-panel:has(>.xlt-resource-panel){height:auto!important;min-height:270px}
.xlt-panel:has(>.xlt-resource-panel)>div{height:auto}
@media(max-width:640px){.xlt-resource-panel{padding:8px}.xlt-resource-scroll{max-height:210px}}

.xlt-resource-scroll tr>th:first-child{position:sticky;left:0;z-index:1;background:var(--xl-bg);max-width:190px;white-space:normal;min-width:120px}
/* The generic table minimum must not push a NIC's only column offscreen. */
& .xlt-resource-scroll table{min-width:0}
.xlt-resource-scroll tr>th:first-child{width:150px}

& .xlt-resource-cell{color:var(--xl-text)}
& .xlt-resource-cell:disabled{color:var(--xl-muted)}

/* Reading layouts keep native query/identity contracts and expose full content. */
.xlt-native-text,.xlt-native-panel[data-panel-type=table]{grid-column:1/-1}
.xlt-native-kpis{grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr))}
.xlt-panel:has(>.xlt-native-timeline),.xlt-native-panel:has(>.xlt-native-timeline){height:auto!important}
.xlt-native-timeline{min-width:0;flex-shrink:0}
.xlt-native-timeline>div{height:100%}
[data-native-destination=summary] .xlt-native-panel[data-panel-type=table]{grid-column:1/-1}
.xlt-comparison-table td:first-child{max-width:240px;white-space:normal;overflow-wrap:anywhere}
.xlt-comparison-table th,.xlt-comparison-table td{font-variant-numeric:tabular-nums}
.xlt-comparison-table td:nth-child(2),.xlt-comparison-table td:nth-child(3),.xlt-comparison-table td:nth-child(4){white-space:nowrap}
.xlt-comparison-table td:last-child{max-width:220px;white-space:normal;overflow-wrap:anywhere}
.xlt-comparison-table details small{display:block;margin:5px 0}
.xlt-native-highlights details{font-size:12px}
.xlt-native-highlights details p{font-size:12px;line-height:1.5;margin:6px 0}
@container(max-width:1024px){.xlt-investigate-grid>.xlt-candidate-section .xlt-candidates{grid-template-columns:1fr}}

`;
