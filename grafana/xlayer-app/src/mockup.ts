import { RecordRow, numeric } from "./context";
import type { Sample } from "./semantics";
export function eventTime(row: RecordRow): number | undefined {
  const nanos =
    numeric(row.event_time_unix_nano) ?? numeric(row.timestamp_unix_nano);
  return nanos === undefined ? undefined : nanos / 1_000_000;
}
export function latestEntitySamples(values: Sample[]): Sample[] {
  const latest = new Map<string, Sample>();
  for (const sample of values) {
    const key = JSON.stringify(Object.entries(sample.labels).sort());
    if (!latest.has(key) || latest.get(key)!.time < sample.time)
      latest.set(key, sample);
  }
  return [...latest.values()];
}
export function recordedSpanErrors(rows: RecordRow[]): {
  count?: number;
  observed: number;
  total: number;
  limited: boolean;
} {
  const spans = [
    ...new Map(
      rows
        .filter((r) => r.record_type === "span")
        .map((r) => [
          JSON.stringify([
            r.cluster,
            r.observer_node,
            r.run_id,
            r.trace_id,
            r.span_id,
          ]),
          r,
        ]),
    ).values(),
  ];
  const known = spans.filter((r) => r.status === "ok" || r.status === "error");
  return {
    count: known.length
      ? known.filter((r) => r.status === "error").length
      : undefined,
    observed: known.length,
    total: spans.length,
    limited: spans.length >= 5000,
  };
}
export function reportedRollout(
  row: RecordRow | undefined,
): number | undefined {
  const stages = row?.stage_durations_seconds as RecordRow | undefined;
  return numeric(stages?.rollout) ?? numeric(stages?.gen);
}
export function workerPeers(
  durations: Sample[],
  steps: Sample[],
  ages: Sample[],
  maxAge = 300,
): {
  sample: Sample;
  step?: number;
  age?: number;
  peers: number;
  median?: number;
  delta?: number;
}[] {
  const key = (s: Sample) =>
    JSON.stringify(
      Object.entries(s.labels)
        .filter(([k]) => !["__name__", "phase"].includes(k))
        .sort(),
    );
  const stepMap = new Map(latestEntitySamples(steps).map((s) => [key(s), s])),
    ageMap = new Map(latestEntitySamples(ages).map((s) => [key(s), s]));
  const rows = latestEntitySamples(durations)
    .filter((s) => s.labels.phase === "rollout")
    .map((sample) => ({
      sample,
      step: stepMap.get(key(sample))?.value,
      age: ageMap.get(key(sample))?.value,
      peers: 0,
      median: undefined as number | undefined,
      delta: undefined as number | undefined,
    }));
  const groups = new Map<string, typeof rows>();
  for (const r of rows) {
    if (r.step === undefined || r.age === undefined || r.age > maxAge) continue;
    const l = r.sample.labels,
      g = JSON.stringify([
        l.cluster,
        l.run_id,
        l.producer,
        l.role,
        l.phase,
        l.node || l.nodename,
        l.engine,
        r.step,
      ]);
    groups.set(g, [...(groups.get(g) || []), r]);
  }
  for (const group of groups.values()) {
    if (group.length < 3) continue;
    const values = group.map((r) => r.sample.value).sort((a, b) => a - b),
      mid = Math.floor(values.length / 2),
      median =
        values.length % 2 ? values[mid] : (values[mid - 1] + values[mid]) / 2;
    for (const r of group) {
      r.peers = group.length;
      r.median = median;
      r.delta =
        median > 0 ? (100 * (r.sample.value - median)) / median : undefined;
    }
  }
  return rows.sort((a, b) => (b.delta ?? -Infinity) - (a.delta ?? -Infinity));
}

/** Presentation only: interval values and full lane identity are unchanged. */
export function timelineLanes<T extends {name?:string;fields:any[]}>(frames:T[]):T[]{
  return frames.map(frame=>({...frame,fields:frame.fields.map(field=>{
    const lane=frame.name||field.labels?.Lane;
    if(!['Phase','phase'].includes(field.name)||!lane)return field;
    const [phase,operation]=lane.split(' @ ')[0].split(' / ');
    const label=({actor_update:'training · actor',weight_sync:'weight sync',checkpoint_save:'checkpoint'} as Record<string,string>)[phase]|| (phase==='environment'?`tool · ${operation?.split('.').pop()||'call'}`:phase);
    return {...field,name:label,labels:{...field.labels,Lane:lane},config:{...field.config,displayName:label},state:undefined};
  })}));
}
