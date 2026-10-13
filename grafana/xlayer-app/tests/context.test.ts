import { test } from "node:test";
import assert from "node:assert/strict";
import { resourceIdentity, observationStatus, observationLabel } from "../src/record-contract";
import {
  readContext,
  investigationKey,
  writeContext,
  dashboardLink,
  selectStep,
  numeric,
  decodeRecord,
  sceneTime,
  subsystemDestination,
} from "../src/context";

test("multi-node and encoded identifiers survive allowed URL context", () => {
  const context = readContext(
    "?var-node=n1&var-node=n2&var-run_id=run%2B1%26x&var-engine=e%3A8000&from=1000&to=2000&credential=secret",
  );
  assert.deepEqual(readContext(String(writeContext(context))), context);
  assert.equal(String(writeContext(context)).includes("secret"), false);
  const link = new URL(dashboardLink("storage", context), "http://localhost");
  assert.deepEqual(link.searchParams.getAll("var-node"), ["n1", "n2"]);
  assert.equal(link.searchParams.get("var-run_id"), "run+1&x");
});
test("slow step selects row identity and time without resetting resource node", () => {
  const current = readContext(
    "?var-node=storage-2&var-engine=e1&var-trace_id=old&var-candidate_id=old-candidate",
  );
  const selected = selectStep(
    {
      cluster: "c",
      run_id: "r",
      observer_node: "trainer",
      record_id: "record",
      window_start_ms: 100,
      window_end_ms: 200,
    },
    current,
  );
  assert.deepEqual(selected.variables.node, ["storage-2"]);
  assert.deepEqual(selected.variables.source_node, ["trainer"]);
  assert.deepEqual(selected.variables.trace_id, [".*"]);
  assert.deepEqual(selected.variables.candidate_id, []);
  assert.equal(selected.from, "100");
  assert.throws(() => selectStep({ record_id: "x", run_id: "r" }, current));
});
test("logs use independent directory and telemetry Run mapping", () => {
  const context = readContext(
    "?var-run_id=telemetry-run&var-log_run_id=log-directory",
  );
  const params = new URL(dashboardLink("logs", context), "http://localhost")
    .searchParams;
  assert.equal(params.get("var-run_id"), "log-directory");
  assert.equal(params.get("var-telemetry_run_id"), "telemetry-run");
});
test("zero is measured, unknown/nonfinite is missing", () => {
  assert.equal(numeric("   "), undefined);
  assert.equal(numeric([]), undefined);
  assert.equal(numeric({}), undefined);
  assert.equal(numeric(0), 0);
  assert.equal(numeric("0"), 0);
  for (const value of [null, undefined, "", "NaN", Infinity, false])
    assert.equal(numeric(value), undefined);
});
test("canonical Loki wrapper preserves source and entity fields", () => {
  const row = decodeRecord(
    JSON.stringify({
      cluster: "c",
      observer_node: "collector",
      record: {
        run_id: "r",
        current: 0,
        observation_scope: "shared-service",
        entity: "engine=e",
        unit: null,
      },
    }),
  );
  assert.equal(row?.current, 0);
  assert.equal(row?.observer_node, "collector");
  assert.equal(row?.entity, "engine=e");
  assert.equal(decodeRecord("partial{"), undefined);
});

test("numeric Grafana URLs become valid absolute SceneTimeRange dates", () => {
  assert.equal(
    sceneTime("1791373044124"),
    new Date(1791373044124).toISOString(),
  );
  assert.equal(sceneTime("now-30m"), "now-30m");
});

test("compute candidates and network cells pivot to Compute, not serving detail", () => {
  const context = readContext(
    "?var-source_node=trainer&var-node=storage&var-record_id=step&from=1000&to=2000",
  );
  const link = dashboardLink(subsystemDestination("compute"), context);
  assert.match(link, /^\/d\/xlayer-compute-communication/);
  assert.equal(
    new URL(link, "http://localhost").searchParams.get("var-source_node"),
    "trainer",
  );
  assert.equal(subsystemDestination("network"), "compute");
  assert.equal(subsystemDestination("kv"), "stage");
});


test('native URL defaults/order/epoch formatting keep evidence open; real context changes do not',()=>{
  const a=readContext('?var-node=n2&var-node=n1&from=1000&to=2000');
  const b=readContext('?var-node=n1&var-node=n2&var-worker=$__all&from=1970-01-01T00:00:01.000Z&to=2000');
  assert.equal(investigationKey(a),investigationKey(b));
  for(const query of ['?var-node=n1&from=1000&to=2000','?var-node=n2&var-node=n1&var-phase_worker=other&from=1000&to=2000','?var-node=n2&var-node=n1&from=1001&to=2000'])assert.notEqual(investigationKey(a),investigationKey(readContext(query)));
});

test('known observation rows validate without losing additive metadata or resource identity',()=>{
 const row=decodeRecord({row_kind:'evidence',current:'0',baseline:null,observation_status:'observed',
  clock_status:'unsafe',observation_scope:'device',entity:'node=n,gpu=0',resource_identity:{node:'n',gpu:'0'},new_field:{v:1}});
 assert.equal(row?.current,0);assert.equal(row?.observation_status,'observed');assert.equal(row?.clock_status,'unsafe');
 assert.deepEqual(row?.resource_identity,{node:'n',gpu:'0'});assert.deepEqual(row?.new_field,{v:1});
 assert.equal(decodeRecord({record:{node:'n'}})?.node,'n');
 assert.equal(decodeRecord({record:[]}),undefined);
 for(const change of [{signal:{}},{entity:[]},{resource_identity:{gpu:0}}])
  assert.equal(decodeRecord({current:0,...change}),undefined);
 for(const value of [Infinity,NaN,{},[],true,'NaN']){
  const invalid=decodeRecord({current:value,observation_status:'observed'});
  assert.equal(invalid?.current,null);assert.equal(invalid?.observation_status,'unknown');
 }
 assert.equal(decodeRecord({current:0,observation_status:'future_success'})?.observation_status,'unknown');
 for(const status of ['no_data','error','stale','unknown'])
  assert.equal(decodeRecord({current:0,observation_status:status})?.observation_status,status);
});

test('legacy LLM statistical observations retain finite statistics without coercing them to a scalar',()=>{
 const current={min:0,mean:0,max:0,last:0,sample_count:2,max_series_delta:null};
 const row=decodeRecord({current,baseline:{mean:10},observation_status:'observed'});
 assert.deepEqual(row?.current,current);assert.deepEqual(row?.baseline,{mean:10});
 assert.equal(row?.observation_status,'observed');
 for(const value of [{mean:Infinity},{mean:'0'},{invented:0},{}]){
  const invalid=decodeRecord({current:value,observation_status:'observed'});
  assert.equal(invalid?.current,null);assert.equal(invalid?.observation_status,'unknown');
 }
});

test('resource identity preserves scope fields and rejects duplicate legacy keys',()=>{
 assert.deepEqual(resourceIdentity({entity:'node=n,gpu=0'}),{node:'n',gpu:'0'});
 assert.deepEqual(resourceIdentity({resource_identity:{node:'n',gpu:'0'}}),{node:'n',gpu:'0'});
 for(const entity of ['node=n,node=other','node=','node=n,broken'])assert.equal(resourceIdentity({entity}),undefined);
 assert.equal(observationStatus({current:0,clock_status:'unsafe'}),'observed');
 assert.equal(observationStatus({current:0,observation_status:'stale'}),'stale');
});

test('explicit observation status has display semantics independent of value and clock',()=>{
 assert.equal(observationLabel({current:0,observation_status:'observed',clock_status:'unsafe'}),undefined);
 for(const [status,label] of [['no_data','No data'],['error','Query error'],['stale','Stale'],['unknown','Unknown'],['future_success','Unknown']])
  assert.equal(observationLabel({current:0,observation_status:status}),label);
});

test('statistical sample counts describe exposure and never invent an observation',()=>{
 for(const current of [{sample_count:0},{sample_count:2},{mean:0,sample_count:0}]){
  assert.equal(observationStatus({current}),'unknown');
  const row=decodeRecord({current,observation_status:'observed'});
  assert.deepEqual(row?.current,current);assert.equal(row?.observation_status,'unknown');
 }
 for(const sample_count of [-1,1.5,Infinity,'2',true]){
  const row=decodeRecord({current:{mean:0,sample_count},observation_status:'observed'});
  assert.equal(row?.current,null);assert.equal(row?.observation_status,'unknown');
 }
 for(const current of [{mean:0,sample_count:2},{mean:0}]){
  assert.equal(observationStatus({current}),'observed');
  assert.equal(decodeRecord({current,observation_status:'observed'})?.observation_status,'observed');
 }
});
