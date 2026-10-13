"""Run scenarios through one Scenario Explorer UI, including lifecycle isolation."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse,parse_qs,urlencode
from design_validate import owned_origin


def verify_progress_feedback(page,origin,capture=None):
    """Controller-response fixture in actual Grafana; no live replay is changed."""
    from copy import deepcopy
    from playwright.sync_api import expect
    original_url=page.url
    theme=parse_qs(urlparse(original_url).query).get('theme',['dark'])[0]
    fixture=page.request.get(origin+'/api/plugin-proxy/xlayer-telemetry-app/demo-control/status').json()
    fixture.update(generation=1000,selected='',status='idle',validation={},observations=[],error=None)
    posts=[]
    pattern='**/api/plugin-proxy/xlayer-telemetry-app/demo-control/*'
    def reply(route):
        action=urlparse(route.request.url).path.rsplit('/',1)[-1]
        if route.request.method=='POST':
            posts.append(action);fixture['generation']+=1
            if action in ('start','replay'):
                fixture.update(selected='normal',status='collecting',validation={'generation':{'state':'running','checks':[]}},observations=[],error=None)
            elif action=='stop':fixture['status']='stopped'
            elif action=='reset':fixture.update(selected='',status='idle',validation={},observations=[])
            else:raise AssertionError('Unexpected fixture command: '+action)
        route.fulfill(json=deepcopy(fixture))
    page.route(pattern,reply)
    try:
        page.goto(origin+'/a/xlayer-telemetry-app/demo?'+urlencode({'theme':theme,'var-demo_scenario':'normal'}))
        page.get_by_role('button',name='Run scenario',exact=True).click()
        bar=page.get_by_role('progressbar',name='Scenario stage progress')
        expect(bar).to_have_attribute('aria-valuenow','0')
        expect(page.get_by_role('button',name='Replay scenario',exact=True)).to_be_disabled()
        expect(page.get_by_role('button',name='Stop scenario',exact=True)).to_be_enabled()
        expect(page.get_by_role('button',name='Reset demo',exact=True)).to_be_enabled()
        assert '90' not in page.locator('.xlt-scenario-progress').inner_text()
        stage=page.locator('[data-stage=generation]')
        stage.locator('summary').click()
        page.evaluate("window.scenarioFlashes={};document.addEventListener('animationstart',event=>{if(event.animationName==='xlt-stage-flash'){const id=event.target.closest('[data-stage]').dataset.stage;window.scenarioFlashes[id]=(window.scenarioFlashes[id]||0)+1;}})")
        fixture['validation'].update(generation={'state':'passed','checks':['Fixture input ready']},collection={'state':'running','checks':[]})
        expect(stage).to_have_attribute('data-transition','passed',timeout=10000)
        expect(stage).to_have_attribute('open','')
        expect(bar).to_have_attribute('aria-valuenow','1')
        page.wait_for_function('window.scenarioFlashes.generation===1')
        if capture:capture('progress-stage-completed')
        with page.expect_response(lambda response:response.url.endswith('/demo-control/status')):pass
        assert page.evaluate('window.scenarioFlashes.generation')==1,'Polling replayed the completion flash'
        page.locator('.xlt-sidebar').get_by_role('link',name='Dashboards',exact=True).click()
        page.wait_for_url('**/dashboards?**')
        page.go_back();page.wait_for_url('**/demo?**')
        expect(bar).to_have_attribute('aria-valuenow','1',timeout=10000)
        expect(page.get_by_role('button',name='Replay scenario',exact=True)).to_be_disabled()
        page.emulate_media(reduced_motion='reduce')
        fixture['validation'].update(collection={'state':'passed','checks':[]},query={'state':'running','checks':[]})
        completed=page.locator('[data-stage=collection]')
        expect(completed).to_have_attribute('data-transition','passed',timeout=10000)
        assert completed.locator('.xlt-stage-flash').evaluate('(element)=>getComputedStyle(element).animationName')=='none'
        assert page.locator('[data-stage=query] .xlt-stage-mark').evaluate('(element)=>getComputedStyle(element).animationName')=='none'
        fixture['validation']['query']={'state':'failed','checks':[]}
        fixture['status']='error'
        expect(page.locator('[data-stage=query]')).to_have_attribute('data-transition','failed',timeout=10000)
        page.emulate_media(reduced_motion='no-preference')
        page.get_by_role('button',name='Replay scenario',exact=True).click()
        expect(bar).to_have_attribute('aria-valuenow','0')
        expect(page.locator('.xlt-validation-stages [data-transition]')).to_have_count(0)
        page.get_by_role('button',name='Stop scenario',exact=True).click()
        expect(bar).to_have_count(0)
        page.get_by_role('button',name='Reset demo',exact=True).click()
        page.get_by_role('button',name='Run scenario',exact=True).click()
        expect(bar).to_have_attribute('aria-valuenow','0')
        fixture.update(status='ready',validation={name:{'state':'passed','checks':[]} for name in ('generation','collection','query','diagnosis')},observations=[{
            'scenario':'normal','step':1,'boundary_scope':'rl_step','context':{'cluster':'fixture','run_id':'fixture-run','source_node':'fixture-node','record_id':'fixture-step','from':'1000','to':'2000'}}])
        expect(page.locator('[data-stage=diagnosis]')).to_have_attribute('data-state','passed',timeout=10000)
        expect(bar).to_have_count(0)
        expect(page.get_by_role('button',name='Replay scenario',exact=True)).to_be_enabled()
        expect(page.locator('.xlt-replays').get_by_role('link',name='Open Run →',exact=True)).to_be_visible()
        expect(page.locator('[data-stage=ui]')).to_have_attribute('data-state','pending')
        assert posts==['start','replay','stop','reset','start'],posts
        if capture:capture('progress-ready')
    finally:
        page.unroute(pattern,reply)
        page.emulate_media(reduced_motion='no-preference')
        page.goto(original_url if original_url.startswith(origin) else origin+'/a/xlayer-telemetry-app/demo')


def run(args):
    from playwright.sync_api import sync_playwright,expect
    from browser_validate import EntryDiagnostics
    from ci_demo import datasource_error_result
    origin=owned_origin(args.url);args.output.mkdir(parents=True,exist_ok=False)
    report={'grafana':origin,'scenarios':[],'browser_errors':[],'scope':'One Grafana; per-replay synthetic stores; no hardware validation'}
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'],**({'executable_path':args.browser} if args.browser else {}))
        page=browser.new_page(viewport={'width':1440,'height':1000},timezone_id='Asia/Seoul',locale='en-US')
        page.on('pageerror',lambda error:report['browser_errors'].append(str(error)))
        diagnostics=EntryDiagnostics(page,args.output,'scenario',report['browser_errors'])
        previous_url=None;previous_generation=None;signatures={}
        try:
            page.goto(origin+'/a/xlayer-telemetry-app/demo?theme=light&timezone=UTC')
            verify_progress_feedback(page,origin,lambda name:page.screenshot(path=str(args.output/(name+'.png')),full_page=True))
            report['progress_feedback']='Stage progress, transition-only flash, duplicate-run guard, interrupt controls, navigation and reduced motion; controller-response fixture'
            page.locator('.xlt-scenario-controls').wait_for(timeout=30000)
            # Cancel before a completed pair exists: no stale publication may reappear.
            page.get_by_role('button',name='Run scenario',exact=True).click()
            page.wait_for_function("document.querySelector('.xlt-scenario-controls [role=status]')?.textContent.startsWith('collecting')",timeout=30000)
            progress=page.locator('.xlt-scenario-progress')
            expect(progress).to_be_visible()
            expect(progress).to_contain_text('데이터를 수집하고 있습니다')
            expect(progress.get_by_role('progressbar',name='Scenario stage progress')).to_be_visible()
            expect(page.get_by_role('button',name='Replay scenario',exact=True)).to_be_disabled()
            page.get_by_role('button',name='Stop scenario',exact=True).click()
            page.wait_for_function("document.querySelector('.xlt-scenario-controls [role=status]')?.textContent.startsWith('stopped')",timeout=60000)
            expect(progress).to_have_count(0)
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
                expect(progress).to_have_count(0)
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
                assert page.evaluate("['.xlt-progress .xlt-eyebrow','.xlt-progress small'].every(selector=>parseFloat(getComputedStyle(document.querySelector(selector)).fontSize)>=15)"),'Run labels and descriptions are too small'
                if scenario=='normal':
                    page.locator('.xlt-mfu-strip').wait_for(timeout=20000)
                    assert page.locator('.xlt-mfu-strip').evaluate('(element)=>parseFloat(getComputedStyle(element).fontSize)>=15')
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
                if scenario=='normal':
                    def fail_loki(route):
                        queries=route.request.post_data_json.get('queries',[])
                        if any(query.get('datasource',{}).get('uid')=='telemetry-loki' for query in queries):
                            route.fulfill(status=200,json=datasource_error_result(queries))
                        else:route.continue_()
                    page.route('**/api/ds/query*',fail_loki)
                    page.reload()
                    error=page.locator('.xlt-primary-kpis .xlt-kpi').last.locator('strong')
                    expect(error).to_have_text('Query error',timeout=30000)
                    assert error.evaluate('(element)=>parseFloat(getComputedStyle(element).fontSize)===15'),'Query error uses the numeric KPI size'
                    page.screenshot(path=str(args.output/'query-error.png'),full_page=True)
                    page.unroute('**/api/ds/query*',fail_loki)
                    page.reload();page.locator('.xlt-primary-kpis').wait_for(timeout=30000)
                    expect(page.locator('.xlt-progress strong')).to_contain_text('Step',timeout=30000)
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
                dark_run=urlparse(previous_url);dark_context=parse_qs(dark_run.query);dark_context['theme']=['dark']
                page.goto(dark_run._replace(query=urlencode(dark_context,doseq=True)).geturl())
                page.locator('.xlt-primary-kpis').wait_for(timeout=30000)
                page.wait_for_function('document.documentElement.scrollWidth<=innerWidth+1',timeout=10000)
                page.screenshot(path=str(args.output/f'{scenario}-run-dark-390.png'),full_page=True)
                page.goto(origin+'/a/xlayer-telemetry-app/demo?'+urlencode({'var-demo_scenario':scenario,'theme':'dark','timezone':'UTC'}))
                page.locator('.xlt-replays li').first.wait_for(timeout=20000)
                page.set_viewport_size({'width':1440,'height':1000})
                result.update(status='passed',workflow='complete')
                (args.output/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
            page.goto(origin+'/a/xlayer-telemetry-app/runs?theme=light')
            description=page.locator('.xlt-header .xlt-page-description')
            description.wait_for(timeout=30000)
            assert description.evaluate('(element)=>element.getBoundingClientRect().height<=parseFloat(getComputedStyle(element).lineHeight)+1'),'Run Explorer description wraps despite desktop space'
            page.screenshot(path=str(args.output/'runs-description.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':900})
            page.wait_for_function('document.documentElement.scrollWidth<=innerWidth+1',timeout=10000)
            page.screenshot(path=str(args.output/'runs-description-390.png'),full_page=True)
            page.goto(origin+'/a/xlayer-telemetry-app/demo?'+urlencode({'var-demo_scenario':args.scenarios[-1]}))
            page.locator('.xlt-replays li').first.wait_for(timeout=20000)
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
            diagnostics.fail(error,'Scenario Explorer / '+(report['scenarios'][-1]['workflow'] if report['scenarios'] else 'initial controls'))
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
