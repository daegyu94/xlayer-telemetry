import { RecordRow, numeric, scalar } from "./context";

export type PhaseWindow = {
  status: "observed" | "missing" | "ambiguous";
  start?: number;
  end?: number;
  node?: string;
  cluster?: string;
  observer?: string;
  accuracy?: string;
  reference?: string;
  uncertainty?: number;
  span?: RecordRow;
};
export type Sample = {
  value: number;
  unit: string;
  labels: Record<string, string>;
  time: number;
};
export type Cell = {
  binding: "phase-window" | "step-window" | "rolling-context" | "unmapped";
  state: "observed" | "no-data" | "missing" | "ambiguous";
  value?: number;
  unit?: string;
  scope: string;
  type: string;
  explanation: string;
  sample?: Sample;
  evidence?: RecordRow[];
  observations?: number;
};
export const PHASES = [
  "rollout",
  "reward",
  "actor_update",
  "weight_sync",
] as const;
export const SUBSYSTEMS = [
  "gpu",
  "vllm",
  "kv",
  "ray",
  "network",
  "storage",
  "sandbox",
] as const;
export function phaseWindow(
  spans: RecordRow[],
  step: RecordRow,
  phase: string,
): PhaseWindow {
  const start = numeric(step.window_start_ms),
    end = numeric(step.window_end_ms);
  const matches = spans.filter(
    (s) =>
      matchesStep(s, step) &&
      comparableStepClock(s, step) &&
      s.step !== undefined &&
      String(s.step) === String(step.step) &&
      s.phase === phase &&
      ["exact", "calibrated"].includes(String(s.boundary_accuracy)) &&
      numeric(s.start_time_ms) !== undefined &&
      numeric(s.end_time_ms) !== undefined &&
      Number(s.end_time_ms) > Number(s.start_time_ms) &&
      !(s.boundary_accuracy === "calibrated" && numeric(s.time_uncertainty_seconds)! < 0) &&
      (start === undefined || Number(s.start_time_ms) >= start) &&
      (end === undefined || Number(s.end_time_ms) <= end),
  );
  const unique = [
    ...new Map(matches.map((s) => [spanIdentity(s), s])).values(),
  ];
  if (!unique.length) return { status: "missing" };
  if (unique.length !== 1) return { status: "ambiguous" };
  const s = unique[0];
  return {
    status: "observed",
    start: Number(s.start_time_ms),
    end: Number(s.end_time_ms),
    node: scalar(s.node, ""),
    cluster: scalar(s.cluster || step.cluster, "") || undefined,
    observer: scalar(s.observer_node || step.observer_node, "") || undefined,
    accuracy: String(s.boundary_accuracy),
    reference: scalar(s.time_reference, "node clock"),
    uncertainty: numeric(s.time_uncertainty_seconds),
    span: s,
  };
}

function comparableStepClock(span: RecordRow, step: RecordRow): boolean {
  if (!span.node || !step.node) return false;
  if (["unknown", "clock_discontinuity"].includes(String(step.boundary_accuracy)) ||
      (step.time_alignment as RecordRow | undefined)?.status === "unknown") return false;
  if (numeric(step.window_start_ms) === undefined || numeric(step.window_end_ms) === undefined) return false;
  const spanCalibrated = span.boundary_accuracy === "calibrated";
  const stepCalibrated = String(step.boundary_accuracy).startsWith("calibrated");
  if (!spanCalibrated && !stepCalibrated) return span.node === step.node;
  if (!spanCalibrated || !stepCalibrated || !span.time_reference ||
      span.time_reference !== step.time_reference) return false;
  const a = numeric(span.time_uncertainty_seconds), b = numeric(step.time_uncertainty_seconds);
  const start = numeric(step.window_start_ms), end = numeric(step.window_end_ms);
  return a !== undefined && b !== undefined && a >= 0 && b >= 0 &&
    start !== undefined && end !== undefined &&
    Number(span.start_time_ms) - (a + b) * 1000 >= start &&
    Number(span.end_time_ms) + (a + b) * 1000 <= end;
}

function matchesStep(span: RecordRow, step: RecordRow): boolean {
  return !span.identity_conflict && !step.identity_conflict &&
    span.run_id === step.run_id && span.step !== undefined &&
    String(span.step) === String(step.step) &&
    (!step.cluster || span.cluster === step.cluster);
}
function spanIdentity(span: RecordRow): string {
  // A conflicting duplicate ID is ambiguous, rather than last-write-wins.
  const attributes = span.attributes as RecordRow | undefined;
  return JSON.stringify([span.cluster, span.observer_node, span.trace_id,
    span.span_id, span.parent_span_id, span.name, span.node, span.worker_id, span.producer, span.phase,
    span.start_time_ms, span.end_time_ms, span.boundary_accuracy,
    span.time_reference, span.time_uncertainty_seconds, span.role, span.rank, span.local_rank, span.gpu,
    span.duration_seconds, span.duration_source, span.policy_version, span.policy_version_source,
    attributes?.workload_fingerprint, attributes?.boundary_scope, attributes?.measurement_source]);
}
function clockComparable(window: PhaseWindow): boolean {
  return numeric(window.start) !== undefined && numeric(window.end) !== undefined && window.end!>window.start! &&
    (window.accuracy !== "calibrated" ||
    (window.uncertainty !== undefined && window.uncertainty >= 0 &&
      window.uncertainty * 2000 < window.end! - window.start!));
}
const SIGNALS: Record<string, RegExp> = {
  gpu: /gpu|flops/i,
  vllm: /vllm|ttft|queue|preemption/i,
  kv: /kv|prefix|mooncake/i,
  ray: /ray/i,
  network: /network|rdma|nccl|tx_wait/i,
  storage: /threefs|3fs|storage|disk|checkpoint|io_busy/i,
  sandbox: /sandbox|tool/i,
};
export function stepEvidenceCell(rows: RecordRow[], subsystem: string): Cell {
  const evidence = rows.filter((r) =>
    SIGNALS[subsystem]?.test(String(r.signal || r.component || "")),
  );
  return {
    binding: evidence.length ? "step-window" : "unmapped",
    state: evidence.length ? "observed" : "missing",
    scope: scalar(evidence[0]?.observation_scope, "Unknown"),
    type: "diagnosis projection",
    explanation: evidence.length
      ? "Selected Step evidence. Phase attribution and phase baseline are unavailable."
      : "No matching Step evidence.",
    evidence,
  };
}
export function metricCell(
  sample: Sample | undefined,
  window: PhaseWindow,
  scope: string,
  lookbackMs: number | boolean,
): Cell {
  const base: Cell = {
    binding: "unmapped",
    state: "no-data",
    scope,
    type: "sampled",
    explanation: "No sample in the selected interval.",
  };
  if (!sample) return base;
  if (!Number.isFinite(sample.value) || !Number.isFinite(sample.time)) return {...base,explanation:"A finite sample and evaluation timestamp are required."};
  if (window.status !== "observed")
    return {
      ...base,
      state: window.status,
      sample,
      explanation: "A unique measured phase interval is required.",
    };
  const node = sample.labels.node || sample.labels.nodename;
  if (
    !node ||
    !window.node ||
    node !== window.node ||
    (!!window.cluster && sample.labels.cluster !== window.cluster) ||
    sample.time < window.start! ||
    sample.time > window.end!
  )
    return {
      ...base,
      state: "missing",
      sample,
      explanation:
        "Resource identity or sample time does not match the measured phase.",
    };
  if (
    scope === "worker/cgroup" &&
    (!sample.labels.worker_id ||
      sample.labels.worker_id !== window.span?.worker_id ||
      sample.labels.run_id !== window.span?.run_id)
  ) {
    return {
      ...base,
      state: "missing",
      sample,
      explanation:
        "Worker/cgroup identity is not linked to this execution span. Same-node coincidence does not establish worker ownership.",
    };
  }
  // Calibration uncertainty can make short intervals incomparable. Never hide it.
  if (
    !clockComparable(window)
  )
    return {
      ...base,
      state: "missing",
      sample,
      explanation:
        "Calibration uncertainty is absent or exceeds this phase interval.",
    };
  if (lookbackMs)
    return {
      ...base,
      binding: "rolling-context",
      state: "observed",
      value: sample.value,
      unit: sample.unit,
      sample,
      explanation:
        typeof lookbackMs === "number"
          ? `Rolling ${lookbackMs / 1000}s observation evaluated inside the phase on ${node}; not exclusive to this phase. Correlation is not attribution.`
          : "Rolling query lookback extends beyond an evaluation point; not exclusive to this phase. Inspect the canonical query for its actual window. Correlation is not attribution.",
    };
  return {
    ...base,
    binding: "phase-window",
    state: "observed",
    value: sample.value,
    unit: sample.unit,
    sample,
    explanation: `Query evaluation inside phase on ${node}; ${window.accuracy} application boundary (${window.reference}). Raw scrape timestamp / clock calibration may be unknown. Correlation is not attribution.`,
  };
}
export function selectPhaseSample(
  values: Sample[],
  window: PhaseWindow,
): { sample?: Sample; entities: number } {
  if (window.status !== "observed") return { entities: 0 };
  const within = values.filter(
    (s) =>
      s.time >= window.start! &&
      s.time <= window.end! &&
      (!window.cluster || s.labels.cluster === window.cluster) &&
      (s.labels.node || s.labels.nodename) === window.node,
  );
  const entities = new Map<string, Sample>();
  for (const sample of within) {
    const key = JSON.stringify(Object.entries(sample.labels).sort());
    if (!entities.has(key) || entities.get(key)!.time < sample.time)
      entities.set(key, sample);
  }
  return {
    entities: entities.size,
    sample: entities.size === 1 ? [...entities.values()][0] : undefined,
  };
}

type SampleChoice = { sample?: Sample; entities: number; count: number };
function entityKey(labels: Record<string, string>): string {
  return JSON.stringify(Object.entries(labels).sort(([a], [b]) => a.localeCompare(b)));
}
function groupedSamples(values: Sample[], window: PhaseWindow, nodeRequired: boolean): Map<string, Sample[]> {
  const grouped = new Map<string, Sample[]>();
  if (window.status !== "observed") return grouped;
  for (const sample of values) {
    if (!Number.isFinite(sample.value) || !Number.isFinite(sample.time) ||
      sample.time < window.start! || sample.time > window.end! ||
      (window.cluster && sample.labels.cluster !== window.cluster) ||
      (nodeRequired && (sample.labels.node || sample.labels.nodename) !== window.node)) continue;
    const key = entityKey(sample.labels);
    grouped.set(key, [...(grouped.get(key) || []), sample]);
  }
  return grouped;
}

/** Arithmetic mean of gauge query evaluations; never use for quantiles/rates. */
export function gaugeSummary(values: Sample[], window: PhaseWindow): SampleChoice {
  const groups = groupedSamples(values, window, true);
  const count = [...groups.values()].reduce((n, points) => n + points.length, 0);
  if (groups.size !== 1) return {entities: groups.size, count};
  const points = [...groups.values()][0];
  const latest = [...points].sort((a,b) => b.time-a.time)[0];
  if (points.some(p => p.unit !== latest.unit)) return {entities: 1, count};
  return {sample: {...latest, value: points.reduce((sum,p) => sum+p.value, 0)/points.length}, entities: 1, count};
}

/** Session context keeps State/Session identity, but establishes no node ownership. */
export function contextSample(values: Sample[], window: PhaseWindow): SampleChoice {
  const groups = groupedSamples(values, window, false);
  const count = [...groups.values()].reduce((n,points) => n+points.length,0);
  return {entities:groups.size,count,sample:groups.size===1
    ? [...groups.values()][0].sort((a,b)=>b.time-a.time)[0] : undefined};
}

/** Follow observed parent IDs; same-node coincidence does not connect sandbox work. */
export function relatedPhaseWindow(
  spans: RecordRow[], selected: RecordRow, parent: PhaseWindow, subsystem: string,
): PhaseWindow {
  if (subsystem !== "sandbox" || parent.status !== "observed" || !parent.span?.trace_id || !parent.span.span_id || !clockComparable(parent)) return parent;
  const root = parent.span;
  const rows = [...new Map(spans.filter(s => matchesStep(s, selected) && s.trace_id === root.trace_id)
    .map(s => [spanIdentity(s), s])).values()];
  const sameCoordinate=(a:RecordRow,b:RecordRow)=>
    a.boundary_accuracy==="exact" && b.boundary_accuracy==="exact"
      ? !!a.node && a.node===b.node && (!a.time_reference || !b.time_reference || a.time_reference===b.time_reference)
      : a.boundary_accuracy==="calibrated" && b.boundary_accuracy==="calibrated" &&
        !!a.time_reference && a.time_reference===b.time_reference;
  const contained = (s: RecordRow, container: RecordRow) =>
    ["exact", "calibrated"].includes(String(s.boundary_accuracy)) &&
    numeric(s.start_time_ms) !== undefined && numeric(s.end_time_ms) !== undefined &&
    Number(s.end_time_ms)>Number(s.start_time_ms) &&
    Number(s.start_time_ms)>=Number(container.start_time_ms) && Number(s.end_time_ms)<=Number(container.end_time_ms) &&
    clockComparable({status:"observed",accuracy:String(s.boundary_accuracy),uncertainty:numeric(s.time_uncertainty_seconds),start:Number(s.start_time_ms),end:Number(s.end_time_ms)}) &&
    sameCoordinate(s,root) && sameCoordinate(s,container);
  const connected = (child: RecordRow): boolean => {
    let current=child; const visited=new Set<string>();
    for (let depth=0;depth<32;depth++) {
      if (!current.parent_span_id || visited.has(String(current.span_id))) return false;
      visited.add(String(current.span_id));
      const parents=rows.filter(s=>s.span_id===current.parent_span_id);
      if (parents.length!==1 || !contained(current,parents[0])) return false;
      if (parents[0].span_id===root.span_id) return spanIdentity(parents[0])===spanIdentity(root);
      current=parents[0];
    }
    return false;
  };
  const matches=rows.filter(s=>s.name==="sandbox.exec" && !!s.worker_id && contained(s,root) && connected(s));
  if (!matches.length) return parent;
  if (matches.length!==1) return {...parent,status:"ambiguous",span:undefined};
  const child=matches[0];
  return {...parent,start:Number(child.start_time_ms),end:Number(child.end_time_ms),node:scalar(child.node,""),
    accuracy:String(child.boundary_accuracy),reference:scalar(child.time_reference,"node clock"),
    uncertainty:numeric(child.time_uncertainty_seconds),span:child};
}

export type PhaseComparison = {baseline?:number;delta?:number;absolute?:number;reason:string;comparable:boolean};
/** A phase delta is a comparison of matching observations, not a causal claim. */
export function phaseComparison(
  current: Cell, baseline: Cell, currentWindow: PhaseWindow, baselineWindow: PhaseWindow,
  workloadComparability: string,
): PhaseComparison {
  const reject=(reason:string):PhaseComparison=>({comparable:false,reason});
  if (workloadComparability!=="matched_configured_fields") return reject("Workload comparability is not verified.");
  if (current.binding!=="phase-window" || baseline.binding!=="phase-window" ||
    current.state!=="observed" || baseline.state!=="observed") return reject("Only phase-window observations can be compared; rolling/session context is separate.");
  if (currentWindow.status!=="observed" || baselineWindow.status!=="observed" ||
    !clockComparable(currentWindow) || !clockComparable(baselineWindow)) return reject("Measured phase boundaries or clock quality are unavailable.");
  if(currentWindow.accuracy!==baselineWindow.accuracy || currentWindow.reference!==baselineWindow.reference) return reject("Clock reference or boundary accuracy differs.");
  const a=currentWindow.span,b=baselineWindow.span;
  if (!a || !b || ["run_id","node","worker_id","producer","phase"].some(k=>!a[k] || a[k]!==b[k]) ||
    currentWindow.cluster!==baselineWindow.cluster || currentWindow.observer!==baselineWindow.observer)
    return reject("Execution scope differs or its identity is incomplete.");
  const fingerprint=(a.attributes as RecordRow | undefined)?.workload_fingerprint;
  if (typeof fingerprint!=="string" || !fingerprint.trim() || fingerprint!==(b.attributes as RecordRow | undefined)?.workload_fingerprint)
    return reject("An identical explicit workload fingerprint is required.");
  if (!current.unit || current.unit!==baseline.unit || current.scope!==baseline.scope || current.type!==baseline.type ||
    !current.sample || !baseline.sample || entityKey(current.sample.labels)!==entityKey(baseline.sample.labels))
    return reject("Metric units, observation type, scope or resource entity differ.");
  if ((current.observations || 0)<2 || (baseline.observations || 0)<2) return reject("At least two gauge evaluations in each measured interval are required.");
  if (!Number.isFinite(current.value) || !Number.isFinite(baseline.value)) return reject("Finite measured values are required.");
  if (current.sample.time<currentWindow.start! || current.sample.time>currentWindow.end! ||
    baseline.sample.time<baselineWindow.start! || baseline.sample.time>baselineWindow.end! ||
    !Number.isFinite(current.sample.time) || !Number.isFinite(baseline.sample.time))
    return reject("Observation evaluation points must fall inside their own measured intervals.");
  const absolute=current.value!-baseline.value!;
  return {comparable:true,baseline:baseline.value,absolute,delta:baseline.value===0?undefined:100*absolute/baseline.value!,
    reason:baseline.value===0?"Comparable observation; relative delta unavailable because baseline is zero.":"Comparable gauge observations in verified measured phase windows; correlation is not attribution."};
}
