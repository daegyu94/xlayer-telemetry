import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseDemoCatalog,demoObservationContext,demoLandingContext} from '../src/demo-scenarios';
import {readContext,appLink} from '../src/context';
const row={scenario:'normal',step:0,boundary_scope:'rl_step',context:{cluster:'c',run_id:'r',source_node:'n',record_id:'zero',from:'0',to:'1000'}};
const catalog={schema_version:1,data_origin:'synthetic',scenarios:[{id:'normal',title:'Normal',category:'basic',question:'?',expected:'observed',workflow:['Run']}],observations:[row],boundary_tests:[]};
test('scenario links keep real zero epochs and discard malformed records without losing good replays',()=>{
 const data=parseDemoCatalog({...catalog,observations:[row,{...row,context:{...row.context,from:'nan'}},{...row,context:{...row.context,from:''}},{...row,context:{...row.context,from:'   '}},{...row,context:{...row.context,to:'-1'}},{...row,context:{...row.context,run_id:''}},{...row,context:{...row.context,run_id:'.*'}}]});
 assert.deepEqual(data.observations,[row]);
 assert.throws(()=>parseDemoCatalog({...catalog,data_origin:'real'}));
 assert.throws(()=>parseDemoCatalog({...catalog,observations:Array(29).fill(row)}));
});
test('changing replay resets previous entity/candidate context but preserves theme and timezone',()=>{
 const before=readContext('?theme=dark&timezone=Asia%2FSeoul&var-worker=old&var-candidate_id=old&var-engine=old');
 const next=demoObservationContext(row,before);
 assert.equal(next.from,'0');assert.equal(next.theme,'dark');assert.equal(next.timezone,'Asia/Seoul');
 assert.equal(next.variables.worker,undefined);assert.equal(next.variables.candidate_id,undefined);assert.deepEqual(next.variables.run_id,['r']);
 assert.deepEqual(readContext(new URL(appLink('overview',next),'http://local').search),next);
});

test('expired replay recovery preserves display preferences and clears the old investigation',()=>{
 const old=readContext('?theme=dark&timezone=UTC&var-demo_generation=3&var-run_id=g3-old&var-record_id=old&from=0&to=1000');
 const landing=demoLandingContext(old);
 assert.equal(landing.theme,'dark');assert.equal(landing.timezone,'UTC');assert.deepEqual(landing.variables,{});
 assert.equal(landing.from,'now-30m');
});
