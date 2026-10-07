import { test } from "node:test";
import assert from "node:assert/strict";
import { records, samples, selectTargets } from "../src/data";
import { selectPhaseSample, phaseWindow } from "../src/semantics";
test("query reference selection preserves canonical datasource, labels and lookback", () => {
  const targets = [
    { refId: "A", expr: "canonical gauge", datasource: { uid: "remapped" } },
    { refId: "B", expr: "canonical rate[1m]" },
    { refId: "C", hide: true },
  ];
  const selected = selectTargets(targets, ["B"]);
  assert.deepEqual(selected, [targets[1]]);
  selected[0].expr = "changed";
  assert.equal(targets[1].expr, "canonical rate[1m]");
  assert.equal(selectTargets(targets).length, 2);
});
test("logs preserve zero, malformed/missing/duplicate rows remain distinct from measurement", () => {
  const line = JSON.stringify({
    cluster: "c",
    observer_node: "n",
    record: { current: 0, row_kind: "comparison", record_id: "s" },
  });
  const result = records({
    series: [
      {
        length: 4,
        fields: [
          {
            name: "Line",
            values: [
              line,
              line,
              "invalid",
              JSON.stringify({ record: { current: null } }),
            ],
          },
        ],
      },
    ],
  } as any);
  assert.equal(result.length, 2);
  assert.equal(result[0].current, 0);
  assert.equal(result[1].current, null);
  assert.deepEqual(records(undefined), []);
});
test("resource frames keep entity labels and evaluation time; never flatten multiple devices", () => {
  const data = {
    series: [
      {
        length: 3,
        fields: [
          { type: "time", values: [1000, 2000, 3000] },
          {
            type: "number",
            labels: { nodename: "n", gpu: "0" },
            config: { unit: "percent" },
            values: [0, null, NaN],
          },
        ],
      },
    ],
  };
  assert.deepEqual(samples(data as any), [
    {
      value: 0,
      time: 1000,
      unit: "percent",
      labels: { nodename: "n", gpu: "0" },
    },
  ]);
  const window = {
    status: "observed",
    start: 900,
    end: 1500,
    node: "n",
  } as const;
  assert.equal(selectPhaseSample(samples(data as any), window).entities, 1);
  assert.equal(
    selectPhaseSample(
      [
        ...samples(data as any),
        {
          value: 99,
          time: 1100,
          unit: "%",
          labels: { nodename: "n", gpu: "1" },
        },
      ],
      window,
    ).sample,
    undefined,
  );
});
test("calibrated windows retain uncertainty and ambiguous traces cannot be silently combined", () => {
  const step = {
    run_id: "r",
    step: 1,
    window_start_ms: 1,
    window_end_ms: 10000,
  };
  const span = {
    ...step,
    phase: "rollout",
    start_time_ms: 1000,
    end_time_ms: 3000,
    span_id: "a",
    trace_id: "t",
    node: "n",
    boundary_accuracy: "calibrated",
    time_reference: "monitoring",
    time_uncertainty_seconds: 0.01,
  };
  assert.equal(phaseWindow([span], step, "rollout").uncertainty, 0.01);
  assert.equal(
    phaseWindow([span, { ...span, trace_id: "other" }], step, "rollout").status,
    "ambiguous",
  );
});
