import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readContext,writeContext,dashboardLink,investigationKey} from '../src/context';
import {WORKSPACE_PAGES,themeContext,navigationLink} from '../src/navigation';

test('all workspace pivots retain execution, resource and time context in either theme',()=>{
 const context=readContext('?var-run_id=run-a&var-run_id=run-b&var-record_id=step%2B128&var-worker=w&var-node=ssd-node&var-device=nvme0&var-engine=e&from=1000&to=2000');
 for(const theme of ['light','dark'] as const){const themed=themeContext(context,theme);
  for(const page of WORKSPACE_PAGES)assert.deepEqual(readContext(new URL(navigationLink(page.route,themed),'http://localhost').search),themed);
  assert.equal(new URL(dashboardLink('storage',themed),'http://localhost').searchParams.get('theme'),theme);
  assert.equal(investigationKey(themed),investigationKey(context),'visual theme is not a new investigation');
 }
});
test('unknown theme is ignored without serializing arbitrary query state',()=>{
 const context=readContext('?theme=unsafe&token=secret');assert.equal(context.theme,undefined);assert.ok(!String(writeContext(context)).includes('unsafe'));assert.ok(!String(writeContext(context)).includes('secret'));
});
