import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveEventStep, resolveKpiEntity } from "../src/selection";
import type { RecordRow } from "../src/context";
import type { Sample } from "../src/semantics";

const step = (extra: RecordRow = {}): RecordRow => ({
  cluster: "c", observer_node: "collector", run_id: "r", step: 42,
  record_id: "step-w0", node: "node-a", worker_id: "w0",
  boundary_accuracy: "approximate", window_start_ms: 1000, window_end_ms: 5000,
  ...extra,
});
const event = (extra: RecordRow = {}): RecordRow => ({
  cluster: "c", observer_node: "collector", run_id: "r", step: 42,
  node: "node-a", worker_id: "w0", timestamp_unix_nano: 3_000_000_000,
  ...extra,
});
const sample = (extra: Partial<Sample> = {}): Sample => ({
  value: 0.7, unit: "short", time: 10000,
  labels: { cluster: "c", run_id: "r", node: "node-a", worker_id: "w0", producer: "trainer", role: "trainer", local_rank: "0" },
  ...extra,
});
const age = (extra: Partial<Sample> = {}): Sample => sample({ value: 2, unit: "s", ...extra });

test("events choose the matching worker, never the first same Run/Step", () => {
  const own = step(), other = step({ record_id: "step-w1", worker_id: "w1" });
  const result = resolveEventStep(event(), [other, own]);
  assert.equal(result.state, "matched"); assert.equal(result.step, own);
});
test("cluster, observer and explicit identity conflicts prevent accidental joins", () => {
  for (const other of [step({ cluster: "other" }), step({ identity_conflict: true }), step({ observer_node: "other" })]) {
    assert.equal(resolveEventStep(event(), [other]).state, "unlinked");
  }
  assert.equal(resolveEventStep(event({ identity_conflict: true }), [step()]).state, "unlinked");
  assert.equal(resolveEventStep(event({ cluster: undefined }), [step()]).state, "unlinked");
});
test("explicit Step record IDs are strict and cannot fall back to another candidate", () => {
  assert.equal(resolveEventStep(event({ attributes: { step_record_id: "missing" } }), [step()]).state, "unlinked");
  assert.equal(resolveEventStep(event({ step_record_id: "wrong" }), [step()]).state, "unlinked");
  assert.equal(resolveEventStep(event({ step_record_id: "step-w0", attributes: { step_record_id: "conflict" } }), [step()]).state, "unlinked");
  assert.equal(resolveEventStep(event({ step_record_id: "step-w0" }), [step()]).state, "matched");
  for (const value of [null, false, 42, ""]) {
    assert.equal(resolveEventStep(event({ attributes: { step_record_id: value } }), [step()]).state, "unlinked");
  }
});
test("an event's own record ID is not a completed Step reference", () => {
  assert.equal(resolveEventStep(event({ record_id: "event-own-id" }), [step()]).state, "matched");
  assert.equal(resolveEventStep(event({ record_id: "event-own-id", step_record_id: "missing-step" }), [step()]).state, "unlinked");
});
test("explicit Step references connect SDK and bridge producers without inventing a timestamp relation", () => {
  const trainer = step({ producer: "verl", role: "trainer" });
  const sdk = event({ producer: "verl_native", role: "trainer", attributes: { step_record_id: "step-w0" } });
  assert.equal(resolveEventStep(sdk, [trainer]).state, "matched");
  const remote = { ...sdk, node: "rollout-node", worker_id: "rollout-0", role: "rollout", boundary_accuracy: "unknown", timestamp_unix_nano: 90_000_000_000 };
  assert.equal(resolveEventStep(remote, [trainer]).state, "matched");
  assert.equal(resolveEventStep({ ...remote, cluster: "other" }, [trainer]).state, "unlinked");
  assert.equal(resolveEventStep({ ...remote, step: 43 }, [trainer]).state, "unlinked");
  const calibrated = { ...sdk, boundary_accuracy: "calibrated", time_reference: "monitor-b", time_uncertainty_seconds: 0.01 };
  assert.equal(resolveEventStep(calibrated, [{ ...trainer, boundary_accuracy: "calibrated_approximate", time_reference: "monitor-a", time_uncertainty_seconds: 0.01 }]).state, "unlinked");
});
test("producer identity never silently resolves multiple same-worker Step records", () => {
  const bridge = step({ producer: "verl" }), sdk = step({ record_id: "sdk-copy", producer: "verl_native" });
  assert.equal(resolveEventStep(event({ producer: "verl_native" }), [bridge, sdk]).state, "ambiguous");
  const unmatched = resolveEventStep(event({ producer: "verl_native" }), [bridge]);
  assert.equal(unmatched.state, "unlinked"); assert.match(unmatched.reason, /producer/i);
  assert.equal(resolveEventStep(event({ producer: "verl_native", step_record_id: "step-w0" }), [bridge, sdk]).state, "matched");
});
test("one full identity can still have repeated Step records: preserve ambiguity", () => {
  const first = step(), second = step({ record_id: "repeated" });
  const result = resolveEventStep(event(), [first, second, first]);
  assert.equal(result.state, "ambiguous"); assert.equal(result.step, undefined);
  assert.deepEqual(result.candidates, [first, second]);
});
test("valid intervals are required, comparable event time cannot escape the Step", () => {
  for (const row of [step({ window_start_ms: null }), step({ window_end_ms: 0 }), step({ boundary_accuracy: "unknown" })]) {
    assert.equal(resolveEventStep(event(), [row]).state, "unlinked");
  }
  assert.equal(resolveEventStep(event({ timestamp_unix_nano: 6_000_000_000 }), [step()]).state, "unlinked");
  assert.equal(resolveEventStep(event({ timestamp_unix_nano: 0 }), [step({ window_start_ms: 0 })]).state, "matched");
  for (const value of [-1, 1.5, false, Number.MAX_SAFE_INTEGER + 1]) {
    assert.equal(resolveEventStep(event({ step: value }), [step({ step: value })]).state, "unlinked");
  }
});
test("calibrated event and Step coordinates require the same reference and uncertainty coverage", () => {
  const calibrated = step({ boundary_accuracy: "calibrated_approximate", time_reference: "monitor", time_uncertainty_seconds: 0.01 });
  const own = event({ boundary_accuracy: "calibrated", time_reference: "monitor", time_uncertainty_seconds: 0.01, event_time_unix_nano: 3_000_000_000 });
  assert.equal(resolveEventStep(own, [calibrated]).state, "matched");
  assert.equal(resolveEventStep({ ...own, time_reference: "other" }, [calibrated]).state, "unlinked");
  assert.equal(resolveEventStep({ ...own, time_uncertainty_seconds: undefined }, [calibrated]).state, "unlinked");
  assert.equal(resolveEventStep({ ...own, event_time_unix_nano: 1_005_000_000 }, [calibrated]).state, "unlinked");
});
test("unknown clocks never use raw time to select one of repeated identities", () => {
  const result = resolveEventStep(event({ boundary_accuracy: "unknown", timestamp_unix_nano: 2_000_000_000 }), [step(), step({ record_id: "later", window_start_ms: 10000, window_end_ms: 12000 })]);
  assert.equal(result.state, "ambiguous");
  assert.equal(resolveEventStep(event({ node: "node-b", worker_id: "remote", boundary_accuracy: "unknown" }), [step()]).state, "unlinked");
});
test("cross-node navigation requires an actual same-trace parent chain, not a shared trace alone", () => {
  const root = { ...event(), record_type: "span", trace_id: "t", span_id: "root", boundary_accuracy: "exact", start_time_ms: 1500, end_time_ms: 4500 };
  const child = { ...root, node: "node-b", worker_id: "remote", span_id: "child", parent_span_id: "root", start_time_ms: 2000, end_time_ms: 4000 };
  const remote = event({ node: "node-b", worker_id: "remote", trace_id: "t", span_id: "child", boundary_accuracy: "unknown" });
  assert.equal(resolveEventStep(remote, [step()], [root, child]).state, "matched");
  assert.equal(resolveEventStep({ ...remote, span_id: undefined }, [step()], [root, child]).state, "unlinked");
  assert.equal(resolveEventStep(remote, [step()], [child]).state, "unlinked");
  assert.equal(resolveEventStep(remote, [step()], [root, { ...child, trace_id: "different" }]).state, "unlinked");
  assert.equal(resolveEventStep(remote, [step()], [root, child, { ...root, worker_id: "conflict" }]).state, "unlinked");
});
test("KPI keeps every entity label and never presents the most recent entity as a total", () => {
  const a = sample(), b = sample({ time: 11000, labels: { ...a.labels, worker_id: "w1" } });
  const result = resolveKpiEntity([a, b], { scope: "application", ownerRun: "r" });
  assert.equal(result.state, "multiple"); assert.equal(result.sample, undefined); assert.equal(result.entities.length, 2);
  assert.equal(resolveKpiEntity([a, b], { scope: "application", ownerRun: "r", worker: "w1" }).sample, b);
});
test("explicit application Run selection excludes other Runs while multi-Run stays multiple", () => {
  const a = sample(), b = sample({ labels: { ...a.labels, run_id: "other" } });
  assert.equal(resolveKpiEntity([a, b], { scope: "application", ownerRun: "r" }).sample, a);
  assert.equal(resolveKpiEntity([a, b], { scope: "application", selectedRuns: ["r", "other"] }).state, "multiple");
  assert.equal(resolveKpiEntity([sample({ labels: { node: "node-a" } })], { scope: "application", ownerRun: "r" }).state, "no-data");
});
test("shared resource KPI never invents a Run owner or filters an absent Run label", () => {
  const resource = sample({ labels: { cluster: "c", node: "node-a", gpu: "0" }, unit: "percent" });
  const result = resolveKpiEntity([resource], { scope: "resource", ownerRun: "r", selectedRuns: ["r"] });
  assert.equal(result.state, "observed"); assert.equal(result.sample, resource); assert.equal(result.sample?.labels.run_id, undefined);
});
test("absent, conflicting or stale age hides a current application KPI", () => {
  assert.equal(resolveKpiEntity([sample()], { scope: "application", ages: [] }).state, "freshness-unknown");
  assert.equal(resolveKpiEntity([sample()], { scope: "application", ages: [age({ labels: { ...sample().labels, producer: "other" } })] }).sample, undefined);
  const result = resolveKpiEntity([sample()], { scope: "application", ages: [age({ value: 301 })], maxAgeSeconds: 300 });
  assert.equal(result.state, "stale"); assert.equal(result.sample, undefined); assert.equal(result.age, 301);
});
test("age identity is strict for cluster, Run, node aliases, worker, producer and rank", () => {
  for (const key of ["cluster", "run_id", "node", "nodename", "worker_id", "producer", "role", "local_rank"]) {
    const mismatch = age({ labels: { ...sample().labels, [key]: "other" } });
    assert.equal(resolveKpiEntity([sample()], { scope: "application", ages: [mismatch] }).state, "freshness-unknown", key);
    assert.equal(resolveKpiEntity([sample()], { scope: "application", ages: [mismatch], ageIdentityKeys: [] }).state, "freshness-unknown", key);
  }
});
test("same entity age preserves fresh measured zero and phase dimensions do not change ownership", () => {
  const value = sample({ value: 0, labels: { ...sample().labels, phase: "rollout" } });
  const result = resolveKpiEntity([value], { scope: "application", ages: [age({ value: 0 })], phase: "rollout" });
  assert.equal(result.state, "observed"); assert.equal(result.sample?.value, 0); assert.equal(result.age, 0);
});
test("the age query evaluation cannot make an old producer sample fresh", () => {
  const result = resolveKpiEntity([sample()], { scope: "application", ages: [age({ value: 2, time: 0 })], evaluationTime: 400000, maxAgeSeconds: 300 });
  assert.equal(result.state, "stale"); assert.equal(result.sample, undefined); assert.equal(result.age, 402);
  assert.equal(resolveKpiEntity([sample()], { scope: "application", ages: [age({ value: -1 })] }).state, "freshness-unknown");
});
test("latest-per-entity selection is immutable and rejects conflicting values or units", () => {
  const old = sample({ value: 100, time: 1000 }), latest = sample({ value: 0 }), original = structuredClone([old, latest]);
  assert.equal(resolveKpiEntity([old, latest], { scope: "application" }).sample, latest);
  assert.deepEqual([old, latest], original);
  assert.equal(resolveKpiEntity([latest, { ...latest, value: 1 }], { scope: "application" }).state, "invalid");
  assert.equal(resolveKpiEntity([old, { ...latest, unit: "percent" }], { scope: "application" }).state, "invalid");
});

test('numeric rank and GPU identities cannot collide within the same node/worker', () => {
  for (const key of ['rank', 'local_rank', 'gpu']) {
    assert.equal(resolveEventStep(event({[key]:0}),[step({[key]:1})]).state,'unlinked');
    assert.equal(resolveEventStep(event({[key]:0}),[step({[key]:'0'})]).state,'matched');
  }
});

test('canonical observer selection does not change an application sample execution identity',()=>{
 const value=sample({labels:{...sample().labels,node:'rollout-node',nodename:'observer-node'}});
 const result=resolveKpiEntity([value],{scope:'application',selectedRuns:['r'],ages:[age({labels:value.labels})]});
 assert.equal(result.state,'observed');assert.equal(result.sample?.labels.node,'rollout-node');assert.equal(result.sample?.labels.nodename,'observer-node');
});
