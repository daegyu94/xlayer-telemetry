import { test } from "node:test";
import assert from "node:assert/strict";
import {
  timelineLanes,
  eventTime,
  latestEntitySamples,
  recordedSpanErrors,
  reportedRollout,
} from "../src/mockup";
import type { RecordRow } from "../src/context";
import type { Sample } from "../src/semantics";

test("event sorting prefers correlation time and keeps raw clock time as a fallback", () => {
  assert.equal(
    eventTime({
      event_time_unix_nano: 9_000_000,
      timestamp_unix_nano: 3_000_000,
    }),
    9,
  );
  assert.equal(eventTime({ timestamp_unix_nano: "3000000" }), 3);
  assert.equal(
    eventTime({ event_time_unix_nano: 0, timestamp_unix_nano: 3_000_000 }),
    0,
  );
});

test("unknown event time is never replaced with now or treated as measured zero", () => {
  for (const row of [
    {},
    { event_time_unix_nano: null },
    { event_time_unix_nano: "" },
    { event_time_unix_nano: "not-a-time" },
    { event_time_unix_nano: Infinity },
    { timestamp_unix_nano: false },
  ]) {
    assert.equal(eventTime(row), undefined);
  }
  assert.equal(
    eventTime({ event_time_unix_nano: null, timestamp_unix_nano: 3_000_000 }),
    3,
  );
});

test("latest entity snapshots preserve measured zero and do not use a range maximum", () => {
  const values: Sample[] = [
    { value: 99, unit: "%", labels: { node: "n", gpu: "0" }, time: 100 },
    { value: 0, unit: "%", labels: { node: "n", gpu: "0" }, time: 200 },
    { value: 40, unit: "%", labels: { node: "n", gpu: "0" }, time: 150 },
  ];
  const original = structuredClone(values);
  assert.deepEqual(latestEntitySamples(values), [values[1]]);
  assert.deepEqual(values, original);
});

test("entity identity uses every label and is independent of label insertion order", () => {
  const values: Sample[] = [
    {
      value: 10,
      unit: "%",
      labels: { node: "n", gpu: "0", cluster: "c" },
      time: 100,
    },
    {
      value: 20,
      unit: "%",
      labels: { cluster: "c", gpu: "0", node: "n" },
      time: 200,
    },
    {
      value: 30,
      unit: "%",
      labels: { node: "n", gpu: "1", cluster: "c" },
      time: 200,
    },
    {
      value: 40,
      unit: "%",
      labels: { node: "n", gpu: "0", cluster: "other" },
      time: 200,
    },
    {
      value: 50,
      unit: "requests",
      labels: { node: "n", gpu: "0", cluster: "c", engine: "e1" },
      time: 200,
    },
  ];
  const result = latestEntitySamples(values);
  assert.equal(result.length, 4);
  assert.deepEqual(
    result.map((sample) => sample.value).sort((a, b) => a - b),
    [20, 30, 40, 50],
  );
  assert.deepEqual(latestEntitySamples([]), []);
});

const span = (overrides: RecordRow = {}): RecordRow => ({
  record_type: "span",
  cluster: "c",
  observer_node: "collector",
  run_id: "r",
  trace_id: "trace",
  span_id: "span",
  status: "error",
  ...overrides,
});

test("recorded span errors count observed SDK errors without double-counting replayed records", () => {
  assert.deepEqual(
    recordedSpanErrors([
      span(),
      span(),
      span({ span_id: "ok", status: "ok" }),
      { record_type: "event", name: "worker.error", status: "error" },
    ]),
    { count: 1, observed: 2, total: 2, limited: false },
  );
});

test("span deduplication preserves cluster, observer, Run and trace boundaries", () => {
  const rows = [
    span(),
    span({ cluster: "other" }),
    span({ observer_node: "other" }),
    span({ run_id: "other" }),
    span({ trace_id: "other" }),
    span({ span_id: "other" }),
  ];
  assert.deepEqual(recordedSpanErrors(rows), {
    count: 6,
    observed: 6,
    total: 6,
    limited: false,
  });
});

test("span status coverage distinguishes no evidence from zero recorded errors", () => {
  assert.deepEqual(recordedSpanErrors([]), {
    count: undefined,
    observed: 0,
    total: 0,
    limited: false,
  });
  assert.deepEqual(recordedSpanErrors([span({ status: undefined })]), {
    count: undefined,
    observed: 0,
    total: 1,
    limited: false,
  });
  assert.deepEqual(recordedSpanErrors([span({ status: "ok" })]), {
    count: 0,
    observed: 1,
    total: 1,
    limited: false,
  });
});

test("unknown statuses remain visible in coverage without becoming errors or successful spans", () => {
  assert.deepEqual(
    recordedSpanErrors([
      span(),
      span({ span_id: "ok", status: "ok" }),
      span({ span_id: "unknown", status: "unknown" }),
    ]),
    { count: 1, observed: 2, total: 3, limited: false },
  );
});

test("canonical 5000-record cap is flagged rather than presented as complete error coverage", () => {
  const rows = Array.from({ length: 5000 }, (_, index) =>
    span({ span_id: String(index), status: "ok" }),
  );
  assert.equal(recordedSpanErrors(rows).limited, true);
  assert.equal(recordedSpanErrors(rows.slice(0, 4999)).limited, false);
});

test("reported rollout uses only the recorded rollout/gen duration and preserves zero", () => {
  assert.equal(reportedRollout({ stage_durations_seconds: { gen: 8 } }), 8);
  assert.equal(reportedRollout({ stage_durations_seconds: { rollout: 7 } }), 7);
  assert.equal(
    reportedRollout({ stage_durations_seconds: { rollout: 0, gen: 8 } }),
    0,
  );
  assert.equal(reportedRollout({ stage_durations_seconds: { gen: 0 } }), 0);
});

test("training durations and span boundaries never synthesize a reported rollout", () => {
  for (const row of [
    undefined,
    {},
    { stage_durations_seconds: { update_actor: 2, update_critic: 3 } },
    { start_time_ms: 1000, end_time_ms: 5000, phase: "rollout" },
    { step_duration_seconds: 7 },
    { stage_durations_seconds: { gen: null } },
    { stage_durations_seconds: { gen: NaN } },
    { stage_durations_seconds: { gen: Infinity } },
  ]) {
    assert.equal(reportedRollout(row), undefined);
  }
});

test("worker peer differences require same reported step, fresh age, and at least three comparable identity scopes", async () => {
  const { workerPeers } = await import("../src/mockup");
  const durations = [2.8, 3, 4.9, 3.2].map((value, i) => ({
    value,
    time: 100,
    unit: "s",
    labels: {
      cluster: "c",
      run_id: "r",
      producer: "p",
      role: "rollout",
      phase: "rollout",
      node: "n",
      worker_id: String(i),
    },
  }));
  const steps = durations.map((s) => ({
    ...s,
    value: 10,
    labels: Object.fromEntries(
      Object.entries(s.labels).filter(([k]) => k !== "phase"),
    ),
  }));
  const ages = steps.map((s) => ({ ...s, value: 1 }));
  const outliers = workerPeers(durations, steps, ages);
  assert.equal(outliers[0].sample.labels.worker_id, "2");
  assert.equal(outliers[0].peers, 4);
  assert.ok(outliers[0].delta! > 50);
  assert.equal(
    workerPeers(
      durations,
      steps.map((s, i) => ({ ...s, value: i === 2 ? 11 : 10 })),
      ages,
    ).find((r) => r.sample.labels.worker_id === "2")!.delta,
    undefined,
  );
  assert.equal(
    workerPeers(
      durations,
      steps,
      ages.map((s) => ({ ...s, value: 400 })),
    ).every((r) => r.delta === undefined),
    true,
  );
});

 test("Timeline lane presentation preserves interval values, accuracy and source fields",()=>{const frame={name:'rollout @ worker-0',fields:[{name:'Start Time',values:[1],config:{}},{name:'End Time',values:[2],config:{}},{name:'phase',values:['rollout [exact, node clock]'],config:{},state:{displayName:'Phase'}}]};const result=timelineLanes([frame])[0];assert.equal((result.fields[2].config as {displayName?:string}).displayName,'rollout');assert.equal((result.fields[2] as any).labels.Lane,frame.name);assert.equal(result.fields[2].values,frame.fields[2].values);assert.equal(result.fields[0],frame.fields[0]);assert.equal(frame.fields[2].state?.displayName,'Phase');});
