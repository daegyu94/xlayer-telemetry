import { numeric, type RecordRow, type Context } from './context';
import { latestEntitySamples, eventTime } from './mockup';
import { PHASES, phaseWindow, type Sample } from './semantics';

const attrs = (row: RecordRow): RecordRow =>
  row.attributes && typeof row.attributes === 'object' && !Array.isArray(row.attributes)
    ? row.attributes as RecordRow : {};

export function executionKey(row: RecordRow): string {
  return JSON.stringify(['cluster', 'node', 'producer', 'role', 'worker_id', 'rank', 'local_rank', 'gpu']
    .map(key => [key, row[key] ?? null]));
}

export function stepSpans(rows: RecordRow[], step: RecordRow): RecordRow[] {
  if (!step.cluster || !step.run_id || !Number.isSafeInteger(numeric(step.step)) || numeric(step.step)! < 0) return [];
  return [...new Map(rows.filter(row => !row.identity_conflict && row.record_type === 'span' &&
    row.run_id === step.run_id && row.cluster === step.cluster && String(row.step) === String(step.step))
    .map(row => [JSON.stringify(Object.entries(row).sort(([a], [b]) => a.localeCompare(b))), row])).values()];
}

export function executionChoices(rows: RecordRow[], step: RecordRow) {
  const groups = new Map<string, {key: string; row: RecordRow; count: number}>();
  for (const row of stepSpans(rows, step)) {
    const key = executionKey(row), existing = groups.get(key);
    groups.set(key, {key, row, count: (existing?.count || 0) + 1});
  }
  return [...groups.values()].sort((a, b) => String(a.row.node).localeCompare(String(b.row.node)) ||
    String(a.row.worker_id).localeCompare(String(b.row.worker_id)));
}

export function selectExecution(rows: RecordRow[], key?: string): RecordRow[] {
  return key ? rows.filter(row => executionKey(row) === key) : rows;
}

export function observedPhases(rows: RecordRow[], step: RecordRow, key?: string): string[] {
  const extra = new Set<string>();
  for (const span of selectExecution(stepSpans(rows, step), key)) {
    const phase = String(span.phase || '');
    if (phase && phase.length <= 64 && phaseWindow([span], step, phase).status === 'observed') extra.add(phase);
  }
  const order: string[] = [...PHASES, 'critic_update', 'reference_log_prob', 'reference',
    'advantage_estimation', 'checkpoint_save', 'checkpoint_load', 'policy_update', 'environment'];
  return [...order.filter(phase => key ? extra.has(phase) :
    (PHASES as readonly string[]).includes(phase) || extra.has(phase)),
    ...[...extra].filter(phase => !order.includes(phase)).sort()].slice(0, 16);
}

export function measuredWorkers(rows: RecordRow[], step: RecordRow) {
  const spans = stepSpans(rows, step);
  return executionChoices(spans, step).map(choice => {
    const own = selectExecution(spans, choice.key), window = phaseWindow(own, step, 'rollout');
    // A monotonic call duration survives an unaligned remote clock. It does not
    // permit a sampled metric query on the trainer's time axis.
    const calls = own.filter(span => span.phase === 'rollout' &&
      ['exact', 'calibrated'].includes(String(span.boundary_accuracy)) &&
      ['monotonic', 'injected_clock'].includes(String(span.duration_source)));
    const row = window.span || (calls.length === 1 ? calls[0] : undefined);
    const measured = row ? numeric(row.duration_seconds) : undefined;
    const duration = measured !== undefined && measured >= 0 ? measured : undefined;
    return {...choice, phases: new Set(own.map(span => span.phase)).size, window, duration,
      fingerprint: row ? attrs(row).workload_fingerprint : undefined,
      workload: row ? JSON.stringify(['prompt_tokens','output_tokens','turns','tool_calls','concurrency','applied_policy_version','replica_generation','execution_mode','model_identifier','serving_state']
        .map(key=>[key,attrs(row)[key]??null])) : undefined, operation: row?.name,
      scope: row ? attrs(row).boundary_scope : undefined, peers: 0,
      median: undefined as number | undefined, delta: undefined as number | undefined};
  }).map((row, _index, all) => {
    const peers = all.filter(peer => peer.duration !== undefined &&
      typeof peer.fingerprint === 'string' && peer.fingerprint && peer.fingerprint === row.fingerprint &&
      peer.workload === row.workload &&
      peer.operation && peer.operation === row.operation && peer.scope && peer.scope === row.scope &&
      peer.row.producer && peer.row.producer === row.row.producer && peer.row.role && peer.row.role === row.row.role);
    if (row.duration === undefined || peers.length < 3) return row;
    const sorted = peers.map(peer => peer.duration!).sort((a, b) => a - b), middle = Math.floor(sorted.length / 2);
    const median = sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
    return {...row, peers: peers.length, median, delta: median > 0 ? 100 * (row.duration - median) / median : undefined};
  });
}

export type PressureOptions = {
  kind: 'higher' | 'utilization' | 'task-state'; evidence?: RecordRow[];
  signal?: string; unit?: string; scale?: number;
};

function evidenceIdentity(row: RecordRow, sample: Sample): boolean {
  if (row.identity_conflict || !row.cluster || row.cluster !== sample.labels.cluster) return false;
  const start = numeric(row.window_start_ms), end = numeric(row.window_end_ms);
  if (start === undefined || end === undefined || end < start || sample.time < start || sample.time > end) return false;
  const pairs = String(row.entity || '').split(',');
  if (!row.entity || pairs.some(pair => !pair.includes('='))) return false;
  const expected = Object.fromEntries(pairs.map(pair => {
    const separator = pair.indexOf('='); return [pair.slice(0, separator), pair.slice(separator + 1)];
  }));
  const node = expected.node || expected.nodename;
  if (!node || node !== (sample.labels.node || sample.labels.nodename)) return false;
  const qualifiers = ['gpu', 'gpu_uuid', 'device', 'port', 'worker_id', 'instance', 'engine', 'engine_id']
    .filter(key => expected[key]);
  return qualifiers.length > 0 && qualifiers.every(key => expected[key] === sample.labels[key]) &&
    Object.entries(expected).every(([key, value]) => key === 'node' || key === 'nodename' || sample.labels[key] === value);
}

export function stepProjection(rows: RecordRow[], step?: RecordRow): RecordRow[] {
  if (!step || step.identity_conflict || !step.cluster || !step.record_id || !step.run_id) return [];
  const start = numeric(step.window_start_ms), end = numeric(step.window_end_ms);
  if (start === undefined || end === undefined || end <= start) return [];
  return rows.filter(row => !row.identity_conflict && row.cluster === step.cluster &&
    row.run_id === step.run_id && row.record_id === step.record_id &&
    numeric(row.window_start_ms) === numeric(step.window_start_ms) &&
    numeric(row.window_end_ms) === numeric(step.window_end_ms) &&
    (!step.observer_node || row.observer_node === step.observer_node));
}

export function pressureOrder(values: Sample[], options: PressureOptions) {
  return latestEntitySamples(values).filter(sample => Number.isFinite(sample.value) && Number.isFinite(sample.time))
    .map(sample => {
      const evidence = (options.evidence || []).filter(row => row.signal === options.signal &&
        evidenceIdentity(row, sample) && row.unit === options.unit);
      const baseline = evidence.length === 1 ? numeric(evidence[0].baseline) : undefined;
      const delta = baseline !== undefined && baseline !== 0
        ? 100 * (sample.value * (options.scale ?? 1) - baseline) / Math.abs(baseline) : undefined;
      const supporting = evidence.some(row => row.evidence_type === 'supporting');
      const pending = options.kind === 'task-state' && /^(PENDING|FAILED|RETRY)/.test(sample.labels.State || '');
      return {sample, baseline, delta, supporting, pending};
    }).sort((a, b) => Number(b.supporting) - Number(a.supporting) || Number(b.pending) - Number(a.pending) ||
      Math.abs(b.delta || 0) - Math.abs(a.delta || 0) ||
      (options.kind === 'utilization' ? a.sample.value - b.sample.value : b.sample.value - a.sample.value));
}

/** Applied policy coverage needs an explicit worker event, not trainer version/lag. */
export function appliedPolicies(rows: RecordRow[]) {
  return rows.filter(row => !row.identity_conflict && row.record_type === 'event' && row.name === 'weights.applied' &&
    attrs(row).policy_scope === 'worker_applied' && row.policy_version_source === 'producer_reported' &&
    typeof row.policy_version === 'number' && Number.isSafeInteger(row.policy_version) && row.policy_version >= 0 &&
    row.cluster && row.run_id && row.node && row.worker_id && eventTime(row) !== undefined)
    .sort((a, b) => eventTime(a)! - eventTime(b)!).slice(-100);
}

/** Resource selection changes only from an explicitly selected measured worker. */
export function workerContext(context: Context, row: RecordRow, key: string): Context {
  return {...context, variables: {...context.variables, phase_worker: [key],
    ...(row.node ? {node: [String(row.node)]} : {}),
    ...(row.gpu !== undefined && row.gpu !== null ? {gpu: [String(row.gpu)]} : {})}};
}

/** Per-resource drill-down preserves the selected Step's observer and Run. */
export function resourceContext(context: Context, labels: Record<string, string>): Context {
  const node = labels.node || labels.nodename;
  return {...context, variables: {...context.variables, ...(node ? {node: [node]} : {}),
    ...(labels.gpu ? {gpu: [labels.gpu]} : {}), ...(labels.device ? {device: [labels.device]} : {}),
    ...(labels.engine ? {engine: [labels.engine]} : {})}};
}
