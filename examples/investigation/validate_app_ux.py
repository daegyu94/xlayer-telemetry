"""CPU artifact + actual owned Grafana App UX checks; no GPU/veRL benchmark."""
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.parse import parse_qs,urlparse

from xlayer_telemetry.fileio import atomic_write_text
from xlayer_telemetry.manifest import make_agent_rl_manifest,write_manifest
from xlayer_telemetry.step_history import StepHistoryWriter
from xlayer_telemetry.operations.runs import publish


def fixture(root):
    root.mkdir(parents=True,exist_ok=False)
    for name,model in [('compare-a','CPU-only synthetic fixture'),('compare-b','CPU-only synthetic fixture'),('compare-other','Different synthetic workload'),
                       ('resource-a','Synthetic saved diagnosis'),('resource-b','Synthetic saved diagnosis')]:
        run=root/name
        write_manifest(run/'telemetry-manifest.json',make_agent_rl_manifest(run_id=name,configuration={
            'cluster':'scenes-demo','observer_node':'gpu-a','model_identifier':model,'execution_mode':'sync',
            'workload_fingerprint':'cpu-file-roundtrip-v1','data_origin':'synthetic'}))
        writer=StepHistoryWriter(run/'telemetry-events/verl-steps.jsonl',run_id=name,node='gpu-a',worker_id='driver')
        for step in range(3):
            start=time.perf_counter()
            payload=run/'payload.bin';payload.write_bytes(b'x'*4096);assert payload.read_bytes()==b'x'*4096
            elapsed=time.perf_counter()-start
            record=writer.append({'step':step,'data':{'perf/time_per_step':elapsed,'timing_s/gen':elapsed,'perf/total_num_tokens':100}})
        payload.unlink()
        atomic_write_text(run/'telemetry-health.json',json.dumps({'observed_at':time.time(),'workload':{'status':'exited','exit_code':0}}))
        if name.startswith('resource-'):
            # Explicit synthetic saved-report fixture, not measured GPU/storage
            # telemetry. The periodic resource window differs from every Step.
            end=record['analysis_window']['end']
            window={'start':end+1,'end':end+11,'accuracy':'sampled'}
            report={'record_type':'bottleneck_diagnosis','run_id':name,'data_origin':'synthetic',
                'node':'gpu-a','trigger':'periodic','trigger_record_id':None,
                'generated_at':'2026-10-10T00:00:00Z','analysis_window':window,
                'comparison':{'current_interval':window,'signals':[
                    {'signal':'storage_rpc_latency','current':1 if name=='resource-a' else 1000,
                     'unit':'s' if name=='resource-a' else 'ms','scope':'shared_service','window_statistic':'mean',
                     'labels':{'node':'gpu-b','instance':'synthetic-endpoint'}}]}}
            atomic_write_text(run/'diagnostics/diagnostics.jsonl',json.dumps(report)+'\n')
    return root


def validate(url, output, browser_path=None):
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True,exist_ok=False)
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=browser_path)
        page=browser.new_page(viewport={'width':1440,'height':1100},locale='en-US',timezone_id='UTC')
        page.on('pageerror',lambda error:errors.append(str(error)))
        route=url+'/a/xlayer-telemetry-app/runs?var-cluster=scenes-demo&var-run_id=.*&from=now-30m&to=now'
        for index,theme in enumerate(('light','dark','light')):
            page.goto(route+'&theme='+theme,wait_until='domcontentloaded')
            page.get_by_role('checkbox',name='Compare compare-a',exact=True).wait_for(timeout=30000)
            page.get_by_role('checkbox',name='Compare compare-a',exact=True).check()
            page.get_by_role('checkbox',name='Compare compare-b',exact=True).check()
            page.get_by_text('Comparability: Verified',exact=True).wait_for(timeout=10000)
            page.screenshot(path=str(output/f'runs-{index+1}-{theme}-desktop.png'),full_page=True)
            share=page.get_by_text('Share link',exact=True);share.click()
            href=page.locator('details').filter(has=page.get_by_text('Share link',exact=True)).locator('a').get_attribute('href')
            assert 'var-compare_run_a=' in href and 'var-compare_run_b=' in href and 'theme='+theme in href
            page.goto(url+href,wait_until='domcontentloaded')
            page.get_by_text('Comparability: Verified',exact=True).wait_for(timeout=20000)
            page.get_by_role('checkbox',name='Compare compare-b',exact=True).uncheck()
            page.get_by_role('checkbox',name='Compare compare-other',exact=True).check()
            page.get_by_text('Comparability: Incomparable',exact=True).wait_for(timeout=10000)
            panel=page.get_by_role('region',name='Run Comparison') if page.get_by_role('region',name='Run Comparison').count() else page.locator('[aria-label="Run Comparison"]')
            assert panel.get_by_text('N/A',exact=True).count()>=1
            page.get_by_label('Search Runs',exact=True).fill('compare-a')
            assert page.get_by_role('checkbox',name='Compare compare-b',exact=True).count()==0
            page.get_by_label('Search Runs',exact=True).fill('')
            page.get_by_role('link',name='compare-a: Saved Step Evidence →',exact=True).click()
            page.get_by_role('navigation',name='XLayer investigation').wait_for(timeout=20000)
            query=parse_qs(urlparse(page.url).query)
            page.wait_for_function("new URLSearchParams(location.search).get('var-run_id')==='compare-a'",timeout=10000)
            page.screenshot(path=str(output/f'evidence-{index+1}-{theme}.png'),full_page=True)
            query=parse_qs(urlparse(page.url).query)
            assert query.get('var-run_id')==['compare-a'] and 'var-record_id' in query and query.get('theme')==[theme], json.dumps(query)
            page.go_back(wait_until='domcontentloaded')
            page.get_by_role('heading',name='Run Comparison',exact=True).wait_for(timeout=20000)
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(output/f'runs-{index+1}-{theme}-mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            page.set_viewport_size({'width':1440,'height':1100})
        page.goto(route+'&theme=dark',wait_until='domcontentloaded')
        page.get_by_role('checkbox',name='Compare resource-a',exact=True).check()
        page.get_by_role('checkbox',name='Compare resource-b',exact=True).check()
        panel=page.locator('[aria-label="Run Comparison"]')
        metric=panel.get_by_role('row').filter(has_text='storage_rpc_latency')
        metric.wait_for(timeout=10000)
        assert metric.locator('td').nth(1).inner_text().startswith('1 s')
        assert metric.locator('td').nth(2).inner_text().startswith('1,000 ms')
        assert metric.locator('td').nth(3).inner_text()=='N/A'
        link=metric.get_by_role('link',name='resource-a: Resource Evidence →',exact=True)
        query=parse_qs(urlparse(link.get_attribute('href')).query)
        saved_link=page.get_by_role('row').filter(has=page.get_by_role('checkbox',name='Compare resource-a',exact=True)).get_by_role('link',name='Saved Step →',exact=True)
        step_query=parse_qs(urlparse(saved_link.get_attribute('href')).query)
        assert query['from']!=step_query['from'] and query['to']!=step_query['to']
        assert 'var-record_id' not in query and query['var-node']==['gpu-b'] and query['theme']==['dark']
        page.screenshot(path=str(output/'resource-units-window-desktop.png'),full_page=True)
        link.click()
        page.get_by_role('navigation',name='XLayer investigation').wait_for(timeout=20000)
        actual=parse_qs(urlparse(page.url).query)
        assert actual['from']==query['from'] and actual['to']==query['to'] and actual.get('var-record_id') in (None,['.*'],['$__all'])
        page.go_back(wait_until='domcontentloaded')
        metric=page.locator('[aria-label="Run Comparison"]').get_by_role('row').filter(has_text='storage_rpc_latency')
        metric.wait_for(timeout=10000)
        metric.get_by_role('link',name='Resource Timeline →',exact=True).first.click()
        page.get_by_role('navigation',name='XLayer investigation').wait_for(timeout=20000)
        actual=parse_qs(urlparse(page.url).query)
        assert actual['from']==query['from'] and actual['to']==query['to'] and actual.get('var-record_id') in (None,['.*'],['$__all'])
        page.screenshot(path=str(output/'resource-timeline.png'),full_page=True)
        # Missing catalog and failure are browser response faults, not a backend outage.
        endpoint='**/api/dashboards/uid/xlayer-run-catalog'
        for label,status in [('missing',404),('failure',503)]:
            def failure_handler(request_route,request=None):
                request_route.fulfill(status=status,content_type='application/json',body='{}')
            page.route(endpoint,failure_handler)
            page.goto(route+'&theme=light',wait_until='domcontentloaded')
            page.get_by_text('저장 catalog가 게시되지 않았습니다.',exact=False).wait_for(timeout=10000) if status==404 else page.get_by_role('alert').wait_for(timeout=10000)
            page.screenshot(path=str(output/f'runs-{label}.png'),full_page=True)
            page.unroute(endpoint)
        browser.close()
    if errors:raise AssertionError('Browser errors: '+json.dumps(errors))
    (output/'validation.json').write_text(json.dumps({'status':'passed','loops':3,'themes':['light','dark'],
        'desktop':1440,'mobile':390,'browser_errors':errors,'scope':'CPU synthetic artifacts and actual Grafana; no physical cluster'},indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture-root',type=Path)
    parser.add_argument('--dashboard-output',type=Path)
    parser.add_argument('--url')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--browser')
    args=parser.parse_args()
    if args.fixture_root:
        if not args.dashboard_output:parser.error('--fixture-root requires --dashboard-output')
        print(json.dumps(publish(fixture(args.fixture_root),args.dashboard_output)))
    if args.url:
        if not args.output:parser.error('--url requires --output')
        validate(args.url,args.output,args.browser)


if __name__=='__main__':main()
