/** Known observation fields at the saved-record boundary; additive fields survive. */
export type RecordRow = Record<string, unknown>;
export type ObservationStatus = 'observed' | 'no_data' | 'error' | 'stale' | 'unknown';
export type Measurement = number | Record<string, number | null> | null;
export type ObservationRow = RecordRow & {
  current?: Measurement;
  baseline?: Measurement;
  value?: Measurement;
  observation_status?: ObservationStatus;
  observation_scope?: string | null;
  clock_status?: string | null;
  reason_code?: string | null;
  resource_identity?: Record<string, string>;
};

export function numeric(value: unknown): number | undefined {
  if ((typeof value !== 'number' && typeof value !== 'string') ||
      (typeof value === 'string' && !value.trim())) return undefined;
  const number = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(number) ? number : undefined;
}

const STAT_FIELDS = ['min', 'mean', 'max', 'last', 'sample_count', 'sampled_increase', 'max_series_delta'];
function measurement(value: unknown): Measurement {
  const scalar = numeric(value);
  if (scalar !== undefined) return scalar;
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const entries = Object.entries(value);
    if (entries.length && entries.every(([key, item]) => STAT_FIELDS.includes(key) &&
        (item === null || typeof item === 'number' && Number.isFinite(item))))
      return {...value as Record<string, number | null>};
  }
  return null;
}
function measured(value: unknown): boolean {
  const decoded = measurement(value);
  return decoded !== null && (typeof decoded === 'number' || Object.values(decoded).some(item => item !== null));
}

export function observationStatus(row: RecordRow): ObservationStatus {
  const status = row.observation_status;
  if (status !== undefined) {
    if (status === 'observed') return !measured(row.current ?? row.value) ? 'unknown' : 'observed';
    return ['no_data', 'error', 'stale', 'unknown'].includes(String(status)) ? status as ObservationStatus : 'unknown';
  }
  return measured(row.current ?? row.value) ? 'observed' : 'unknown';
}

export function decodeObservationRow(row: RecordRow): ObservationRow | undefined {
  const result: ObservationRow = {...row};
  // Invalid identity must not become a usable scope merely through String coercion.
  for (const key of ['row_kind', 'cluster', 'observer_node', 'node', 'worker_id', 'run_id',
                     'signal', 'entity', 'observation_scope', 'unit', 'window_statistic',
                     'clock_status', 'reason_code']) {
    if (row[key] !== undefined && row[key] !== null && typeof row[key] !== 'string') return undefined;
  }
  for (const key of ['current', 'baseline', 'value']) {
    if (row[key] !== undefined) result[key] = measurement(row[key]);
  }
  for (const key of ['delta', 'delta_percent']) {
    if (row[key] !== undefined) result[key] = numeric(row[key]) ?? null;
  }
  if (row.observation_status !== undefined) result.observation_status = observationStatus(result);
  if (row.resource_identity !== undefined) {
    const identity = row.resource_identity;
    if (!identity || typeof identity !== 'object' || Array.isArray(identity) ||
        Object.values(identity).some(value => typeof value !== 'string')) return undefined;
    result.resource_identity = {...identity as Record<string, string>};
  }
  return result;
}

/** Saved evidence identity is independent of its observer/run context. */
export function resourceIdentity(row: RecordRow): Record<string, string> | undefined {
  if (row.resource_identity && typeof row.resource_identity === 'object' && !Array.isArray(row.resource_identity)) {
    const entries = Object.entries(row.resource_identity);
    return entries.length && entries.every(([, value]) => typeof value === 'string' && !!value)
      ? {...row.resource_identity as Record<string, string>} : undefined;
  }
  if (typeof row.entity !== 'string' || !row.entity) return undefined;
  const pairs = row.entity.split(',').map(pair => {
    const separator = pair.indexOf('=');
    return separator > 0 && separator < pair.length - 1 ? [pair.slice(0, separator), pair.slice(separator + 1)] : [];
  });
  if (pairs.some(pair => pair.length !== 2) || new Set(pairs.map(pair => pair[0])).size !== pairs.length) return undefined;
  return Object.fromEntries(pairs);
}

export function observationLabel(row: RecordRow): string | undefined {
  if (row.observation_status === undefined) return undefined;
  return {observed: undefined, no_data: 'No data', error: 'Query error', stale: 'Stale', unknown: 'Unknown'}[observationStatus(row)];
}
