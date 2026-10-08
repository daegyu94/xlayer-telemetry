import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readContext,appLink,investigationKey,selectStep} from '../src/context';
import {PAGES,sceneRoutes,pagePanels,versionFromPath,switchVersion} from '../src/pages';

test('classic URLs remain unchanged and workspace links preserve execution context',()=>{
 const context=readContext('?from=100&to=200&var-cluster=c&var-run_id=r&var-record_id=step&var-node=gpu&var-phase_worker=worker','classic');
 assert.ok(appLink('overview',context).startsWith('/a/xlayer-telemetry-app/overview?'));
 const workspace=switchVersion(context,'workspace');
 assert.ok(appLink('investigate',workspace).startsWith('/a/xlayer-telemetry-app/v2/investigate?'));
 assert.deepEqual(workspace.variables,context.variables);assert.equal(investigationKey(workspace),investigationKey(context));
 const selected=selectStep({record_id:'other',run_id:'r',node:'observer',window_start_ms:1000,window_end_ms:2000},workspace);
 assert.ok(appLink('analyze',selected).includes('/v2/analyze?'));
 assert.equal(versionFromPath('/a/xlayer-telemetry-app/v2/logs'),'workspace');
 assert.equal(versionFromPath('/a/xlayer-telemetry-app/timeline'),'classic');
});
test('six product pages and legacy timeline share the same panel contracts in both versions',()=>{
 assert.deepEqual(PAGES,['overview','analyze','investigate','deep-dive','infrastructure','logs']);
 const routes=sceneRoutes();assert.equal(routes.length,14);
 for(const page of [...PAGES,'timeline'] as const){
  const variants=routes.filter(route=>route.page===page);
  assert.equal(variants.length,2);assert.deepEqual(variants[0].panels,variants[1].panels);
  assert.deepEqual(variants[0].panels,pagePanels(page));
 }
 assert.ok(pagePanels('logs').some(p=>p.dashboard==='logs'&&p.panel===1));
 assert.ok(pagePanels('infrastructure').some(p=>p.dashboard==='storage'&&p.panel===113));
});
