import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {readFileSync} from 'node:fs';
import {RUN_SOURCES,RUN_SOURCE_PANELS,RunSources} from '../src/RunSources';
import {readContext} from '../src/context';
import type {Panel} from '../src/catalog';

test('landing cards retain context and keep system resources in Infrastructure',()=>{
 const html=renderToStaticMarkup(React.createElement(RunSources,{panels:[],View:()=>null,context:readContext('?var-cluster=c&var-run_id=r&var-node=n&var-record_id=s&var-demo_generation=1&from=1000&to=2000&theme=dark')}));
 for(const name of ['VERL','vLLM','Ray','Mooncake','3FS'])assert.ok(html.includes(`aria-label="${name} signals"`));
 assert.doesNotMatch(html,/aria-label="GPU signals"/);
 assert.equal((html.match(/Source panel을 사용할 수 없습니다/g)||[]).length,10);
 assert.match(html,/Synthetic demo/);
 assert.match(html,/Training step time/);assert.match(html,/Reward mean/);
 assert.match(html,/Time to first token p95/);assert.match(html,/Prefix cache token hit ratio/);
 assert.match(html,/KV Store · operation/);assert.match(html,/Memory\/file hits and lookups/);
 assert.match(html,/Request latency · reported p99/);assert.match(html,/Queued requests · service/);
 assert.match(html,/id="xlt-3fs"/);
 assert.equal(RUN_SOURCES.find(source=>source.name==='Mooncake')?.panels[0].title,'Store request latency p95');
 assert.equal(RUN_SOURCES.find(source=>source.name==='Mooncake')?.panels[1].title,'Cache lookup rates');
 for(const [,href] of html.matchAll(/href="([^"]+)"/g)){
  const url=new URL(href.split('&amp;').join('&'),'http://localhost');
  for(const [key,value] of Object.entries({'var-cluster':'c','var-run_id':'r','var-node':'n','var-record_id':'s','var-demo_generation':'1',from:'1000',to:'2000',theme:'dark'}))assert.equal(url.searchParams.get(key),value);
 }
 assert.match(html,/detail_tab=3FS/);
});
test('each landing source uses an existing canonical panel with its unit and identity query',()=>{
 const files={overview:'run-overview',compute:'compute-communication',stage:'agent-rl-stages',storage:'data-storage'};
 for(const source of RUN_SOURCE_PANELS){
  const name=files[source.source as keyof typeof files];assert.ok(name);
  const dashboard=JSON.parse(readFileSync(`../../examples/dashboards/${name}.json`,'utf8'));
  const walk=(panels:Panel[]):Panel[]=>panels.flatMap(panel=>[panel,...walk(panel.panels||[])]);
  const panel=walk(dashboard.panels).find(panel=>panel.id===source.panel);assert.ok(panel?.targets?.length,source.scope);
  assert.ok(panel?.fieldConfig,source.scope);
 }
 assert.equal(RUN_SOURCES.length,5);
});
