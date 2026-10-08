import {test} from 'node:test';
import assert from 'node:assert/strict';
import {boundaryPresentation,entitySelectionHint,compactEntity,DEEP_DIVE_SPECS,detailTabIndex} from '../src/presentation';
test('async trainer update is explicit and never presented as an inclusive rollout step',()=>{
 const update=boundaryPresentation({boundary_scope:'trainer_update',execution_mode:'async'});
 assert.equal(update.label,'Trainer update');assert.equal(update.timeLabel,'Update time');assert.match(update.note,/rollout\/tool/);
 assert.equal(boundaryPresentation({boundary_scope:'rl_step'}).label,'Step');
 assert.equal(boundaryPresentation({execution_mode:'async'}).label,'Observation');
});
test('entity guidance identifies the actual dimension that differs',()=>{
 const hints=(key:string)=>entitySelectionHint([{[key]:'0'},{[key]:'1'}]);
 assert.match(hints('gpu'),/GPU/);assert.match(hints('instance'),/Engine/);assert.match(hints('worker_id'),/Worker/);assert.match(hints('run_id'),/Run/);
 assert.match(hints('verl_stage'),/stage/);assert.match(hints('other'),/implicit aggregation/);
});
test('compact presentation preserves full labels outside the summary',()=>{
 const labels={nodename:'node',node:'node',gpu:'0',instance:'very-long-endpoint',job:'native',model_name:'model'};
 assert.equal(compactEntity(labels),'node · GPU 0');assert.equal(labels.instance,'very-long-endpoint');
});
test('storage investigation reuses source-qualified canonical panels and saved 3FS evidence',()=>{
 assert.equal(DEEP_DIVE_SPECS.find(s=>s.label==='Connector RPC')?.panel,60);
 assert.equal(DEEP_DIVE_SPECS.find(s=>s.label==='DFS batch')?.panel,66);
 assert.equal(DEEP_DIVE_SPECS.find(s=>s.label==='DFS bytes')?.panel,64);
 assert.equal(DEEP_DIVE_SPECS.find(s=>s.label==='3FS evidence')?.panel,undefined);
 assert.equal(DEEP_DIVE_SPECS.find(s=>s.label==='Local I/O mean')?.panel,30);
});
test('storage metric navigation can retain the active evidence tab without choosing an unsupported tab',()=>{
 assert.equal(detailTabIndex('3FS evidence'),4);assert.equal(detailTabIndex('unknown'),0);
});
