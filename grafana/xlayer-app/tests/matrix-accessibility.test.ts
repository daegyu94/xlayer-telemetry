import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {MatrixCellButton, matrixDeltaLabel} from '../src/MatrixCellButton';

for (const [label,comparison,expected] of [
 ['0%', {comparable:true,delta:0,reason:'matched'}, 'No change vs baseline'],
 ['75%', {comparable:true,delta:25,reason:'matched'}, '↑ +25.0% vs baseline'],
 ['42%', {comparable:true,delta:-10,reason:'matched'}, '↓ 10.0% vs baseline'],
 ['0%', {comparable:true,reason:'zero baseline'}, 'Δ unavailable · baseline 0'],
 ['No data', {comparable:false,reason:'missing'}, undefined],
 ['Clock unverified', {comparable:false,reason:'unsafe clock'}, undefined],
 ['Query error', {comparable:false,reason:'query failed'}, undefined],
] as const) test(`Matrix accessible name preserves the displayed result: ${label} / ${expected}`,()=>{
 const deltaLabel=matrixDeltaLabel(comparison);
 assert.equal(deltaLabel,expected);
 const missing=!comparison.comparable;
 const quality=missing?'device':'Sampled · Node';
 const html=renderToStaticMarkup(React.createElement(MatrixCellButton,{
  identity:'rollout × gpu evidence',label,quality,comparison,
  explanation:'Source scope is device, not Run ownership',onClick:()=>{},observations:missing?undefined:2,
 }));
 const name=/aria-label="([^"]+)"/.exec(html)![1];
 assert.ok(name.includes(label));assert.ok(name.includes(quality));
 if(!missing)assert.ok(name.includes('2 query observations · mean'));
 else assert.ok(!name.includes('query observations'));
 if(expected)assert.ok(name.includes(expected));
 assert.ok(html.includes(`<b>${label}</b>`));
 assert.ok(html.includes('aria-description="Source scope is device, not Run ownership'));
});
