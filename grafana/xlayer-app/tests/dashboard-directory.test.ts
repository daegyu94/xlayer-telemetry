import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {DashboardDirectory} from '../src/DashboardDirectory';
import {NATIVE_PAGES} from '../src/native-workspace';
import {readContext} from '../src/context';

test('dashboard directory keeps all six legacy routes and full investigation context even when a source is unavailable',()=>{
 const context=readContext('?var-cluster=c&var-run_id=r&var-record_id=step&var-node=n&var-source_node=observer&var-worker=w&var-detail_tab=GPU&from=1000&to=2000&theme=dark&timezone=UTC');
 const html=renderToStaticMarkup(React.createElement(DashboardDirectory,{context,catalog:{},onNavigate:()=>{}}));
 const links=[...html.matchAll(/href="([^"]+)"/g)].map(match=>new URL(match[1].split('&amp;').join('&'),'http://local'));
 assert.equal(links.length,6);
 assert.equal((html.match(/Dashboard unavailable/g)||[]).length,6);
 for(const [index,page] of NATIVE_PAGES.entries()){
  assert.equal(links[index].pathname,`/a/xlayer-telemetry-app/${page.route}`);
  assert.deepEqual(readContext(links[index].search),context);
  assert.ok(html.includes(page.description));
 }
});
