import test from 'node:test';
import assert from 'node:assert/strict';
import type {PanelData} from '@grafana/data';
import {timelinePanelHeight} from '../src/native-panel-layout';

test('Timeline reserves native axis/header space and at least 30px per 1/7/20 lanes',()=>{
 for(const lanes of [1,7,20]){
  const data={series:Array.from({length:lanes},()=>({fields:[{type:'time'},{type:'string'}]}))} as PanelData;
  assert.ok(timelinePanelHeight(data)>=120+lanes*30);
 }
 assert.equal(timelinePanelHeight(undefined),320);
});
