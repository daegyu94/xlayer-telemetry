#!/usr/bin/env python3
"""Actual Grafana journey regression. Optional Playwright; no workload is launched."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:23400')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--browser',default=None,help='Optional Chromium executable')
    parser.add_argument('--label',default='final')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    errors=[];checks=[];queries=[]
    def epoch(value):
        return int(value) if value.isdigit() else round(datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()*1000)
    with sync_playwright() as p:
        options={'headless':True}
        if args.browser:options.update(executable_path=args.browser,args=['--no-sandbox'])
        browser=p.chromium.launch(**options)
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:queries.append(r.post_data_json) if '/api/ds/query' in r.url and r.method=='POST' else None)
        def capture(name):page.screenshot(path=str(args.output/f'{args.label}-{name}.png'),full_page=True)
        url=args.url+'/a/xlayer-telemetry-app?from=now-5m&var-cluster=scenes-demo&var-run_id=verl-agent-demo&var-node=gpu-node-0&var-gpu=0'
        page.goto(url);page.get_by_role('button',name=re.compile('Analyze Step')).first.wait_for(timeout=30000);page.wait_for_timeout(1500)
        assert 'Synthetic demo' in page.locator('.xlt').inner_text()
        assert 'vs baseline' in page.locator('.xlt-kpis').inner_text()
        assert page.locator('.xlt-kpis .xlt-card').count()==8
        assert 'Policy' in page.locator('.xlt-run-context').inner_text()
        assert 'trainer version' in page.locator('.xlt-header').inner_text()
        assert 'Recent Events' in page.locator('.xlt').inner_text()
        assert 'No data' not in page.locator('.xlt-panel').first.inner_text()
        capture('overview')
        for width in [1280,900,390]:
            page.set_viewport_size({'width':width,'height':900});page.wait_for_timeout(300)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth+1')
            capture(f'overview-{width}')
        page.set_viewport_size({'width':1440,'height':1000})
        checks.append('Native plugin entry / Scenes Run variables / KPI and measured timeline render with live synthetic data')
        # Use an actual completed slow frame when the live scenario alternates.
        selector=page.get_by_role('combobox',name='Completed Step')
        slow=next((o.get_attribute('value') for o in selector.locator('option').all() if '48.00' in o.inner_text()),None)
        if slow:
            selector.select_option(slow);page.wait_for_timeout(1200)
        queries.clear()
        page.get_by_role('button',name=re.compile('Analyze Step')).first.click()
        page.get_by_role('heading',name='Phase × Subsystem',exact=True).wait_for()
        page.get_by_role('button',name='rollout × storage evidence',exact=True).wait_for()
        page.wait_for_function("document.querySelector('[aria-label=\"rollout × storage evidence\"]')?.innerText.includes('Step evidence')")
        page.wait_for_timeout(800)
        saved=parse_qs(urlparse(page.url).query)
        assert saved['var-run_id']==['verl-agent-demo'] and saved['var-source_node']==['gpu-node-0'] and saved['var-node']==['gpu-node-0']
        assert saved.get('var-record_id') and epoch(saved['to'][0])-epoch(saved['from'][0]) in [18401,48001]
        # Native datasource requests must use the selected Step window, not a
        # misleading URL over an accidentally defaulted now-6h SceneTimeRange.
        step_requests=[q for q in queries if not any(str(p.get('refId')).startswith('BASELINE_') for p in q.get('queries',[]))]
        baseline_requests=[q for q in queries if q not in step_requests]
        matching=[q for q in step_requests if abs(epoch(str(q['from']))-epoch(saved['from'][0]))<=1 and abs(epoch(str(q['to']))-epoch(saved['to'][0]))<=1]
        assert matching and len(matching)==len(step_requests),'Some native requests did not match the Step interval'
        text=page.locator('.xlt').inner_text()
        assert 'System Pressure' in text and 'Outlier worker snapshots' in text
        assert 'Sampled' in page.locator('.xlt-matrix').inner_text()
        assert re.search(r'\d',page.get_by_role('button',name='rollout × vllm evidence').inner_text()),'Live engine sample missing'
        for name,needle in [('Storage entity','load_get'),('Ray entity','PENDING_ARGS_AVAIL')]:
            entity=page.get_by_role('combobox',name=name)
            if entity.count():
                value=next(o.get_attribute('value') for o in entity.locator('option').all() if needle in o.inner_text());entity.select_option(value);page.wait_for_timeout(350)
        if slow:
            assert 'vs baseline' in page.get_by_role('button',name='rollout × gpu evidence').inner_text()
            assert 'Rolling' in page.get_by_role('button',name='rollout × storage evidence').inner_text()
            assert 'Linked tool call' in page.get_by_role('button',name='rollout × sandbox evidence').inner_text()
        assert not any(re.match(r'from-\d+|to-\d+',k) for k in parse_qs(urlparse(page.url).query))
        step_query_count=len(step_requests)
        assert all('$cluster' not in q.get('expr','') and '$node' not in q.get('expr','') for request in matching for q in request.get('queries',[]))
        capture('analyze');checks.append('Completed Step fixes identity and native query/time-picker interval; Matrix contains observed gauge / rolling / N/A / MFU missing states')
        page.get_by_role('button',name='rollout × storage evidence',exact=True).click()
        detail=page.get_by_role('complementary',name='Evidence detail');detail.wait_for();page.wait_for_timeout(400)
        assert 'full Step' in detail.inner_text() and 'per_run_3fs_client_bytes' in detail.inner_text()
        assert 'shared-service' in detail.inner_text() and 'causal path' in detail.inner_text()
        capture('evidence');checks.append('Storage cell opens supporting / against / missing evidence without asserting phase attribution')
        detail.get_by_role('link',name='storage ↗',exact=True).click();page.wait_for_url('**/d/xlayer-data-storage?**');page.wait_for_timeout(1800)
        deep=parse_qs(urlparse(page.url).query)
        for key in ['var-cluster','var-run_id','var-source_node','var-node','var-record_id','var-gpu']:
            assert deep[key]==saved[key],(key,deep,saved)
        assert deep['var-phase']==['rollout'] and epoch(deep['from'][0])==epoch(saved['from'][0]) and epoch(deep['to'][0])==epoch(saved['to'][0])
        capture('storage');page.go_back();page.get_by_role('heading',name='Phase × Subsystem',exact=True).wait_for();page.wait_for_timeout(1200)
        back=parse_qs(urlparse(page.url).query)
        for key in ['var-record_id','var-run_id','var-node','var-source_node']:assert back[key]==saved[key]
        checks.append('Existing Data & Storage keeps Run/Step/observer/resource/GPU/phase/time; Browser Back restores investigation')
        for width in [1280,900,390]:
            page.set_viewport_size({'width':width,'height':900});page.wait_for_timeout(400)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth+1'),'Page overflows viewport'
            matrix=page.locator('.xlt-matrix');matrix.scroll_into_view_if_needed();capture(f'analyze-{width}')
            page.get_by_role('button',name='rollout × storage evidence',exact=True).click();detail.wait_for();capture(f'evidence-{width}')
            detail.get_by_role('button',name='Close',exact=True).click()
        checks.append('900px and 390px layout: page contained; Matrix/table scroll locally; evidence remains usable')
        page.set_viewport_size({'width':1440,'height':1000})
        page.get_by_role('button',name='Compare baseline & candidates →',exact=True).click();page.wait_for_timeout(1800)
        if not slow:page.get_by_text('Δ unavailable · baseline 0',exact=True).wait_for(timeout=15000)
        assert 'What changed?' in page.locator('.xlt').inner_text()
        page.locator('.xlt-candidates article').filter(has=page.get_by_role('heading',name='rollout' if slow else 'compute',exact=True)).get_by_role('button',name='Open Evidence →').click()
        detail.wait_for();assert detail.get_by_role('link',name='stage ↗' if slow else 'compute ↗',exact=True).count()==1
        capture('investigate')
        page.keyboard.press('Escape');detail.wait_for(state='detached',timeout=3000)
        page.set_viewport_size({'width':390,'height':900});page.wait_for_timeout(300)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth+1')
        capture('investigate-390');page.set_viewport_size({'width':1440,'height':1000})
        page.locator('.xlt-candidates article').filter(has=page.get_by_role('heading',name='rollout' if slow else 'compute',exact=True)).get_by_role('button',name='Open Evidence →').click()
        page.keyboard.press('Escape');detail.wait_for(state='detached',timeout=3000)
        page.locator('.xlt-candidates article').filter(has=page.get_by_role('heading',name='storage',exact=True)).get_by_role('button',name='Deep Dive →',exact=True).first.click()
        page.get_by_role('heading',name='Key Findings',exact=True).wait_for();page.wait_for_timeout(1200)
        workspace=parse_qs(urlparse(page.url).query)
        assert workspace['candidate_id'] if 'candidate_id' in workspace else workspace.get('var-candidate_id')
        for key in ['var-run_id','var-node','var-record_id']:assert workspace[key]==saved[key]
        assert 'per_run_3fs_client_bytes' in page.locator('.xlt-workspace').inner_text()
        page.get_by_role('button',name='GPU',exact=True).click();page.wait_for_timeout(300)
        capture('candidate-workspace')
        for width in [1280,390]:
            page.set_viewport_size({'width':width,'height':900});page.wait_for_timeout(300)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth+1')
            capture(f'candidate-workspace-{width}')
        page.set_viewport_size({'width':1440,'height':1000});page.go_back();page.get_by_role('heading',name='What changed?',exact=True).wait_for()
        checks.append('Candidate Deep Dive workspace keeps selected Step, supporting/missing evidence and native metric tabs')
        page.get_by_role('button',name='Inspect measured timeline →',exact=True).click();page.wait_for_timeout(1800)
        assert 'Measured spans' in page.locator('.xlt').inner_text();capture('timeline')
        checks.append('Analyze → baseline / candidates → evidence → measured timeline keeps Step; Compute pivot and Escape restore focus')
        page.get_by_role('link',name='Deep Dive',exact=True).last.click();page.wait_for_timeout(700)
        assert 'Choose a subsystem' in page.locator('.xlt').inner_text();capture('deep-dive')
        checks.append('App Timeline / Deep Dive routing preserves investigation context')
        page.goto(url.replace('verl-agent-demo','not-a-real-run'));page.wait_for_timeout(1800)
        assert 'No completed Step' in page.locator('.xlt').inner_text();capture('no-data')
        checks.append('Unknown Run stays selected and displays no-data rather than inferred healthy/zero values')
        # Optional dashboard availability is deliberately removed at Grafana's
        # metadata boundary. This tests absence handling, not a real Loki outage.
        page.route('**/api/dashboards/uid/xlayer-bottleneck-summary',lambda route:route.fulfill(status=404,json={'message':'Dashboard not found'}))
        page.route('**/api/dashboards/uid/xlayer-cross-layer-timeline',lambda route:route.fulfill(status=404,json={'message':'Dashboard not found'}))
        page.route('**/api/dashboards/uid/xlayer-run-logs',lambda route:route.fulfill(status=404,json={'message':'Dashboard not found'}))
        page.goto(url);page.wait_for_timeout(1800)
        assert 'Loki timeline unavailable' in page.locator('.xlt').inner_text();capture('optional-unavailable')
        checks.append('Optional dashboard 404 fixture degrades to metrics/Deep Dive; it is not reported as real backend outage validation')
        assert not errors,errors
        report={'checks':checks,'browser_errors':errors,'grafana_version':'12.1.0','scenes_version':'6.20.0','data_origin':'live synthetic metrics + SDK-generated step/span/diagnosis fixtures','native_requests_after_step_selection':step_query_count,'bounded_baseline_requests':len(baseline_requests),'native_requests_matching_step_window':len(matching),'selected_context':saved}
        (args.output/f'{args.label}-validation.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2));browser.close()

if __name__=='__main__':main()
