import {test} from 'node:test';
import assert from 'node:assert/strict';
import {timelineCalls} from '../src/workspace-model';
const step={run_id:'r',node:'n',step:7,record_id:'s',window_start_ms:1000,window_end_ms:20000};
const call={run_id:'r',node:'n',step:7,phase:'rollout',worker_id:'w',span_id:'a',boundary_accuracy:'exact',start_time_ms:3000,end_time_ms:9000};
test('compact timeline keeps overlapping workers as separate observed calls',()=>{
 const rows=timelineCalls([{...call,worker_id:'w2',span_id:'b',start_time_ms:4000},call],step);
 assert.equal(rows.length,2);assert.deepEqual(rows.map(row=>row.span.worker_id),['w','w2']);
 assert.equal(rows[0].window.start,3000);assert.equal(rows[1].window.start,4000);
});
test('unlinked, unknown and unaligned clock observations are not turned into timeline bars',()=>{
 assert.equal(timelineCalls([call],undefined).length,0);
 assert.equal(timelineCalls([{...call,run_id:'other'}, {...call,boundary_accuracy:'unknown'}, {...call,node:'remote',clock_reference:'remote'}],step).length,0);
});
