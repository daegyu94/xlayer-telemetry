import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {MatrixEntityControl} from '../src/MatrixEntityControl';
import {relatedPanels} from '../src/related-panels';

test('unavailable explicit Matrix selection can always be cleared with zero or one remaining entity',()=>{
 for(const entities of [[],[{key:'B',label:'GPU B'}]]){
  const html=renderToStaticMarkup(React.createElement(MatrixEntityControl,{title:'GPU',selectedKey:'A',entities,onChange:()=>{}}));
  assert.ok(html.includes('선택 해제'));assert.ok(html.includes('role="status"'));assert.ok(html.includes('value="A"'));
 }
});
test('Related Metrics labels remain paired with the surviving canonical panels',()=>{
 const value=relatedPanels([{label:'GPU',panel:undefined},{label:'vLLM',panel:undefined},{label:'KV Cache',panel:'prefix-cache'},{label:'Network',panel:'network'}]);
 assert.deepEqual(value.related,['prefix-cache','network']);assert.deepEqual(value.relatedLabels,['KV Cache','Network']);
});
