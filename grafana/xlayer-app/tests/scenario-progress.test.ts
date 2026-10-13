import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {ScenarioProgress,ScenarioStage,EXECUTION_STAGES,scenarioIsBusy} from '../src/ScenarioProgress';

test('progress counts reported execution-stage completions and excludes browser-only validation',()=>{
 const html=renderToStaticMarkup(React.createElement(ScenarioProgress,{message:'Collecting',validation:{
  generation:{state:'passed',checks:[]},collection:{state:'running',checks:[]},
  query:{state:'failed',checks:[]},ui:{state:'passed',checks:[]},
 }}));
 assert.match(html,/aria-valuemax="4"/);assert.match(html,/aria-valuenow="1"/);
 assert.match(html,/data-state="failed"/);assert.match(html,/data-state="running"/);
 const missing=renderToStaticMarkup(React.createElement(ScenarioProgress,{message:'Starting',validation:undefined}));
 assert.match(missing,/aria-valuenow="0"/);assert.match(missing,/data-waiting="true"/);
});

test('revisiting a completed stage shows its result without replaying a completion flash',()=>{
 const html=renderToStaticMarkup(React.createElement(ScenarioStage,{stage:EXECUTION_STAGES[0],generation:2,result:{state:'passed',checks:['Actual collected input']}}));
 assert.match(html,/data-state="passed"/);assert.match(html,/✓/);
 assert.match(html,/Actual collected input/);assert.doesNotMatch(html,/xlt-stage-flash|data-transition/);
 assert.match(html,/검증을 통과했습니다/);assert.doesNotMatch(html,/아직 검증하지 않았습니다/);
});

test('in-flight execution and cleanup block duplicate runs while terminal states permit another run',()=>{
 for(const state of ['requesting','starting','collecting','stopping','resetting'])assert.equal(scenarioIsBusy(state),true,state);
 for(const state of ['idle','ready','error','stopped'])assert.equal(scenarioIsBusy(state),false,state);
});
