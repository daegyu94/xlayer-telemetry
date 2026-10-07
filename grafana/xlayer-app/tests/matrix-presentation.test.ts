import {test} from 'node:test';
import assert from 'node:assert/strict';
import {baselineBounds,filterMatrixEntity,matrixEntities,matrixLookback} from '../src/matrix-presentation';
test('Matrix identity selectors retain operation/state without pooling entities',()=>{
 const rows=[{value:1,time:1,unit:'s',labels:{engine:'e',operation:'get'}},{value:4,time:1,unit:'s',labels:{engine:'e',operation:'put'}}];
 const choices=matrixEntities(rows);assert.equal(choices.length,2);
 assert.equal(filterMatrixEntity(rows,choices[0].key)[0].value,1);
 assert.deepEqual(filterMatrixEntity(rows,'stale selection'),[]);
});
test('Saved baseline bounds require known comparability, matching identity and no overlap',()=>{
 const selected={record_id:'current',run_id:'r',cluster:'c',window_start_ms:100};
 const summary={...selected,baseline_start_ms:10,baseline_end_ms:90,baseline_record_id:'base',workload_comparability:'matched_configured_fields'};
 assert.deepEqual(baselineBounds(selected,summary),{start:10,end:90,record:'base'});
 assert.equal(baselineBounds(selected,{...summary,baseline_end_ms:101})?.end,101); // floor/ceil projection quantization only
 for(const change of [{workload_comparability:'unverified'},{cluster:'other'},{baseline_end_ms:102},{baseline_record_id:null}])assert.equal(baselineBounds(selected,{...summary,...change}),undefined);
});
test('Rate lookback is sourced from actual expression or left unknown, never fabricated',()=>{
 assert.equal(matrixLookback(['rate(x[8s])']),8000);
 assert.equal(matrixLookback(['rate(x[$__rate_interval])']),true);
 assert.equal(matrixLookback(['rate(x[1m])+rate(y[5m])']),300000);
});
