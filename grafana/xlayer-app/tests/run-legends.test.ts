import {test} from 'node:test';
import assert from 'node:assert/strict';
import type {DataFrame} from '@grafana/data';
import {compactRunLegends} from '../src/run-legends';

const frame=(refId:string,labels:Record<string,string>):DataFrame=>({refId,length:1,fields:[
 {name:'Time',type:'time' as any,config:{},values:[1]},
 {name:'Value',type:'number' as any,labels,config:{unit:'s'},values:[2]},
]});
test('landing legends hide shared identity while keeping metric and engine names',()=>{
 const labels={cluster:'scenes-demo',node:'gpu-node-0',instance:'vllm:8000',model_name:'demo-model',engine:'0'};
 const frames=[frame('A',labels),frame('B',labels)];
 const snapshot=JSON.stringify(frames);
 const result=compactRunLegends(frames,[
  {refId:'A',legendFormat:'{{cluster}}/{{node}} {{instance}} {{model_name}} engine={{engine}} local'},
  {refId:'B',legendFormat:'{{cluster}}/{{node}} {{instance}} {{model_name}} engine={{engine}} external_'},
 ]);
 assert.deepEqual(result.map(f=>f.fields[1].config.displayName),['0 local','0 external']);
 assert.equal(JSON.stringify(frames),snapshot);
 assert.equal(result[0].fields[1].labels,labels);
 assert.equal(result[0].fields[1].values,frames[0].fields[1].values);
 assert.equal(result[0].fields[1].config.unit,'s');
});
test('multiple runs, nodes, models and producers remain distinguishable',()=>{
 const targets=[{refId:'A',legendFormat:'{{cluster}}/{{instance}} {{run_id}} {{producer}}/{{role}} worker {{worker_id}}'}];
 const labels={cluster:'c',instance:'n',run_id:'r',producer:'p',role:'trainer',worker_id:'driver'};
 assert.equal(compactRunLegends([frame('A',labels)],targets)[0].fields[1].config.displayName,'trainer driver');
 for(const key of ['cluster','instance','run_id','producer']){
  const result=compactRunLegends([frame('A',labels),frame('A',{...labels,[key]:'other'})],targets);
  assert.notEqual(result[0].fields[1].config.displayName,result[1].fields[1].config.displayName,key);
  assert.match(result[1].fields[1].config.displayName||'',/other/);
 }
 const result=compactRunLegends([frame('A',{model_name:'m1'}),frame('A',{model_name:'m2'})],[{refId:'A',legendFormat:'{{model_name}} hits/s'}]);
 assert.deepEqual(result.map(f=>f.fields[1].config.displayName),['m1 hits/s','m2 hits/s']);
});
test('missing identity, absent templates and empty results preserve usable names',()=>{
 const frames=[frame('A',{node:'n'}),frame('A',{})];
 const result=compactRunLegends(frames,[{refId:'A',legendFormat:'{{node}} pending'}]);
 assert.equal(result[0].fields[1].config.displayName,'n pending');
 assert.equal(result[1].fields[1].config.displayName,'pending');
 assert.equal(compactRunLegends(frames,[])[0].fields[1],frames[0].fields[1]);
 assert.equal(compactRunLegends([frame('A',{node:'n'})],[{refId:'A',legendFormat:'{{node}}'}])[0].fields[1].config.displayName,undefined);
 assert.deepEqual(compactRunLegends([],[]),[]);
});
