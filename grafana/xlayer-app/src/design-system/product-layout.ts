/** Product composition only; theme and primitives are defined separately. */
export const productLayout = `
/* Decision-first product hierarchy. Native panel styles and query units stay local. */
.xlt-run-navigation {display:flex;flex-wrap:wrap;align-items:center;gap:16px;padding:12px 24px;border-bottom:1px solid var(--xl-border);background:var(--xl-bg)}
.xlt-run-navigation>strong {min-width:0;overflow-wrap:anywhere}
.xlt-run-navigation .xlt-investigation-nav {margin-left:auto;padding:0;border:0}
.xlt-dashboard-directory-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,22rem),1fr));gap:16px}
.xlt-dashboard-directory-grid>a {display:flex;flex-direction:column;text-decoration:none;color:var(--xl-text)}
.xlt-dashboard-directory-grid>a:hover {background:var(--xl-soft)}
.xlt-dashboard-directory-grid>a>span {margin-top:auto;color:var(--xl-primary)}
.xlt-investigation-nav {display:flex;flex-wrap:wrap;gap:6px;padding:10px 24px;border-bottom:1px solid var(--xl-border);background:var(--xl-bg)}
.xlt-investigation-nav a {display:flex;align-items:center;gap:6px;padding:8px 12px;color:var(--xl-muted);border-radius:5px}
.xlt-investigation-nav a[aria-current=page] {background:var(--xl-selected);color:var(--xl-primary);font-weight:650}
.xlt-primary-kpis {grid-template-columns:repeat(auto-fit,minmax(min(100%,10.5rem),1fr))}
.xlt-progress strong {font-size:26px}
.xlt-primary-kpis .xlt-eyebrow,.xlt-primary-kpis .xlt-kpi small,.xlt-primary-kpis .xlt-kpi-unit,.xlt-mfu-strip {font-size:var(--xl-supporting);line-height:1.5}
.xlt-content .xlt-run-brief {display:flex;align-items:center;justify-content:space-between;gap:24px;border-left:4px solid var(--xl-muted);background:var(--xl-bg)}
.xlt-content .xlt-run-brief.xlt-brief-attention {border-left-color:var(--xl-warning)}
.xlt-run-brief h3 {font-size:21px;margin:4px 0 7px}
.xlt-run-brief p {margin:0 0 6px;color:var(--xl-secondary)}
.xlt-run-brief small {display:block;color:var(--xl-muted)}
.xlt-brief-action {flex:0 0 auto;max-width:40%}
.xlt-brief-action small {margin-top:8px}

.xlt-slow-steps ol {list-style:none;padding:0;margin:12px 0 0}
.xlt-slow-steps li+li {border-top:1px solid var(--xl-border)}
.xlt-slow-steps li button {display:grid;grid-template-columns:110px minmax(40px,1fr) 85px 80px;align-items:center;gap:16px;width:100%;border:0;background:transparent;padding:12px 0;text-align:left}
.xlt-slow-steps li button:hover {background:var(--xl-soft)}
.xlt-duration-track {height:7px;background:var(--xl-soft);border-radius:3px;overflow:hidden}
.xlt-duration-track i {display:block;height:100%;background:var(--xl-primary)}
.xlt-secondary-signals,.xlt-execution-details {margin:16px 0;background:var(--xl-bg);border:1px solid var(--xl-border);border-radius:8px;padding:16px}
.xlt-secondary-signals>summary,.xlt-execution-details>summary {cursor:pointer;font-weight:600}
.xlt-secondary-signals .xlt-kpis {margin-top:14px;grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}
.xlt-investigate-grid>.xlt-run-brief {grid-column:1/-1;grid-row:1}
.xlt-investigate-grid>.xlt-candidate-section {grid-row:2}
.xlt-investigate-grid>p {grid-row:3}
.xlt-investigate-grid>.xlt-investigation-section {grid-row:4}
@container(max-width:700px) {.xlt-primary-kpis {grid-template-columns:repeat(2,minmax(0,1fr))}}
@container(max-width:600px) {
 .xlt-content .xlt-run-brief {flex-direction:column;align-items:stretch;gap:12px}
 .xlt-brief-action {max-width:none}
 .xlt-investigation-nav {padding:8px}
 .xlt-investigation-nav a {padding:7px;font-size:12px}
 .xlt-slow-steps li button {grid-template-columns:85px minmax(15px,1fr) 65px;gap:8px}
 .xlt-slow-steps li button>span:last-child {display:none}
 .xlt-primary-kpis .xlt-kpi strong {font-size:23px}
}

.xlt-run-heading {margin-bottom:8px;gap:12px}
.xlt-run-heading .xlt-header-details {margin-top:6px}
.xlt-clock-quality {margin:6px 0;font-size:var(--xl-supporting);line-height:1.5}
.xlt-primary-kpis .xlt-kpi {min-height:170px}
.xlt-primary-kpis .xlt-kpi strong {font-size:27px}
.xlt-inventory-summary {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:18px}
.xlt-inventory-summary>div {background:var(--xl-bg);border:1px solid var(--xl-border);border-radius:8px;padding:16px}
.xlt-run-source-grid {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.xlt-run-source {min-width:0;border:1px solid var(--xl-border);border-radius:8px;background:var(--xl-bg);overflow:hidden}
.xlt-run-source>header {display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 16px;border-bottom:1px solid var(--xl-border)}
.xlt-run-source h4 {margin:0 0 4px;font-size:16px}
.xlt-run-source small {color:var(--xl-muted);font-size:var(--xl-supporting);line-height:1.5}
.xlt-run-source-panels {display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
.xlt-run-source-wide {grid-column:1/-1}
.xlt-run-source-metric {min-width:0;padding:10px;border-right:1px solid var(--xl-border)}
.xlt-run-source-metric:last-child {border-right:0}
.xlt-run-source .xlt-panel {height:230px;min-height:0;margin:5px 0 0}
.xlt-run-source-wide .xlt-native-panel[data-panel-type=table] {height:280px;overflow:auto}
.xlt-run-source .xlt-empty {padding:20px}
.xlt-resource-gpu-list {display:flex;flex-direction:column;gap:14px;min-width:0}
.xlt-resource-gpu-node {min-width:0}
.xlt-resource-gpu-node>strong {display:block;margin:0 0 6px}
.xlt-resource-gpu-cells {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,8rem),1fr));gap:6px;min-width:0}
.xlt-resource-gpu-cell {min-width:0;padding:6px;border:1px solid var(--xl-border);border-radius:6px;background:var(--xl-soft)}
.xlt-resource-gpu-cell>span {display:block;margin:0 0 4px;color:var(--xl-muted);font-size:var(--xl-supporting)}
.xlt-resource-gpu-cell .xlt-resource-cell {min-height:38px}
.xlt-inventory-summary strong {display:block;font-size:28px}
.xlt-inventory-summary span {color:var(--xl-muted)}
.xlt-inventory-summary>p {grid-column:1/-1;margin:0;color:var(--xl-muted)}
@container(max-width:600px) {.xlt-inventory-summary {grid-template-columns:1fr}.xlt-primary-kpis .xlt-kpi strong{font-size:22px}}

.xlt-storage-context {margin-top:20px;border-top:1px solid var(--xl-border);padding-top:14px}
.xlt-storage-context>summary {cursor:pointer;font-weight:600;color:var(--xl-secondary)}
.xlt-primary-kpis .xlt-kpi strong.xlt-kpi-state {font-size:15px}
.xlt-slow-steps button:focus-visible,.xlt-primary-action:focus-visible,.xlt-investigation-nav a:focus-visible {outline:2px solid var(--xl-primary);outline-offset:3px}
@container(max-width:600px) {
 .xlt-run-heading {display:block;margin-bottom:8px}
 .xlt-run-heading .xlt-header-actions {margin-top:8px}
 .xlt-content:has(.xlt-run-heading) {padding-top:15px}
 .xlt-primary-kpis .xlt-kpi {padding:12px;gap:3px}
 .xlt-context-meta {gap:4px}
}
.xlt-demo-link {display:block;margin:24px 12px 0;padding-top:12px;border-top:1px solid var(--xl-border);color:var(--xl-secondary);font-size:var(--xl-supporting)}
.xlt-demo-link small {display:block;color:var(--xl-muted)}
.xlt-scenario-layout {display:grid;grid-template-columns:minmax(200px,.75fr) minmax(0,1.6fr);gap:22px;align-items:start}
.xlt-scenario-picker {display:none}
.xlt-scenario-list {display:flex;flex-direction:column;gap:8px}
.xlt-scenario-list a {display:flex;flex-direction:column;gap:4px;border:1px solid var(--xl-border);border-radius:8px;padding:14px;background:var(--xl-bg);color:var(--xl-text)}
.xlt-scenario-list a[aria-current=page] {border-color:var(--xl-primary);background:var(--xl-selected);box-shadow:inset 3px 0 var(--xl-primary)}
.xlt-scenario-list small,.xlt-scenario-list span {color:var(--xl-muted)}
.xlt-scenario-selected {padding:24px;background:var(--xl-bg);border:1px solid var(--xl-border);border-radius:8px}
.xlt-scenario-selected h3 {font-size:25px;margin-top:8px}
.xlt-workflow-steps {display:flex;flex-wrap:wrap;list-style:none;padding:0;gap:14px;margin:22px 0}
.xlt-workflow-steps li {display:flex;gap:6px;align-items:center;font-size:13px}
.xlt-workflow-steps span {border-radius:50%;padding:2px 8px;background:var(--xl-selected);color:var(--xl-primary)}
.xlt-replays {padding:0;list-style:none;max-height:460px;overflow:auto}
.xlt-replays li {padding:12px 0;border-bottom:1px solid var(--xl-border)}
.xlt-replays small {display:block;color:var(--xl-muted);margin:5px 0}
@container(max-width:700px) {.xlt-scenario-layout {grid-template-columns:1fr}.xlt-scenario-list {display:none}.xlt-scenario-picker {display:grid;gap:8px}.xlt-scenario-selected{padding:16px}}
.xlt-kpi-label {display:flex;align-items:center;justify-content:space-between;gap:5px}
& .xlt-help {display:inline-flex;align-items:center;justify-content:center;padding:2px;border:0;background:transparent;color:var(--xl-muted);min-width:22px;min-height:22px}
.xlt-kpi-detail {font-size:var(--xl-supporting);color:var(--xl-muted);line-height:1.5;margin-top:auto}
.xlt-kpi-detail>summary {cursor:pointer;padding-top:4px}
.xlt-kpi-detail small {display:block;margin-top:6px;white-space:normal;overflow-wrap:anywhere}
.xlt-recorded-errors {color:var(--xl-danger)}
.xlt-help:focus-visible,.xlt-scenario-list a:focus-visible {outline:2px solid var(--xl-primary);outline-offset:2px}
.xlt-demo-link[aria-current=page] {color:var(--xl-text);border-left:3px solid var(--xl-primary);padding-left:9px}

.xlt-scenario-controls {display:flex;flex-wrap:wrap;align-items:center;gap:var(--xlt-space,12px);margin:20px 0}
.xlt-scenario-progress {display:flex;align-items:center;gap:12px;background:var(--xl-soft);border-radius:4px;padding:12px}
.xlt-scenario-progress:has(.xlt-scenario-progress-bar) {display:block}
.xlt-scenario-progress-caption {display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px}
.xlt-scenario-progress-caption small {color:var(--xl-muted);font-size:var(--xl-supporting)}
.xlt-scenario-progress-bar {position:relative;display:flex;gap:4px;overflow:hidden;height:8px;border-radius:4px;background:var(--xl-bg)}
.xlt-scenario-progress-bar>span {flex:1;background:var(--xl-border);border-radius:3px}
.xlt-scenario-progress-bar>span[data-state=passed] {background:var(--xl-teal)}
.xlt-scenario-progress-bar>span[data-state=failed] {background:var(--xl-danger)}
.xlt-scenario-progress-bar>span[data-state=running] {background:var(--xl-primary);animation:xlt-stage-pulse 1.6s ease-in-out infinite}
.xlt-scenario-progress-bar[data-waiting=true]::after {content:'';position:absolute;inset:0;width:20%;background:var(--xl-primary);opacity:.35;animation:xlt-stage-wait 1.8s ease-in-out infinite}
.xlt-scenario-progress>span:first-child {flex-shrink:0}
.xlt-validation-stages {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,10rem),1fr));gap:8px;margin:16px 0}
.xlt-validation-stages details {position:relative;overflow:hidden;background:var(--xl-soft);border:1px solid var(--xl-border);border-radius:4px;padding:10px;font-size:var(--xl-supporting);line-height:1.5}
.xlt-validation-stages details[data-state=passed] {border-color:var(--xl-teal)}
.xlt-validation-stages details[data-state=failed] {border-color:var(--xl-danger)}
.xlt-validation-stages details[data-state=running] {border-color:var(--xl-primary)}
.xlt-validation-stages summary,.xlt-validation-stages p,.xlt-validation-stages small {position:relative}
.xlt-stage-mark {display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border:1px solid currentColor;border-radius:50%;color:var(--xl-muted);vertical-align:middle}
.xlt-validation-stages details[data-state=passed] .xlt-stage-mark {color:var(--xl-teal)}
.xlt-validation-stages details[data-state=failed] .xlt-stage-mark {color:var(--xl-danger)}
.xlt-validation-stages details[data-state=running] .xlt-stage-mark {color:var(--xl-primary);border-top-color:transparent;animation:xlt-stage-spin 1s linear infinite}
.xlt-stage-flash {position:absolute;inset:0;pointer-events:none;background:color-mix(in srgb,var(--xl-teal) 24%,transparent);opacity:0;animation:xlt-stage-flash .85s ease-out both}
.xlt-validation-stages details[data-state=failed] .xlt-stage-flash {background:color-mix(in srgb,var(--xl-danger) 24%,transparent)}
.xlt-validation-stages b,.xlt-validation-stages small {display:block}
@keyframes xlt-stage-pulse {0%,100%{opacity:.45}50%{opacity:1}}
@keyframes xlt-stage-wait {0%{transform:translateX(-100%)}100%{transform:translateX(500%)}}
@keyframes xlt-stage-spin {to{transform:rotate(360deg)}}
@keyframes xlt-stage-flash {0%{opacity:0}20%{opacity:1}100%{opacity:0}}
@media(prefers-reduced-motion:reduce) {
 .xlt-scenario-progress-bar>span[data-state=running],.xlt-scenario-progress-bar[data-waiting=true]::after,.xlt-validation-stages details[data-state=running] .xlt-stage-mark,.xlt-stage-flash {animation:none}
}
.xlt-expected-results {margin:14px 0;font-size:var(--xl-supporting);line-height:1.5}
`;
