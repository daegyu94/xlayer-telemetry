import { test } from "node:test";
import assert from "node:assert/strict";
import { phaseWindow, stepEvidenceCell, metricCell } from "../src/semantics";
const selected = {
  run_id: "r",
  step: 127,
  record_id: "record",
  window_start_ms: 1000,
  window_end_ms: 20000,
};
const span = {
  run_id: "r",
  step: 127,
  phase: "rollout",
  node: "n",
  worker_id: "w",
  trace_id: "t",
  span_id: "s",
  boundary_accuracy: "exact",
  start_time_ms: 3000,
  end_time_ms: 11000,
};
test("reported stage duration never creates an execution interval", () => {
  assert.equal(phaseWindow([], selected, "rollout").status, "missing");
  assert.equal(
    phaseWindow(
      [{ ...span, boundary_accuracy: "unknown" }],
      selected,
      "rollout",
    ).status,
    "missing",
  );
});
test("phase requires explicit step identity and one unambiguous measured interval", () => {
  assert.equal(phaseWindow([span], selected, "rollout").status, "observed");
  assert.equal(
    phaseWindow([{ ...span, step: undefined }], selected, "rollout").status,
    "missing",
  );
  assert.equal(
    phaseWindow(
      [span, { ...span, worker_id: "other", span_id: "s2" }],
      selected,
      "rollout",
    ).status,
    "ambiguous",
  );
});
test("whole-step shared evidence remains step evidence, not phase attribution", () => {
  const cell = stepEvidenceCell(
    [
      {
        ...selected,
        row_kind: "evidence",
        signal: "threefs_p99_latency",
        current: 19,
        baseline: 8,
        unit: "ms",
        observation_scope: "shared-service",
      },
    ],
    "storage",
  );
  assert.equal(cell.binding, "step-window");
  assert.equal(cell.value, undefined);
  assert.equal(cell.scope, "shared-service");
});
test("sampled zero and rolling-window quality are explicit", () => {
  const window = phaseWindow([span], selected, "rollout");
  const zero = metricCell(
    {
      value: 0,
      unit: "requests",
      labels: { node: "n", instance: "e" },
      time: 10000,
    },
    window,
    "shared-service",
    0,
  );
  assert.equal(zero.value, 0);
  assert.equal(zero.type, "sampled");
  assert.equal(zero.binding, "phase-window");
  const rolling = metricCell(
    { value: 19, unit: "ms", labels: { node: "n" }, time: 10000 },
    window,
    "node",
    60000,
  );
  assert.equal(rolling.value, undefined);
  assert.equal(rolling.binding, "rolling-context");
});
test("different resource identity or no sample cannot be mapped into phase", () => {
  const window = phaseWindow([span], selected, "rollout");
  assert.equal(
    metricCell(
      {
        value: 90,
        unit: "percent",
        labels: { nodename: "other" },
        time: 10000,
      },
      window,
      "node",
      0,
    ).value,
    undefined,
  );
  assert.equal(metricCell(undefined, window, "node", 0).state, "no-data");
});

test("worker/cgroup samples require explicit worker and Run identity, unlike shared node correlation", () => {
  const window = phaseWindow([span], selected, "rollout");
  const sample = {
    value: 0.2,
    unit: "ratio",
    labels: { node: "n", run_id: "r", worker_id: "other" },
    time: 10000,
  };
  assert.equal(
    metricCell(sample, window, "worker/cgroup", false).value,
    undefined,
  );
  assert.equal(
    metricCell(
      { ...sample, labels: { ...sample.labels, worker_id: "w" } },
      window,
      "worker/cgroup",
      false,
    ).value,
    0.2,
  );
  assert.match(
    metricCell(sample, window, "shared-service", true).explanation,
    /actual window/,
  );
});
