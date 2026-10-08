import { test } from 'node:test';
import assert from 'node:assert/strict';
import { storagePlotSelection, storageMetrics } from '../src/storage-series';
import { readContext, writeContext, dashboardLink } from '../src/context';
const point = (extra = {}) => ({row_kind:'storage_sample',cluster:'c',run_id:'r',record_id:'step',window_role:'current',source_table:'distributions',metric_name:'read_latency',sample_timestamp_ms:100000,sample_value:0,unit:null,series_key:'distributions:read_latency:host=a',source_host:'a',entity:'host=a',observation_scope:'shared-service',phase_attribution:'not_established',...extra});
test('storage metric selection is literal URL context and survives App/dashboard navigation',()=>{
 const ctx=readContext('?var-storage_metric=read%2B%22latency%22&var-record_id=step&from=1000&to=2000');
 assert.equal(ctx.variables.storage_metric?.[0],'read+"latency"');
 assert.deepEqual(readContext(String(writeContext(ctx))),ctx);
 assert.equal(new URL(dashboardLink('storage',ctx),'http://local').searchParams.get('var-storage_metric'),'read+"latency"');
});
test('plot requires one literal metric and never mixes raw source units or tables',()=>{
 assert.equal(storagePlotSelection([point()],undefined).state,'select-metric');
 assert.equal(storagePlotSelection([point()],'read_latency').state,'ready');
 assert.equal(storagePlotSelection([point(),point({source_table:'counters'})],'read_latency').state,'mixed-source');
 assert.equal(storagePlotSelection([point(),point({unit:'ns'})],'read_latency').state,'mixed-unit');
 assert.equal(storagePlotSelection([point(),point({metric_name:'other',unit:'bytes'})],'read_latency').state,'ready');
});
test('current points preserve measured zero/raw seconds resolution and baseline keeps its original earlier axis',()=>{
 const base=point({window_role:'baseline',sample_timestamp_ms:50000,sample_value:100});
 const selected=storagePlotSelection([point(),base],'read_latency');
 assert.equal(selected.state,'ready');assert.equal(selected.points[0].sample_value,0);
 assert.equal(selected.points[0].sample_timestamp_ms,100000);assert.equal(selected.points.length,1);
 assert.equal(base.sample_timestamp_ms,50000);assert.equal(selected.unit,undefined);
 assert.equal(storagePlotSelection([base],'read_latency').state,'no-data');
});
test('ambiguous or unknown samples stay unplotted; known non-attribution and source host are preserved',()=>{
 for(const extra of [{sample_value:null},{sample_timestamp_ms:null},{ambiguous_sample:true}])assert.equal(storagePlotSelection([point(extra)],'read_latency').state,'no-data');
 const selected=storagePlotSelection([point({source_host:'storage-node'})],'read_latency');
 assert.equal(selected.points[0].source_host,'storage-node');assert.equal(selected.points[0].phase_attribution,'not_established');
 assert.equal(selected.points[0].node,undefined);
});
test('one chart does not merge samples from different owner observations',()=>{
 assert.equal(storagePlotSelection([point(),point({record_id:'other'})],'read_latency').state,'multiple-owner');
 assert.deepEqual(storageMetrics([point(),point({window_role:'baseline'}),point({metric_name:'write_latency'})]),['read_latency','write_latency']);
});
