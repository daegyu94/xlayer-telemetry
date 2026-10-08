import { numeric, type RecordRow } from "./context";
import type { Sample } from "./semantics";

export type EventStepResolution = {
  state: "matched" | "ambiguous" | "unlinked";
  step?: RecordRow;
  candidates: RecordRow[];
  reason: string;
};

const text = (value: unknown): string | undefined =>
  typeof value === "string" && value.trim() ? value : undefined;
const attributes = (row: RecordRow): RecordRow =>
  row.attributes && typeof row.attributes === "object" && !Array.isArray(row.attributes)
    ? row.attributes as RecordRow : {};
const explicitStepIds = (row: RecordRow): string[] =>
  // record_id identifies the event/span itself unless a source explicitly maps
  // it; only step_record_id declares a relation to a completed Step artifact.
  [row.step_record_id, attributes(row).step_record_id]
    .filter((value): value is string => text(value) !== undefined);
const invalidStepReference = (row: RecordRow): boolean =>
  [[row, "step_record_id"], [attributes(row), "step_record_id"]]
    .some(([container, key]) => Object.prototype.hasOwnProperty.call(container, key as string) &&
      text((container as RecordRow)[key as string]) === undefined);
const sameCoordinateIdentity = (a: RecordRow, b: RecordRow): boolean =>
  !!text(a.node) && a.node === b.node && !!text(a.worker_id) && a.worker_id === b.worker_id &&
  ["observer_node", "role", "rank", "local_rank", "gpu"].every(key =>
    a[key] === undefined || a[key] === null || b[key] === undefined || b[key] === null ||
    String(a[key]) === String(b[key]));
const sameRunStep = (a: RecordRow, b: RecordRow): boolean =>
  !a.identity_conflict && !b.identity_conflict && !!text(a.cluster) && a.cluster === b.cluster &&
  !!text(a.run_id) && a.run_id === b.run_id && Number.isSafeInteger(numeric(a.step)) && numeric(a.step)! >= 0 &&
  numeric(a.step) === numeric(b.step);
const rowKey = (row: RecordRow): string =>
  JSON.stringify(Object.entries(row).sort(([a], [b]) => a.localeCompare(b)));
const deduplicate = (rows: RecordRow[]): RecordRow[] =>
  [...new Map(rows.map(row => [rowKey(row), row])).values()];
const validStep = (row: RecordRow): boolean => {
  const start = numeric(row.window_start_ms), end = numeric(row.window_end_ms);
  return !!text(row.record_id) && start !== undefined && end !== undefined && end > start &&
    !["unknown", "clock_discontinuity"].includes(String(row.boundary_accuracy)) &&
    (row.time_alignment as RecordRow | undefined)?.status !== "unknown";
};

type Coordinate = { state: "comparable"; uncertainty: number } | { state: "unknown" } | { state: "conflict" };
/** Raw node times are comparable only on one node; calibrated times need one reference. */
function coordinates(event: RecordRow, step: RecordRow): Coordinate {
  const a = String(event.boundary_accuracy || "node_clock"), b = String(step.boundary_accuracy || "approximate");
  if (["unknown", "clock_discontinuity"].includes(a) || (event.time_alignment as RecordRow | undefined)?.status === "unknown")
    return { state: "unknown" };
  if (a.startsWith("calibrated") || b.startsWith("calibrated")) {
    const ea = numeric(event.time_uncertainty_seconds), sa = numeric(step.time_uncertainty_seconds);
    if (!a.startsWith("calibrated") || !b.startsWith("calibrated") || !text(event.time_reference) ||
      event.time_reference !== step.time_reference || ea === undefined || sa === undefined || ea < 0 || sa < 0)
      return { state: "conflict" };
    return { state: "comparable", uncertainty: (ea + sa) * 1000 };
  }
  if (!text(event.node) || event.node !== step.node) return { state: "unknown" };
  if (text(event.time_reference) && text(step.time_reference) && event.time_reference !== step.time_reference)
    return { state: "conflict" };
  return { state: "comparable", uncertainty: 0 };
}

function eventWithinStep(event: RecordRow, step: RecordRow): boolean {
  const coordinate = coordinates(event, step);
  if (coordinate.state === "conflict") return false;
  // Identity/parent links can identify a Step without asserting a clock alignment.
  if (coordinate.state === "unknown") return true;
  const nanos = numeric(event.event_time_unix_nano) ?? numeric(event.timestamp_unix_nano);
  const point = nanos === undefined ? numeric(event.start_time_ms) : nanos / 1_000_000;
  if (point === undefined) return true;
  return point - coordinate.uncertainty >= Number(step.window_start_ms) &&
    point + coordinate.uncertainty <= Number(step.window_end_ms);
}

/** Explicit parent IDs connect execution entities; sharing a trace ID is insufficient. */
function observedParentLink(event: RecordRow, step: RecordRow, spans: RecordRow[]): boolean {
  const trace = text(event.trace_id), anchor = text(event.span_id) || text(event.parent_span_id);
  if (!trace || !anchor) return false;
  const rows = deduplicate(spans.filter(span => sameRunStep(span, step) && span.trace_id === trace && text(span.span_id)));
  let id: string | undefined = anchor;
  const seen = new Set<string>();
  for (let depth = 0; id && depth < 64; depth++) {
    if (seen.has(id)) return false;
    seen.add(id);
    const matches = rows.filter(span => span.span_id === id);
    if (matches.length !== 1) return false;
    const span = matches[0];
    if (depth === 0 && ["node", "worker_id"].some(key => text(event[key]) && text(span[key]) && event[key] !== span[key])) return false;
    const ids = explicitStepIds(span);
    if (invalidStepReference(span) || (ids.length && (new Set(ids).size !== 1 || ids[0] !== step.record_id))) return false;
    const rootId = text(step.span_id);
    if (rootId && step.trace_id === trace && span.span_id === rootId) return true;
    if (ids[0] === step.record_id) return true;
    if (sameCoordinateIdentity(span, step)) {
      const start = numeric(span.start_time_ms), end = numeric(span.end_time_ms), coordinate = coordinates(span, step);
      if (coordinate.state === "comparable" && start !== undefined && end !== undefined && end > start &&
        start - coordinate.uncertainty >= Number(step.window_start_ms) && end + coordinate.uncertainty <= Number(step.window_end_ms)) return true;
    }
    id = text(span.parent_span_id);
  }
  return false;
}

/** Resolve a navigation target without equating timestamp coincidence with execution. */
export function resolveEventStep(event: RecordRow, steps: RecordRow[], spans: RecordRow[] = []): EventStepResolution {
  const reject = (reason: string): EventStepResolution => ({ state: "unlinked", candidates: [], reason });
  if (event.identity_conflict) return reject("Event identity conflicts with its record/stream provenance.");
  if (invalidStepReference(event)) return reject("An explicit Step record reference is malformed; no implicit fallback is permitted.");
  const ids = explicitStepIds(event);
  if (new Set(ids).size > 1) return reject("Explicit Step record references conflict.");
  const candidates = deduplicate(steps.filter(step => validStep(step) && sameRunStep(event, step) &&
    (!ids.length || ids[0] === step.record_id) &&
    eventWithinStep(event, step) &&
    (ids.length > 0 || sameCoordinateIdentity(event, step) || observedParentLink(event, step, spans))));
  if (!candidates.length) return reject("No unique execution identity or observed parent relation links this event to a usable Step.");
  if (candidates.length > 1) return { state: "ambiguous", candidates, reason: "Multiple completed Step records match the execution context; choose one explicitly." };
  const selected = candidates[0];
  if (!ids.length && text(event.producer) && text(selected.producer) && event.producer !== selected.producer &&
    !observedParentLink(event, selected, spans))
    return reject("Producer provenance differs; an explicit step_record_id or observed parent relation is required across SDK/bridge sources.");
  return { state: "matched", candidates, step: selected, reason: ids.length
    ? "An explicit step_record_id links this event to the completed Step. Unaligned clocks are not compared and the link does not establish causality."
    : "Linked by explicit execution identity or observed parent relation; correlation does not establish causality." };
}

export type KpiSelectionOptions = {
  scope: "application" | "resource";
  ownerRun?: string;
  selectedRuns?: string[];
  worker?: string | string[];
  node?: string | string[];
  phase?: string;
  ages?: Sample[];
  requireFreshness?: boolean;
  maxAgeSeconds?: number;
  /** Query evaluation time, not wall-clock now for a historical pinned interval. */
  evaluationTime?: number;
  ageIdentityKeys?: string[];
};
export type KpiEntityResolution = {
  state: "observed" | "multiple" | "no-data" | "freshness-unknown" | "stale" | "invalid";
  sample?: Sample;
  entities: Sample[];
  age?: number;
  reason: string;
};
const entityKey = (sample: Sample): string => JSON.stringify(Object.entries(sample.labels).sort(([a], [b]) => a.localeCompare(b)));
function literalSelection(value: string | string[] | undefined): string[] {
  const list = typeof value === "string" ? [value] : value || [];
  return list.some(item => item === ".*" || item === "$__all") ? [] : list.filter(item => !!item.trim());
}

/** Latest is defined within a full label tuple, never across entities or units. */
export function resolveKpiEntity(values: Sample[], options: KpiSelectionOptions): KpiEntityResolution {
  const reject = (state: KpiEntityResolution["state"], reason: string, entities: Sample[] = [], age?: number): KpiEntityResolution =>
    ({ state, reason, entities, ...(age === undefined ? {} : { age }) });
  const runs = literalSelection(options.ownerRun ? [options.ownerRun] : options.selectedRuns),
    workers = literalSelection(options.worker), nodes = literalSelection(options.node);
  const groups = new Map<string, Sample[]>();
  for (const value of values) {
    if (!Number.isFinite(value.value) || !Number.isFinite(value.time) ||
      (options.phase && value.labels.phase !== options.phase) ||
      (options.scope === "application" && (!text(value.labels.run_id) || (runs.length && !runs.includes(value.labels.run_id)))) ||
      (workers.length && !workers.includes(value.labels.worker_id)) ||
      (nodes.length && !nodes.includes(value.labels.node || value.labels.nodename))) continue;
    const key = entityKey(value);
    groups.set(key, [...(groups.get(key) || []), value]);
  }
  const entities: Sample[] = [];
  for (const points of groups.values()) {
    const latest = [...points].sort((a, b) => b.time - a.time)[0];
    if (points.some(point => point.unit !== latest.unit) ||
      points.some(point => point.time === latest.time && point.value !== latest.value))
      return reject("invalid", "Conflicting values or units for the same entity and evaluation time.");
    entities.push(latest);
  }
  entities.sort((a, b) => entityKey(a).localeCompare(entityKey(b)));
  if (!entities.length) return reject("no-data", "No finite observation matches the selected scope and identity.");
  if (entities.length !== 1) return reject("multiple", "Multiple entities are selected; no implicit aggregation or representative value is defined.", entities);
  const selected = entities[0];
  const requireFreshness = options.requireFreshness ?? options.ages !== undefined;
  if (!requireFreshness) return { state: "observed", sample: selected, entities, reason: "One explicit entity; freshness is not assessed by this source." };
  const ownership = [...new Set(["cluster", "run_id", "node", "nodename", "instance", "producer", "role", "worker_id", "local_rank", "rank",
    ...(options.ageIdentityKeys || [...Object.keys(selected.labels), ...(options.ages || []).flatMap(value => Object.keys(value.labels))])])]
    .filter(key => !["__name__", "phase"].includes(key));
  const matching = (options.ages || []).filter(value => Number.isFinite(value.time) &&
    ownership.every(key => value.labels[key] === selected.labels[key]));
  const ageGroups = new Map<string, Sample[]>();
  for (const value of matching) {
    const key = entityKey(value);
    ageGroups.set(key, [...(ageGroups.get(key) || []), value]);
  }
  if (ageGroups.size !== 1) return reject("freshness-unknown", "A unique age observation with the same producer/worker identity is required.", entities);
  const points = [...ageGroups.values()][0], latestAge = [...points].sort((a, b) => b.time - a.time)[0];
  const evaluation = options.evaluationTime ?? selected.time;
  if (!Number.isFinite(evaluation) || !Number.isFinite(latestAge.value) || latestAge.value < 0 || latestAge.time > evaluation ||
    points.some(point => point.time === latestAge.time && point.value !== latestAge.value))
    return reject("freshness-unknown", "Age is invalid, conflicting or evaluated after the selected observation.", entities);
  const age = latestAge.value + (evaluation - latestAge.time) / 1000;
  const limit = options.maxAgeSeconds ?? 300;
  if (!Number.isFinite(limit) || limit < 0) return reject("invalid", "Freshness threshold must be finite and nonnegative.", entities);
  if (age > limit) return reject("stale", "The producer observation is stale; its value is withheld.", entities, age);
  return { state: "observed", sample: selected, entities, age, reason: "One explicit entity with a matching fresh producer age." };
}
