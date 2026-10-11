import test from 'node:test';import assert from 'node:assert/strict';
import {preserveIdentity} from '../src/identity-variable';
test('retention and empty option refresh never replace explicit saved Run/observer identity',()=>{
 for(const name of ['cluster','run_id','source_node']){const update={value:['another'],text:['another'],options:[]};preserveIdentity(name,['archived'],['archived'],update);assert.deepEqual(update.value,['archived']);}
 const update={value:['active'],text:['active']};preserveIdentity('gpu',['9'],['9'],update);assert.deepEqual(update.value,['active']);
 preserveIdentity('run_id',['$__all'],['All'],update);assert.deepEqual(update.value,['active']);
});

import {normalizeSelection} from '../src/identity-variable';
test('native All links remain an all-series regex in Scenes textbox filters',()=>{
 const gpu={type:'textbox',query:'.*',current:{value:'.*'}};
 assert.deepEqual(normalizeSelection(gpu,['$__all']),['.*']);
 assert.deepEqual(normalizeSelection(gpu,['3']),['3']);
 assert.deepEqual(normalizeSelection({type:'query',includeAll:true},['.*']),['$__all']);
 assert.deepEqual(normalizeSelection({type:'textbox',query:'300'},['$__all']),['$__all']);
 assert.deepEqual(normalizeSelection(undefined,['literal.*']),['literal.*']);
});
