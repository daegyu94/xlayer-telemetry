import type { PanelData } from "@grafana/data";
import { RecordRow, decodeRecord } from "./context";
import type { Sample } from "./semantics";

function object(value: unknown): RecordRow | undefined {
  try {
    const parsed = typeof value === "string" ? JSON.parse(value) : value;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as RecordRow : undefined;
  } catch { return undefined; }
}

// Loki stream node identifies the observer, not an execution worker. Preserve
// explicit record identity and flag conflicting sources instead of replacing it.
function provenance(line: unknown, row: RecordRow, labels: unknown): RecordRow {
  const wrapper = object(line), body = object(wrapper?.record) || wrapper;
  const stream = object(labels);
  const result = {...row};
  const conflicts: string[] = [];
  for (const [key, candidates] of [
    ["cluster", [body?.cluster, wrapper?.cluster, stream?.cluster]],
    ["observer_node", [body?.observer_node, wrapper?.observer_node, stream?.node]],
  ] as const) {
    const values = candidates.filter((v): v is string => typeof v === "string" && !!v.trim());
    if (values.length) result[key] = values[0];
    if (new Set(values).size > 1) conflicts.push(key);
  }
  if (conflicts.length) {
    result.identity_conflict = true;
    result.identity_conflict_fields = conflicts.join(",");
  }
  return result;
}
export function records(data: PanelData | undefined): RecordRow[] {
  if (data?.state && !["Done", "Streaming"].includes(data.state)) return [];
  const rows: RecordRow[] = [];
  for (const frame of data?.series || []) {
    const line = frame.fields.find(
      (f) => f.name === "Line" || f.name === "line",
    );
    if (!line) continue;
    const labels = frame.fields.find(f => f.name.toLowerCase() === "labels");
    for (let i = 0; i < frame.length; i++) {
      const row = decodeRecord(line.values[i]);
      if (row) rows.push(provenance(line.values[i], row, labels?.values[i]));
    }
  }
  return [...new Map(rows.map((row) => [JSON.stringify(row), row])).values()];
}
export function samples(data: PanelData | undefined): Sample[] {
  if (data?.state && !["Done", "Streaming"].includes(data.state)) return [];
  const result: Sample[] = [];
  for (const frame of data?.series || []) {
    const time = frame.fields.find((f) => f.type === "time");
    for (const field of frame.fields.filter((f) => f.type === "number")) {
      for (let i = 0; i < frame.length; i++) {
        const value = field.values[i];
        if (typeof value === "number" && Number.isFinite(value))
          result.push({
            value,
            unit: field.config.unit || "",
            labels: field.labels || {},
            time: time
              ? Number(time.values[i])
              : Number(data?.timeRange?.to.valueOf()),
          });
      }
    }
  }
  return result;
}

/** Prometheus instant/table frames preserve declared labels as row fields. */
export function tableRows(data:PanelData|undefined):RecordRow[]{
 if(data?.state&&!["Done","Streaming"].includes(data.state))return [];
 return (data?.series||[]).flatMap(frame=>Array.from({length:frame.length},(_v,index)=>{
  const row:RecordRow={_refId:frame.refId};
  for(const field of frame.fields)row[field.name]=field.values[index];
  const value=frame.fields.find(f=>f.type==='number'&&f.name.startsWith('Value'));
  if(value)row.Value=value.values[index];
  return row;
 }));
}
export function selectTargets(
  targets: RecordRow[],
  refs?: string[],
): RecordRow[] {
  return targets
    .filter((t) => !t.hide && (!refs || refs.includes(String(t.refId))))
    .map((t) => ({ ...t }));
}
export function canonicalRefs(targets:RecordRow[],refs?:string[]):string[]{
 return selectTargets(targets,refs).map(target=>String(target.refId));
}
