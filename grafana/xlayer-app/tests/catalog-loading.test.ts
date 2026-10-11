import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadDashboardCatalog,CatalogLoadError} from '../src/catalog-loading';
import {DASHBOARD_UIDS} from '../src/context';
const dashboard={panels:[],templating:{list:[]}};
for(const status of [404,403,429,503,undefined])test(`optional Dashboard ${status??'timeout'} does not reject core and retains failure state`,async()=>{
 const result=await loadDashboardCatalog(async uid=>{if(uid===DASHBOARD_UIDS.logs)throw {status};return {dashboard};});
 for(const key of ['overview','stage','compute','storage'] as const)assert.equal(result[key],dashboard);
 assert.equal(result.logs,undefined);assert.equal(result.issues?.logs?.state,status===404?'not-provisioned':'query-failed');
 assert.equal(result.issues?.logs?.status,status);
});
test('required failure identifies the source; subsequent recovery returns the canonical Dashboard',async()=>{
 await assert.rejects(loadDashboardCatalog(async uid=>{if(uid===DASHBOARD_UIDS.compute)throw {status:503};return {dashboard};}),error=>error instanceof CatalogLoadError&&error.issues.compute?.status===503&&error.message.includes(DASHBOARD_UIDS.compute));
 const result=await loadDashboardCatalog(async()=>({dashboard}));assert.equal(result.compute,dashboard);assert.deepEqual(result.issues,{});
});
test('malformed optional response is query failure, distinct from not provisioned',async()=>{
 const result=await loadDashboardCatalog(async uid=>uid===DASHBOARD_UIDS.logs?{}:{dashboard});assert.equal(result.issues?.logs?.state,'query-failed');
});
