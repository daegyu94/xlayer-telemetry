export const APP_ID = "xlayer-telemetry-app";
export const APP_BASE = `/a/${APP_ID}`;
export const VARIABLE_NAMES = [
  "cluster",
  "run_id",
  "source_node",
  "node",
  "record_id",
  "candidate_id",
  "phase_worker",
  "matrix_gpu_entity",
  "matrix_vllm_entity",
  "matrix_kv_entity",
  "matrix_ray_entity",
  "matrix_network_entity",
  "matrix_storage_entity",
  "matrix_sandbox_entity",
  "trace_id",
  "diagnosis_method",
  "log_run_id",
  "gpu",
  "engine",
  "sandbox_node",
  "phase",
  "role",
  "worker",
  "device",
  "mount",
  "storage_system",
  "storage_node",
  "ssd",
  "training_max_age",
] as const;
export type VariableName = (typeof VARIABLE_NAMES)[number];
export type Context = {
  variables: Partial<Record<VariableName, string[]>>;
  from: string;
  to: string;
  timezone: string;
};
export type RecordRow = Record<string, unknown>;
export function readContext(search: string): Context {
  const params = new URLSearchParams(search);
  const variables: Context["variables"] = {};
  for (const name of VARIABLE_NAMES) {
    const values = params.getAll(`var-${name}`);
    if (values.length) variables[name] = values;
  }
  return {
    variables,
    from: params.get("from") || "now-30m",
    to: params.get("to") || "now",
    timezone: params.get("timezone") || "browser",
  };
}
export function writeContext(context: Context): URLSearchParams {
  const params = new URLSearchParams({
    from: context.from,
    to: context.to,
    timezone: context.timezone,
  });
  for (const name of VARIABLE_NAMES)
    for (const value of context.variables[name] || [])
      params.append(`var-${name}`, value);
  return params;
}
export function appLink(page: string, context: Context): string {
  return `${APP_BASE}/${page}?${writeContext(context)}`;
}
export function sceneTime(value: string): string {
  // SceneTimeRange constructor accepts date math / ISO, not numeric URL epochs.
  return /^\d+$/.test(value) ? new Date(Number(value)).toISOString() : value;
}
/** URL ordering and native default filters do not change the investigation. */
export function investigationKey(context: Context): string {
  const normalizeTime = (value: string) => {
    const parsed = /^\d+$/.test(value) ? Number(value) : /^\d{4}-/.test(value) ? Date.parse(value) : NaN;
    return Number.isFinite(parsed) ? String(parsed) : value;
  };
  return JSON.stringify([normalizeTime(context.from), normalizeTime(context.to), context.timezone,
    VARIABLE_NAMES.map(name => [name, (context.variables[name]?.length ? context.variables[name]! : ['.*'])
      .map(value => value === '$__all' ? '.*' : value).sort()])]);
}
export const DASHBOARD_UIDS = {
  overview: "telemetry-overview",
  summary: "xlayer-bottleneck-summary",
  timeline: "xlayer-cross-layer-timeline",
  stage: "agent-rl-stage-correlation",
  compute: "xlayer-compute-communication",
  storage: "xlayer-data-storage",
  logs: "xlayer-run-logs",
  signals: "xlayer-workspace-overview",
} as const;
export type Destination = keyof typeof DASHBOARD_UIDS;
export function subsystemDestination(subsystem: string): Destination {
  return ["gpu", "compute", "network"].includes(subsystem)
    ? "compute"
    : subsystem === "storage"
      ? "storage"
      : "stage";
}
export function dashboardLink(
  destination: Destination,
  context: Context,
): string {
  const params = writeContext(context);
  if (destination === "logs") {
    params.delete("var-run_id");
    params.set(
      "var-telemetry_run_id",
      runSelectionRegex(context.variables.run_id),
    );
    const logs = context.variables.log_run_id;
    params.set(
      "var-run_id",
      !logs?.length || logs.includes("$__all") || logs.includes(".*")
        ? ".*"
        : logs.length === 1
          ? logs[0]
          : `(?:${logs.join("|")})`,
    );
  }
  return `/d/${DASHBOARD_UIDS[destination]}?${params}`;
}
export function runSelectionRegex(values: string[] | undefined): string {
  if (!values?.length || values.some((v) => v === "$__all" || v === ".*"))
    return ".*";
  const escaped = values.map((v) => v.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return escaped.length === 1 ? escaped[0] : `(?:${escaped.join("|")})`;
}
export function selectStep(row: RecordRow, context: Context): Context {
  const from = numeric(row.window_start_ms),
    to = numeric(row.window_end_ms);
  if (
    from === undefined ||
    to === undefined ||
    to <= from ||
    !row.record_id ||
    !row.run_id
  )
    throw new Error("Step has no usable identity/time window");
  return {
    ...context,
    from: String(from),
    to: String(to),
    variables: {
      ...context.variables,
      cluster: [String(row.cluster)],
      run_id: [String(row.run_id)],
      source_node: [String(row.observer_node || row.node)],
      record_id: [String(row.record_id)],
      trace_id: [".*"],
      candidate_id: [],
    },
  };
}
export function numeric(value: unknown): number | undefined {
  if (
    (typeof value !== "number" && typeof value !== "string") ||
    (typeof value === "string" && !value.trim())
  )
    return undefined;
  if (
    value === null ||
    value === undefined ||
    value === "" ||
    typeof value === "boolean"
  )
    return undefined;
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : undefined;
}
export function scalar(value: unknown, fallback = "Unknown"): string {
  return value === undefined ||
    value === null ||
    value === "" ||
    value === "null"
    ? fallback
    : String(value);
}
export function decodeRecord(line: unknown): RecordRow | undefined {
  try {
    const value = typeof line === "string" ? JSON.parse(line) : line;
    if (!value || typeof value !== "object" || Array.isArray(value))
      return undefined;
    const wrapper = value as RecordRow;
    let record = wrapper.record;
    if (typeof record === "string") record = JSON.parse(record);
    if (record && typeof record === "object" && !Array.isArray(record))
      return {
        ...(record as RecordRow),
        cluster: wrapper.cluster,
        observer_node: wrapper.observer_node,
      };
    return wrapper;
  } catch {
    return undefined;
  }
}
