"""Run scenarios through one Scenario Explorer UI, including lifecycle isolation."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse,parse_qs,urlencode
from design_validate import owned_origin


def run(args):
    from playwright.sync_api import sync_playwright,expect
    origin=owned_origin(args.url);args.output.mkdir(parents=True,exist_ok=False)
    report={'grafana':origin,'scenarios':[],'browser_errors':[],'scope':'One Grafana; per-replay synthetic stores; no hardware validation'}
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'],**({'executable_path':args.browser} if args.browser else {}))
        page=browser.new_page(viewport={'width':1440,'height':1000},timezone_id='Asia/Seoul')
        page.on('pageerror',lambda error:report['browser_errors'].append(str(error)))
        previous_url=None;previous_generation=None;signatures={}
        try:
            page.goto(origin+'/a/xlayer-telemetry-app/demo?theme=light&timezone=UTC')
            page.locator('.xlt-scenario-controls').wait_for(timeout=30000)
            # Cancel before a completed pair exists: no stale publication may reappear.
            page.get_by_role('button',name='Run scenario',exact=True).click()
            page.wait_for_function("document.querySelector('.xlt-scenario-controls [role=status]')?.textContent.startsWith('collecting')",timeout=30000)
            page.get_by_role('button',name='Stop scenario',exact=True).click()
            page.wait_for_function("document.querySelector('.xlt-scenario-controls [role=status]')?.textContent.startsWith('stopped')",timeout=60000)
            assert not page.locator('.xlt-replays li').count()
            page.get_by_role('button',name='Reset demo',exact=True).click()
            page.wait_for_function("document.querySelector('.xlt-scenario-controls [role=status]')?.textContent.startsWith('idle')",timeout=60000)
            report['cancel_during_collection']='passed'
            for scenario in args.scenarios:
                result={'scenario':scenario,'status':'running','workflow':'select → start'};report['scenarios'].append(result)
                # Scenario ID is stable URL state; the accessible card includes purpose and category.
                page.locator(f'.xlt-scenario-list a[href*="demo_scenario={scenario}"]').click()
                button=page.get_by_role('button',name='Run scenario',exact=True)
                if not button.count():button=page.get_by_role('button',name='Replay scenario',exact=True)
                button.click()
                expect(page.locator('.xlt-replays li')).to_have_count(0)
                page.wait_for_function("(previous)=>/^(ready|error)/.test(document.querySelector('.xlt-scenario-controls [role=status]')?.textContent||'') && Number((document.querySelector('.xlt-scenario-controls [role=status]')?.textContent||'').split('generation ')[1])>previous",arg=previous_generation or 0,timeout=300000)
                assert page.locator('.xlt-scenario-controls [role=status]').inner_text().startswith('ready'),page.locator('.xlt-scenario-selected').inner_text()
                for stage in ('generation','collection','query','diagnosis'):
                    expect(page.locator(f'[data-stage={stage}]')).to_have_attribute('data-state','passed')
                state=page.request.get(origin+'/api/plugin-proxy/xlayer-telemetry-app/demo-control/status').json()
                generation=state['generation'];result['generation']=generation;result['backend_validation']=state['validation']
                signature=state['validation']['generation']['checks'][0]
                if scenario in signatures:assert signatures[scenario]==signature,'Replay input shape changed'
                signatures[scenario]=signature
                assert previous_generation is None or generation>previous_generation
                assert all(row['context']['run_id'].startswith(f'g{generation}-') for row in state['observations'])
                if previous_url:
                    page.goto(previous_url)
                    page.get_by_text('Replay expired',exact=True).wait_for(timeout=10000)
                    page.get_by_role('link',name='Open Scenario Explorer →',exact=True).click()
                    page.locator(f'.xlt-scenario-list a[href*="demo_scenario={scenario}"]').click()
                    page.locator('.xlt-replays li').first.wait_for()
                result['workflow']='run → evidence → metric'
                page.screenshot(path=str(args.output/f'{scenario}-explorer.png'),full_page=True)
                page.locator('.xlt-replays li').first.get_by_role('link',name='Open Run →',exact=True).click()
                page.locator('.xlt-primary-kpis').wait_for(timeout=30000)
                page.locator('.xlt-progress strong').filter(has_text='Trainer update' if scenario=='async-replicas' else 'Step').wait_for(timeout=20000)
                context=parse_qs(urlparse(page.url).query)
                assert context['var-demo_generation']==[str(generation)]
                assert context['var-run_id'][0].startswith(f'g{generation}-')
                if scenario=='normal':page.get_by_text('No candidates reported',exact=True).wait_for(timeout=20000)
                if scenario=='recorded-error':expect(page.locator('.xlt-primary-kpis .xlt-kpi').last.locator('strong')).to_have_text('1',timeout=20000)
                page.get_by_role('button',name='Worker throughput 설명',exact=True).hover()
                page.get_by_role('tooltip').wait_for(timeout=10000)
                page.mouse.move(0,0)
                previous_url=page.url;previous_generation=generation
                page.screenshot(path=str(args.output/f'{scenario}-run.png'),full_page=True)
                page.locator('.xlt-primary-action').click()
                page.locator('.xlt-candidate-section').wait_for(timeout=20000)
                if scenario in ('slow-rollout','storage-regression'):
                    page.locator('.xlt-candidates article').first.get_by_role('button',name='Open Evidence →',exact=True).click()
                    page.get_by_role('complementary',name='Evidence detail').wait_for()
                    page.keyboard.press('Escape')
                    page.locator('.xlt-candidates article').first.get_by_role('button',name='Deep Dive →',exact=True).click()
                    page.get_by_role('heading',name='Key Findings',exact=True).wait_for()
                    page.reload();page.get_by_role('heading',name='Key Findings',exact=True).wait_for()
                response=page.request.post(origin+'/api/plugin-proxy/xlayer-telemetry-app/demo-control/ui-result',data={
                    'generation':generation,'state':'passed','checks':['Explorer → Run → Investigation; generation and context retained','KPI expected state and keyboard-accessible Tooltip','Old replay URL withheld after scenario change']})
                assert response.ok
                page.goto(origin+'/a/xlayer-telemetry-app/demo?'+urlencode({'var-demo_scenario':scenario,'theme':'dark','timezone':'UTC'}))
                page.locator('.xlt-replays li').first.wait_for(timeout=20000)
                for width in (1024,390):
                    page.set_viewport_size({'width':width,'height':900})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    page.screenshot(path=str(args.output/f'{scenario}-dark-{width}.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':1000})
                result.update(status='passed',workflow='complete')
                (args.output/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
            # Stop preserves validation summaries; Reset clears the replay and invalidates old URLs.
            page.get_by_role('button',name='Stop scenario',exact=True).click()
            page.wait_for_function("document.querySelector('.xlt-scenario-controls [role=status]')?.textContent.startsWith('stopped')",timeout=60000)
            assert not page.locator('.xlt-replays a').count()
            page.get_by_role('button',name='Reset demo',exact=True).click()
            page.wait_for_function("document.querySelector('.xlt-scenario-controls [role=status]')?.textContent.startsWith('idle')",timeout=60000)
            assert not page.locator('.xlt-replays li').count()
            report['lifecycle']='Start/switch/Stop/Reset and expired URL verified; repeated scenario in --scenarios exercises Replay'
            assert not report['browser_errors'],report['browser_errors']
        except Exception as error:
            report['failure']=str(error)[:3000]
            page.screenshot(path=str(args.output/'failure.png'),full_page=True)
            raise
        finally:
            (args.output/'validation.json').write_text(json.dumps(report,indent=2)+'\n');browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--scenarios',nargs='+',required=True);parser.add_argument('--browser')
    run(parser.parse_args())
