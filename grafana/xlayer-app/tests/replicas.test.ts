import test from 'node:test';
import assert from 'node:assert/strict';
import { replicaRows } from '../src/replicas';

const replica = {id:'r1',instance:'endpoint',endpoint_node:'remote',nodes:['remote','peer'],status:'partial_evidence',
 clock_status:'aligned',entities:[{identity:{cluster:'c',node:'remote',instance:'endpoint',engine:'0'},signals:{
  vllm_requests_waiting:{current:18,baseline:0,delta_percent:null}},missing_sources:['current:vllm_preemptions_delta:missing_or_duplicate'],
  candidates:[{id:'kv_cache_pressure',state:'supporting_signal'}]}]};
test('replica rows preserve partial evidence, missing versus measured zero and multiple nodes',()=>{
 const rows=replicaRows({cluster:'c',rollout_replicas:JSON.stringify([replica])});
 assert.equal(rows.length,1);assert.equal(rows[0].signals.vllm_requests_waiting.current,18);
 assert.equal(rows[0].signals.vllm_requests_waiting.baseline,0);assert.equal(rows[0].signals.vllm_preemptions_delta,undefined);
 assert.deepEqual(rows[0].nodes,['remote','peer']);assert.equal(rows[0].missing.length,1);
});
test('replica projection rejects malformed, conflicting and foreign endpoint identities',()=>{
 assert.deepEqual(replicaRows({rollout_replicas:'broken'}),[]);
 for(const change of [{cluster:'other'},{node:'wrong'},{instance:'other'}]){
  assert.deepEqual(replicaRows({cluster:'c',rollout_replicas:JSON.stringify([{...replica,entities:[{...replica.entities[0],identity:{...replica.entities[0].identity,...change}}]}])}),[]);
 }
 assert.deepEqual(replicaRows({identity_conflict:true,cluster:'c',rollout_replicas:JSON.stringify([replica])}),[]);
 assert.deepEqual(replicaRows({cluster:'c',rollout_replicas:JSON.stringify([{...replica,entities:[null]}])}),[]);
});
test('clock-unverified replica keeps raw values and suppresses deltas and candidates',()=>{
 const rows=replicaRows({cluster:'c',rollout_replicas:JSON.stringify([{...replica,clock_status:'unknown'}])});
 assert.equal(rows[0].signals.vllm_requests_waiting.current,18);assert.equal(rows[0].signals.vllm_requests_waiting.delta_percent,null);
 assert.deepEqual(rows[0].candidates,[]);
});
