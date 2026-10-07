import type { PanelData } from "@grafana/data";
import { RecordRow, decodeRecord } from "./context";
import type { Sample } from "./semantics";
export function records(data: PanelData | undefined): RecordRow[] {
  const rows: RecordRow[] = [];
  for (const frame of data?.series || []) {
    const line = frame.fields.find(
      (f) => f.name === "Line" || f.name === "line",
    );
    if (!line) continue;
    for (let i = 0; i < frame.length; i++) {
      const row = decodeRecord(line.values[i]);
      if (row) rows.push(row);
    }
  }
  return [...new Map(rows.map((row) => [JSON.stringify(row), row])).values()];
}
export function samples(data: PanelData | undefined): Sample[] {
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
export function selectTargets(
  targets: RecordRow[],
  refs?: string[],
): RecordRow[] {
  return targets
    .filter((t) => !t.hide && (!refs || refs.includes(String(t.refId))))
    .map((t) => ({ ...t }));
}
