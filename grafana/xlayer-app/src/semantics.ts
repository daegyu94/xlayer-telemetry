import { RecordRow, numeric, scalar } from "./context";

export type PhaseWindow = {
  status: "observed" | "missing" | "ambiguous";
  start?: number;
  end?: number;
  node?: string;
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
      s.run_id === step.run_id &&
      s.step !== undefined &&
      String(s.step) === String(step.step) &&
      s.phase === phase &&
      ["exact", "calibrated"].includes(String(s.boundary_accuracy)) &&
      numeric(s.start_time_ms) !== undefined &&
      numeric(s.end_time_ms) !== undefined &&
      Number(s.end_time_ms) > Number(s.start_time_ms) &&
      (start === undefined || Number(s.start_time_ms) >= start) &&
      (end === undefined || Number(s.end_time_ms) <= end),
  );
  const unique = [
    ...new Map(matches.map((s) => [`${s.trace_id}/${s.span_id}`, s])).values(),
  ];
  if (!unique.length) return { status: "missing" };
  if (unique.length !== 1) return { status: "ambiguous" };
  const s = unique[0];
  return {
    status: "observed",
    start: Number(s.start_time_ms),
    end: Number(s.end_time_ms),
    node: scalar(s.node, ""),
    accuracy: String(s.boundary_accuracy),
    reference: scalar(s.time_reference, "node clock"),
    uncertainty: numeric(s.time_uncertainty_seconds),
    span: s,
  };
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
  if (lookbackMs)
    return {
      ...base,
      binding: "rolling-context",
      state: "missing",
      sample,
      explanation:
        typeof lookbackMs === "number"
          ? `Rolling ${lookbackMs / 1000}s observation; not exclusive to this phase.`
          : "Rolling query lookback extends beyond an evaluation point; not exclusive to this phase. Inspect the canonical query for its actual window.",
    };
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
    window.accuracy === "calibrated" &&
    (window.uncertainty === undefined ||
      window.uncertainty * 2000 >= window.end! - window.start!)
  )
    return {
      ...base,
      state: "missing",
      sample,
      explanation:
        "Calibration uncertainty is absent or exceeds this phase interval.",
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
