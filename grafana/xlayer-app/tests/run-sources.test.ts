import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {readFileSync} from 'node:fs';
import {RUN_SOURCES,RunSources} from '../src/RunSources';
import {readContext} from '../src/context';
import type {Panel} from '../src/catalog';

test('landing cards retain context and disclose missing panels and saved 3FS scope',()=>{
 const html=renderToStaticMarkup(React.createElement(RunSources,{panels:[],View:()=>null,context:readContext('?var-cluster=c&var-run_id=r&var-node=n&var-record_id=s&from=1000&to=2000&theme=dark')}));
 for(const name of ['VERL','GPU','vLLM','Ray','Mooncake','3FS'])assert.ok(html.includes(`aria-label="${name} signals"`));
 assert.equal((html.match(/Source panel을 사용할 수 없습니다/g)||[]).length,6);
 assert.match(html,/Saved collection comparison/);
 for(const [,href] of html.matchAll(/href="([^"]+)"/g)){
  const url=new URL(href.split('&amp;').join('&'),'http://localhost');
  for(const [key,value] of Object.entries({'var-cluster':'c','var-run_id':'r','var-node':'n','var-record_id':'s',from:'1000',to:'2000',theme:'dark'}))assert.equal(url.searchParams.get(key),value);
 }
 assert.match(html,/detail_tab=3FS/);
});
test('each landing source uses an existing canonical panel with its unit and identity query',()=>{
 const files={overview:'run-overview',compute:'compute-communication',stage:'agent-rl-stages',storage:'data-storage'};
 for(const source of RUN_SOURCES){
  const name=files[source.source as keyof typeof files];assert.ok(name);
  const dashboard=JSON.parse(readFileSync(`../../examples/dashboards/${name}.json`,'utf8'));
  const walk=(panels:Panel[]):Panel[]=>panels.flatMap(panel=>[panel,...walk(panel.panels||[])]);
  const panel=walk(dashboard.panels).find(panel=>panel.id===source.panel);assert.ok(panel?.targets?.length,source.name);
  assert.ok(panel?.fieldConfig,source.name);
 }
});
