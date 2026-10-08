import { numeric, type RecordRow } from './context';

export function storageMetrics(rows: RecordRow[]): string[] {
  return [...new Set(rows.filter(row => row.row_kind === 'storage_sample')
    .map(row => row.metric_name).filter((name): name is string => typeof name === 'string' && !!name))].sort();
}
type PlotState = 'ready' | 'select-metric' | 'no-data' | 'mixed-source' | 'mixed-unit' | 'multiple-owner';
export function storagePlotSelection(rows: RecordRow[], metric?: string): {
  state: PlotState; points: RecordRow[]; unit?: string;
} {
  const reject = (state: PlotState) => ({state,points:[]});
  if (!metric) return reject('select-metric');
  const selected = rows.filter(row => row.row_kind === 'storage_sample' && row.window_role === 'current' && row.metric_name === metric);
  const points = selected.filter(row => !row.identity_conflict && row.ambiguous_sample !== true && row.ambiguous_sample !== 'true' &&
    numeric(row.sample_timestamp_ms) !== undefined && numeric(row.sample_value) !== undefined && typeof row.series_key === 'string' && !!row.series_key);
  if (!points.length) return reject('no-data');
  if (new Set(points.map(row => row.source_table)).size !== 1) return reject('mixed-source');
  const units = new Set(points.map(row => typeof row.unit === 'string' && row.unit ? row.unit : undefined));
  if (units.size !== 1) return reject('mixed-unit');
  if (new Set(points.map(row => JSON.stringify([row.cluster,row.run_id,row.record_id,row.window_start_ms,row.window_end_ms]))).size !== 1)
    return reject('multiple-owner');
  return {state:'ready',points,unit:[...units][0]};
}
