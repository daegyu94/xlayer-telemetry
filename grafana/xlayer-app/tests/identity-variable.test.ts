import test from 'node:test';import assert from 'node:assert/strict';
import {preserveIdentity} from '../src/identity-variable';
test('retention and empty option refresh never replace explicit saved Run/observer identity',()=>{
 for(const name of ['cluster','run_id','source_node']){const update={value:['another'],text:['another'],options:[]};preserveIdentity(name,['archived'],['archived'],update);assert.deepEqual(update.value,['archived']);}
 const update={value:['active'],text:['active']};preserveIdentity('gpu',['9'],['9'],update);assert.deepEqual(update.value,['active']);
 preserveIdentity('run_id',['$__all'],['All'],update);assert.deepEqual(update.value,['active']);
});
