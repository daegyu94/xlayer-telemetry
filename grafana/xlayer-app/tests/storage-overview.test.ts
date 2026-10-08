import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseStorageOverview,storageDetailGroups,storageSourceContext} from '../src/storage-overview';
import {DEEP_DIVE_SPECS,detailTabIndex} from '../src/presentation';

const entry={signal:'mooncake_dfs_read_errors_per_second',current:0,baseline:null,delta_percent:null,
 scope:'shared-service',unit:'keys/s',statistic:'max',entity:{node:'worker',instance:'client'},
 status:'observed',quality_issues:[],layer:'dfs_client'};
const summary=(signals:unknown[]=[entry])=>JSON.stringify({backend:{status:'not_reported',adapter:null},
 operation_attribution:'not_established',signals,threefs:{status:'not_configured',scope:'shared-service',relationship_to_mooncake:'not_established'}});

test('common summary preserves measured zero and does not invent a backend from 3FS availability',()=>{
 const value=parseStorageOverview(summary());assert.ok(value);
 assert.equal(value.signals[0].current,0);assert.equal(value.threefs.status,'not_configured');
 assert.equal(value.backend.status,'not_reported');
 const configured=JSON.parse(summary());configured.threefs.status='observed';
 assert.equal(parseStorageOverview(JSON.stringify(configured))?.backend.status,'not_reported');
});
test('partial/legacy/malformed summaries remain unavailable rather than measured zero',()=>{
 for(const raw of [undefined,'null','{','[]',' '.repeat(16385),summary([{...entry,current:NaN}]),summary([{...entry,current:true}])])
  assert.equal(parseStorageOverview(raw),undefined);
});
test('scope and quality boundaries remain explicit for raw stale and unsafe observations',()=>{
 for(const status of ['stale','clock_unverified','query_failed','no_data']){
  const value=parseStorageOverview(summary([{...entry,status,current:status==='no_data'||status==='query_failed'?null:2,quality_issues:['range_window_exceeds_interval']}]));
  assert.equal(value?.signals[0].status,status);assert.equal(value?.signals[0].scope,'shared-service');
 }
});
test('storage detail groups reuse the existing 3FS entry and omit pNFS and SSD expansion',()=>{
 const groups=storageDetailGroups(DEEP_DIVE_SPECS);
 assert.deepEqual(groups.common.map(s=>s.label),['Connector RPC','DFS batch','DFS bytes','Failures']);
 assert.deepEqual(groups.backend.map(s=>s.label),['3FS evidence']);
 assert.equal(detailTabIndex('3FS evidence'),4);
 assert.ok(groups.context.some(s=>s.label==='Local I/O mean'));
 assert.ok(![...groups.common,...groups.backend,...groups.context].some(s=>/pNFS|SSD Deep Dive/.test(s.label)));
});
test('source pivot preserves execution context and selects RPC endpoint rather than engine index',()=>{
 const context={from:'100',to:'200',timezone:'UTC',variables:{run_id:['r'],record_id:['step'],source_node:['trainer'],worker:['driver'],node:['gpu']}};
 const signal={...entry,layer:'connector',entity:{node:'serving',instance:'engine-endpoint',engine:'0'}};
 const target=storageSourceContext(context,signal);
 assert.equal(target.from,context.from);assert.deepEqual(target.variables.run_id,['r']);
 assert.deepEqual(target.variables.source_node,['trainer']);assert.deepEqual(target.variables.record_id,['step']);
 assert.deepEqual(target.variables.node,['serving']);assert.deepEqual(target.variables.engine,['engine-endpoint']);
});
