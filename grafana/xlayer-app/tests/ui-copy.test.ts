import {test} from 'node:test';
import assert from 'node:assert/strict';
import {displaySummary,displayUiNote} from '../src/ui-copy';

test('candidate display borrows only the exact native summary value mapping',()=>{
 const panel={id:3,type:'table',title:'Candidates',targets:[{expr:'unchanged query'}],fieldConfig:{overrides:[
  {matcher:{id:'byName',options:'state'},properties:[{id:'mappings',value:[{type:'value',options:{raw:{text:'wrong field'}}}]}]},
  {matcher:{id:'byName',options:'summary'},properties:[{id:'mappings',value:[{type:'value',options:{raw:{text:'함께 관측됐습니다.'}}},{type:'regex',options:{pattern:'.*',result:{text:'unsafe replacement'}}}]}]},
 ]}};
 const before=JSON.stringify(panel);
 assert.equal(displaySummary(panel,'raw'),'함께 관측됐습니다.');
 assert.equal(displaySummary(panel,'new native or LLM finding'),'new native or LLM finding');
 assert.equal(displaySummary(undefined,'new native or LLM finding'),'new native or LLM finding');
 assert.equal(JSON.stringify(panel),before);
});

test('display notes preserve technical identifiers and unknown producer messages',()=>{
 assert.match(displayUiNote('Sampled device utilization; not workload MFU.'),/MFU/);
 assert.equal(displayUiNote('upstream new_detail=42'),'upstream new_detail=42');
 assert.equal(displaySummary(undefined,undefined),'판단 설명이 기록되지 않았습니다.');
 for(const raw of ['constructor','toString','__proto__'])assert.equal(displayUiNote(raw),raw);
});

test('clock and scope notes preserve observed identity, precision limits and ownership uncertainty',()=>{
 const raw='Clock quality is unsafe. Raw resource data remain available; precise phase correlation and deltas are withheld. Query evaluation inside phase on node-1; calibrated application boundary (clock-session). Raw scrape timestamp / clock calibration may be unknown. Correlation is not attribution. Saved candidate evidence below describes the full Step, not phase attribution.';
 const displayed=displayUiNote(raw);
 for(const identity of ['unsafe','node-1','calibrated','clock-session'])assert.ok(displayed.includes(identity));
 assert.match(displayed,/보류/);
 assert.match(displayed,/전체 Step/);
 assert.equal(raw.includes('Clock quality is unsafe'),true);
});
