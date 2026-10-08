import { test } from "node:test";
import assert from "node:assert/strict";
import { phaseWindow, stepEvidenceCell, metricCell, gaugeSummary, contextSample, relatedPhaseWindow, phaseComparison } from "../src/semantics";
const selected = {
  run_id: "r",
  node: "n",
  step: 127,
  record_id: "record",
  window_start_ms: 1000,
  window_end_ms: 20000,
};
const span = {
  run_id: "r",
  step: 127,
  phase: "rollout",
  node: "n",
  worker_id: "w",
  trace_id: "t",
  span_id: "s",
  boundary_accuracy: "exact",
  start_time_ms: 3000,
  end_time_ms: 11000,
};
test("reported stage duration never creates an execution interval", () => {
  assert.equal(phaseWindow([], selected, "rollout").status, "missing");
  assert.equal(
    phaseWindow(
      [{ ...span, boundary_accuracy: "unknown" }],
      selected,
      "rollout",
    ).status,
    "missing",
  );
});
test("phase requires explicit step identity and one unambiguous measured interval", () => {
  assert.equal(phaseWindow([span], selected, "rollout").status, "observed");
  assert.equal(
    phaseWindow([{ ...span, step: undefined }], selected, "rollout").status,
    "missing",
  );
  assert.equal(
    phaseWindow(
      [span, { ...span, worker_id: "other", span_id: "s2" }],
      selected,
      "rollout",
    ).status,
    "ambiguous",
  );
});
test("whole-step shared evidence remains step evidence, not phase attribution", () => {
  const cell = stepEvidenceCell(
    [
      {
        ...selected,
        row_kind: "evidence",
        signal: "threefs_p99_latency",
        current: 19,
        baseline: 8,
        unit: "ms",
        observation_scope: "shared-service",
      },
    ],
    "storage",
  );
  assert.equal(cell.binding, "step-window");
  assert.equal(cell.value, undefined);
  assert.equal(cell.scope, "shared-service");
});
test("sampled zero and rolling-window quality are explicit", () => {
  const window = phaseWindow([span], selected, "rollout");
  const zero = metricCell(
    {
      value: 0,
      unit: "requests",
      labels: { node: "n", instance: "e" },
      time: 10000,
    },
    window,
    "shared-service",
    0,
  );
  assert.equal(zero.value, 0);
  assert.equal(zero.type, "sampled");
  assert.equal(zero.binding, "phase-window");
  const rolling = metricCell(
    { value: 19, unit: "ms", labels: { node: "n" }, time: 10000 },
    window,
    "node",
    60000,
  );
  assert.equal(rolling.value, 19);
  assert.equal(rolling.binding, "rolling-context");
});

test("phase provenance excludes other clusters and explicit conflicts while permitting remote observers",()=>{
  const step={...selected,cluster:"c",observer_node:"observer"};
  const own={...span,cluster:"c",observer_node:"observer"};
  assert.equal(phaseWindow([own,{...own,cluster:"other"}],step,"rollout").status,"observed");
  assert.equal(phaseWindow([{...own,identity_conflict:true}],step,"rollout").status,"missing");
  assert.equal(phaseWindow([{...own,observer_node:"remote-rollout"}],step,"rollout").status,"observed");
  assert.equal(phaseWindow([own,{...own,observer_node:"remote-rollout"}],step,"rollout").status,"ambiguous");
  assert.equal(phaseWindow([{...own,boundary_accuracy:"calibrated",time_uncertainty_seconds:-1}],step,"rollout").status,"missing");
  assert.equal(phaseWindow([own,{...own,start_time_ms:4000}],step,"rollout").status,"ambiguous");
});

test("rolling numbers require matching cluster, node, evaluation window and clock quality",()=>{
  const window=phaseWindow([{...span,cluster:"c"}],{...selected,cluster:"c"},"rollout");
  const sample={value:0,unit:"percentunit",labels:{cluster:"c",node:"n"},time:10000};
  const cell=metricCell(sample,window,"shared-service",60000);
  assert.equal(cell.value,0);assert.equal(cell.binding,"rolling-context");assert.equal(cell.state,"observed");
  assert.equal(metricCell({...sample,labels:{cluster:"other",node:"n"}},window,"shared-service",60000).value,undefined);
  assert.equal(metricCell({...sample,time:12000},window,"shared-service",true).value,undefined);
  assert.equal(metricCell(sample,{...window,node:"other"},"shared-service",true).value,undefined);
  assert.equal(metricCell(sample,{...window,accuracy:"calibrated",uncertainty:-1},"shared-service",true).value,undefined);
  assert.equal(metricCell(sample,{...window,status:"missing"},"shared-service",true).value,undefined);
});

test("gauge summary averages points only inside one cluster/node/full entity and counts observations",()=>{
  const window=phaseWindow([{...span,cluster:"c"}],{...selected,cluster:"c"},"rollout");
  const sample={value:20,unit:"percent",labels:{cluster:"c",node:"n",gpu:"0"},time:4000};
  const result=gaugeSummary([sample,{...sample,value:40,time:8000},{...sample,value:999,time:12000},{...sample,value:999,labels:{...sample.labels,cluster:"other"}}],window);
  assert.equal(result.sample?.value,30);assert.equal(result.count,2);assert.equal(result.entities,1);
  const ambiguous=gaugeSummary([sample,{...sample,labels:{...sample.labels,gpu:"1"}}],window);
  assert.equal(ambiguous.sample,undefined);assert.equal(ambiguous.entities,2);
});

test("session context keeps latest state tuple and cluster/time without a node ownership claim",()=>{
  const window={status:"observed" as const,start:3000,end:11000,node:"n",cluster:"c"};
  const sample={value:2,unit:"short",labels:{cluster:"c",SessionName:"session",State:"PENDING_ARGS_AVAIL"},time:4000};
  const result=contextSample([sample,{...sample,value:4,time:9000},{...sample,value:99,time:12000}],window);
  assert.equal(result.sample?.value,4);assert.equal(result.count,2);
  assert.equal(contextSample([sample,{...sample,labels:{...sample.labels,State:"RUNNING"}}],window).sample,undefined);
});

test("sandbox worker mapping requires a measured child and complete same-trace parent chain",()=>{
  const step={...selected,cluster:"c",observer_node:"observer"};
  const parentSpan={...span,cluster:"c",observer_node:"observer"};
  const window=phaseWindow([parentSpan],step,"rollout");
  const tool={...parentSpan,phase:"environment",name:"tool.call",span_id:"tool",parent_span_id:"s",start_time_ms:4000,end_time_ms:10000};
  const child={...tool,name:"sandbox.exec",span_id:"exec",parent_span_id:"tool",worker_id:"pool-0",start_time_ms:5000,end_time_ms:9000};
  assert.equal(relatedPhaseWindow([parentSpan,tool,child],step,window,"sandbox").span?.worker_id,"pool-0");
  assert.equal(relatedPhaseWindow([parentSpan,child],step,window,"sandbox"),window);
  assert.equal(relatedPhaseWindow([parentSpan,tool,{...child,trace_id:"other"}],step,window,"sandbox"),window);
  assert.equal(relatedPhaseWindow([parentSpan,tool,child,{...child,span_id:"second"}],step,window,"sandbox").status,"ambiguous");
  assert.equal(relatedPhaseWindow([parentSpan,tool,{...child,end_time_ms:12000}],step,window,"sandbox"),window);
  assert.equal(relatedPhaseWindow([parentSpan,tool,{...child,boundary_accuracy:"calibrated",time_reference:"other-clock",time_uncertainty_seconds:0.01}],step,window,"sandbox"),window);
});

test("phase delta requires verified matching workload, entity, producer, boundaries and enough points",()=>{
  const parent={...span,producer:"p",attributes:{workload_fingerprint:"config:tokens:batch"}};
  const window=phaseWindow([parent],selected,"rollout");
  const baseWindow={...window,start:1000,end:2500,span:{...parent,step:126,start_time_ms:1000,end_time_ms:2500}};
  const sample={value:20,unit:"percent",labels:{node:"n",gpu:"0"},time:9000};
  const current={...metricCell(sample,window,"node",false),observations:3};
  const baseline={...current,value:10,sample:{...sample,value:10,time:2000}};
  const result=phaseComparison(current,baseline,window,baseWindow,"matched_configured_fields");
  assert.equal(result.comparable,true);assert.equal(result.delta,100);assert.equal(result.absolute,10);assert.equal(result.baseline,10);
  assert.equal(phaseComparison(current,baseline,window,baseWindow,"unverified").comparable,false);
  assert.equal(phaseComparison(current,baseline,window,baseWindow,"verified").comparable,false);
  assert.equal(phaseComparison({...current,observations:1},baseline,window,baseWindow,"matched_configured_fields").comparable,false);
  assert.equal(phaseComparison({...current,binding:"rolling-context"},baseline,window,baseWindow,"matched_configured_fields").comparable,false);
  assert.equal(phaseComparison(current,{...baseline,sample:{...sample,labels:{node:"n",gpu:"1"}}},window,baseWindow,"matched_configured_fields").comparable,false);
  assert.equal(phaseComparison(current,baseline,window,{...baseWindow,span:{...baseWindow.span,producer:"other"}},"matched_configured_fields").comparable,false);
  assert.equal(phaseComparison(current,baseline,window,{...baseWindow,span:{...baseWindow.span,attributes:{}}},"matched_configured_fields").comparable,false);
  assert.equal(phaseComparison(current,{...baseline,unit:"short"},window,baseWindow,"matched_configured_fields").comparable,false);
  assert.equal(phaseComparison(current,{...baseline,sample:{...baseline.sample,time:3000}},window,baseWindow,"matched_configured_fields").comparable,false);
  const zero=phaseComparison(current,{...baseline,value:0,sample:{...baseline.sample,value:0}},window,baseWindow,"matched_configured_fields");
  assert.equal(zero.comparable,true);assert.equal(zero.delta,undefined);assert.equal(zero.absolute,20);
});
test("phase comparison cannot combine different clock references",()=>{
 const currentWindow={status:"observed" as const,reference:"monitor-a",accuracy:"calibrated",start:1,end:10,uncertainty:0,span:{}};
 const baselineWindow={...currentWindow,reference:"monitor-b"};
 const cell={binding:"phase-window" as const,state:"observed" as const,type:"sampled",scope:"node",explanation:"test"};
 assert.equal(phaseComparison(cell,cell,currentWindow,baselineWindow,"matched_configured_fields").comparable,false);
});
test("different resource identity or no sample cannot be mapped into phase", () => {
  const window = phaseWindow([span], selected, "rollout");
  assert.equal(
    metricCell(
      {
        value: 90,
        unit: "percent",
        labels: { nodename: "other" },
        time: 10000,
      },
      window,
      "node",
      0,
    ).value,
    undefined,
  );
  assert.equal(metricCell(undefined, window, "node", 0).state, "no-data");
});

test("worker/cgroup samples require explicit worker and Run identity, unlike shared node correlation", () => {
  const window = phaseWindow([span], selected, "rollout");
  const sample = {
    value: 0.2,
    unit: "ratio",
    labels: { node: "n", run_id: "r", worker_id: "other" },
    time: 10000,
  };
  assert.equal(
    metricCell(sample, window, "worker/cgroup", false).value,
    undefined,
  );
  assert.equal(
    metricCell(
      { ...sample, labels: { ...sample.labels, worker_id: "w" } },
      window,
      "worker/cgroup",
      false,
    ).value,
    0.2,
  );
  assert.match(
    metricCell(sample, window, "shared-service", true).explanation,
    /actual window/,
  );
});

test('remote node clocks require an explicit shared calibration before phase query mapping',()=>{
 const step={...selected,boundary_accuracy:'approximate'};
 const remote={...span,node:'remote'};
 assert.equal(phaseWindow([remote],step,'rollout').status,'missing');
 const calibratedStep={...step,boundary_accuracy:'calibrated_approximate',time_reference:'monitor',time_uncertainty_seconds:.01};
 const calibrated={...remote,boundary_accuracy:'calibrated',time_reference:'monitor',time_uncertainty_seconds:.01};
 assert.equal(phaseWindow([calibrated],calibratedStep,'rollout').status,'observed');
 for(const change of [{time_reference:'other'},{time_uncertainty_seconds:null},{start_time_ms:1005}])assert.equal(phaseWindow([{...calibrated,...change}],calibratedStep,'rollout').status,'missing');
 assert.equal(phaseWindow([span],step,'rollout').status,'observed');
});
