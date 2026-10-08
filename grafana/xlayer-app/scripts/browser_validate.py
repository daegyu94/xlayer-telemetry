#!/usr/bin/env python3
"""Actual Grafana journey regression. Optional Playwright; no workload is launched."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from importlib.metadata import version
import re
import sys
import threading
import time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from playwright.sync_api import sync_playwright, Error as PlaywrightError
from ci_demo import datasource_error_result



def diagnostic_text(value, limit=24000):
    """Bound debug output and remove common credential forms before writing."""
    text = str(value)
    text = re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[redacted]@', text)
    text = re.sub(r'(?i)\bBearer\s+[^\s,;"\']+', 'Bearer [redacted]', text)
    text = re.sub(r"""(?i)((?:password|token|secret|api[_-]?key|authorization)["']?\s*[:=]\s*)["']?[^\s,"'}]+""", r'\1[redacted]', text)
    return text[:limit]


def diagnostic_url(url):
    parsed = urlparse(url)
    host = parsed.hostname or ''
    if parsed.port:
        host += ':' + str(parsed.port)
    allowed = {'from','to','timezone','var-cluster','var-run_id','var-record_id','var-node','var-source_node','var-gpu','var-engine','var-worker','var-phase','var-phase_worker'}
    context = [(key, diagnostic_text(value, 1000)) for key, values in parse_qs(parsed.query).items() if key in allowed for value in values[:4]]
    return urlunparse((parsed.scheme, host, parsed.path, '', urlencode(context), ''))


class EntryDiagnostics:
    """Observe safe metadata only; never retain datasource expressions or headers."""
    def __init__(self, page, output, label, browser_errors):
        self.page, self.output, self.label, self.browser_errors = page, output, label, browser_errors
        self.query_responses, self.http_failures, self.console_errors = [], [], []
        page.on('response', self.response)
        page.on('console', self.console)

    def console(self, message):
        if message.type == 'error' and len(self.console_errors)<20:
            self.console_errors.append(diagnostic_text(message.text, 1000))

    def response(self, response):
        parsed = urlparse(response.url)
        if response.status>=400 and len(self.http_failures)<20:
            self.http_failures.append({'path':parsed.path[:200], 'status':response.status})
        if '/api/ds/query' not in parsed.path or len(self.query_responses)>=60:
            return
        request = response.request
        try:
            payload = request.post_data_json or {}
        except (ValueError, TypeError, PlaywrightError):
            payload = {}
        queries = payload.get('queries', []) if isinstance(payload, dict) else []
        if not isinstance(queries, list):
            queries = []
        self.query_responses.append({'http_status':response.status, 'query_count':len(queries),
            'queries':[{'ref_id':diagnostic_text(query.get('refId',''),80),
                        'datasource_uid':diagnostic_text((query.get('datasource') or {}).get('uid',''),80),
                        'datasource_type':diagnostic_text((query.get('datasource') or {}).get('type',''),80)}
                       for query in queries[:12] if isinstance(query,dict) and isinstance(query.get('datasource') or {},dict)]})

    def fail(self, error):
        record={'stage':'initial App entry / Analyze Step readiness', 'url':diagnostic_url(self.page.url),
                'failure':diagnostic_text(error,2000), 'browser_errors':[diagnostic_text(value,1000) for value in self.browser_errors[:20]],
                'console_errors':self.console_errors, 'initial_query_response_summaries':self.query_responses,
                'http_failures':self.http_failures,
                'limits':{'body_text':24000,'browser_errors':20,'query_responses':60,'queries_per_response':12},
                'telemetry_note':'Browser entry diagnostics. HTTP status/DOM do not establish backend failure or workload health.'}
        try:
            record['body_text']=diagnostic_text(self.page.locator('body').inner_text(timeout=3000))
            record['completed_step_option_count']=self.page.locator('[aria-label="Completed Step"] option').count()
        except Exception as capture_error:
            record['body_capture_error']=diagnostic_text(capture_error,500)
        try:
            self.page.screenshot(path=str(self.output/f'{self.label}-entry-failure.png'),full_page=False,timeout=5000)
        except Exception as capture_error:
            record['screenshot_capture_error']=diagnostic_text(capture_error,500)
        (self.output/f'{self.label}-entry-failure.json').write_text(json.dumps(record,indent=2)+'\n')


def enter_app(page, url, diagnostics):
    """Preserve the original first-entry assertion; capture before rethrowing."""
    try:
        page.goto(url)
        page.get_by_role('button',name=re.compile('Analyze (Step|Trainer update|Observation)')).first.wait_for(timeout=30000)
    except Exception as error:
        try:
            diagnostics.fail(error)
        except Exception as capture_error:
            print('Entry diagnostics could not be written: '+diagnostic_text(capture_error,500),file=sys.stderr)
        raise


def datasource_boundary_checks(page, url, checks, capture):
    """Inject only the native datasource boundary; keep real Loki/other requests."""
    errors = []
    def gpu_request(route):
        body = route.request.post_data_json or {}
        queries = body.get('queries', [])
        return queries if any(any(metric in str(q.get('expr', '')) for metric in ('telemetry_gpu_utilization_percent', 'DCGM_FI_DEV_GPU_UTIL')) for q in queries) else None
    def error_fixture(route):
        queries = gpu_request(route)
        if queries is None:
            route.continue_()
        else:
            errors.append(len(queries))
            route.fulfill(status=200, json=datasource_error_result(queries))
    page.route('**/api/ds/query*', error_fixture)
    try:
        page.goto(url)
        page.get_by_role('heading', name='Run Overview', exact=True).wait_for(timeout=30000)
        page.wait_for_function("Array.from(document.querySelectorAll('.xlt-completed-detail summary')).some(el => el.innerText.includes('Diagnosis / Query Coverage') && /[1-9][0-9]* errors/.test(el.innerText))", timeout=15000)
        coverage = page.locator('details').filter(has=page.locator('summary').filter(has_text='Diagnosis / Query Coverage'))
        coverage.locator('summary').click()
        assert 'Synthetic browser boundary datasource error' in coverage.inner_text()
        assert page.get_by_role('combobox', name='Completed Step').locator('option').count() >= 3
        assert errors
        capture('datasource-error-fixture')
        checks.append('Structured Grafana /api/ds/query GPU error fixture preserves actual Loki Step navigation and exposes Query Coverage; browser boundary simulation, not a backend outage')
    finally:
        page.unroute_all(behavior='wait')
    delays = []
    def delayed_fixture(route):
        queries = gpu_request(route)
        if queries is None:
            route.continue_()
        else:
            response = route.fetch(timeout=10000)
            started = time.monotonic()
            threading.Event().wait(1.2)
            delays.append(time.monotonic() - started)
            route.fulfill(response=response)
    page.route('**/api/ds/query*', delayed_fixture)
    try:
        page.goto(url)
        page.get_by_role('heading', name='Run Overview', exact=True).wait_for(timeout=30000)
        page.get_by_role('button', name=re.compile('Analyze (Step|Trainer update|Observation)')).first.wait_for(timeout=15000)
        page.wait_for_timeout(1500)
        assert delays and min(delays) >= 1.1
        assert page.get_by_role('combobox', name='Completed Step').locator('option').count() >= 3
        assert 'Synthetic browser boundary datasource error' not in page.locator('.xlt').inner_text()
        capture('datasource-delay-fixture')
        checks.append('Real GPU query response delayed at browser boundary by 1.2s; Step navigation remains available and error fixture recovers; not backend latency measurement')
    finally:
        page.unroute_all(behavior='wait')
    loading_context_check(page, url, checks, capture)



def loading_context_check(page, url, checks, capture):
    """Exercise a native Run-variable transition after the old Run has data."""
    page.goto(url)
    page.get_by_role('button', name=re.compile('Analyze (Step|Trainer update|Observation)')).first.wait_for(timeout=15000)
    page.wait_for_timeout(500)
    snapshots = []
    reserved = []
    cancelled = []
    def delayed_unknown_run(route):
        body = route.request.post_data_json or {}
        queries = body.get('queries', [])
        matches = any('not-a-real-run' in str(query.get('expr', '')) and 'verl_step' in str(query.get('expr', '')) and 'verl-agent-demo' not in str(query.get('expr', '')) for query in queries)
        if not matches or reserved:
            route.continue_()
            return
        reserved.append(True)
        response = route.fetch(timeout=10000)
        threading.Event().wait(.3)
        snapshots.append(page.evaluate("""() => ({
          runs:new URLSearchParams(window.location.search).getAll('var-run_id'),
          application:Array.from(document.querySelectorAll('.xlt-kpis .xlt-card')).filter(card=>['Reward','Step time','Worker throughput','Reported rollout'].includes(card.querySelector('.xlt-eyebrow')?.innerText)).map(card=>({name:card.querySelector('.xlt-eyebrow')?.innerText,value:card.querySelector('strong')?.innerText,entity:card.querySelector('.xlt-entity')?.innerText||''})),
          completed:Array.from(document.querySelector('[aria-label="Completed Step"]')?.options||[]).filter(option=>option.value).map(option=>option.innerText)
        })"""))
        threading.Event().wait(.9)
        try:
            route.fulfill(response=response)
        except PlaywrightError as error:
            if 'Route is already handled!' not in str(error):
                raise
            cancelled.append('Native Scenes cancelled the superseded browser-delayed request')
    page.route('**/api/ds/query*', delayed_unknown_run)
    try:
        run = page.locator('.xlt-context-variable').nth(1)
        run.get_by_role('combobox').fill('not-a-real-run')
        page.get_by_role('option').filter(has_text='Hit enter to add').click()
        page.keyboard.press('Escape');page.keyboard.press('Tab')
        # Grafana's multi-value editor commits on blur; remove only the old Run.
        for button in run.get_by_role('button', name='Remove', exact=True).all():
            if button.locator('..').inner_text().strip() == 'verl-agent-demo':
                button.click()
                break
        page.keyboard.press('Escape');page.keyboard.press('Tab')
        page.wait_for_function("new URLSearchParams(window.location.search).getAll('var-run_id').includes('not-a-real-run') && !new URLSearchParams(window.location.search).getAll('var-run_id').includes('verl-agent-demo')", timeout=15000)
        page.get_by_text('No completed Step in this range. Select a Run/time range; Loki step history is optional.', exact=True).wait_for(state='attached', timeout=15000)
        page.unroute_all(behavior='wait')
        relevant = [snapshot for snapshot in snapshots if snapshot['runs']==['not-a-real-run']]
        assert relevant, 'A native Run transition must issue delayed unknown-Run queries'
        for snapshot in relevant:
            assert not snapshot['completed'], 'Previous Run completed Steps were retained during Loading'
            for value in snapshot['application']:
                assert 'verl-agent-demo' not in value['entity'], value
                assert not re.search(r'\d', value['value']), ('Old application KPI value during Loading', value)
        capture('loading-run-context')
        checks.append('Native Run-variable change with delayed datasource responses withholds previous Run application KPI/Step rows while Loading; resource scope remains separate')
    finally:
        page.unroute_all(behavior='wait')


def multi_worker_journey(args):
    errors, checks, queries, transitions = [], [], [], []
    with sync_playwright() as playwright:
        options = {'headless': True}
        if args.browser:
            options.update(executable_path=args.browser, args=['--no-sandbox'])
        browser = playwright.chromium.launch(**options)
        page = browser.new_page(viewport={'width':1440,'height':1000}, locale='en-US', timezone_id='UTC')
        page.add_init_script('''(() => { for (const name of ['pushState','replaceState']) { const original=history[name]; history[name]=function(...args) { const before=location.href,open=!!document.querySelector('.xlt-evidence'); const result=original.apply(this,args); if(before!==location.href)console.debug('XLAYER_CONTEXT '+JSON.stringify({before,after:location.href,evidence_open:open})); return result; }; } })();''')
        def record_transition(message):
            if not message.text.startswith('XLAYER_CONTEXT '):
                return
            raw=json.loads(message.text[len('XLAYER_CONTEXT '):]);before=parse_qs(urlparse(raw['before']).query);after=parse_qs(urlparse(raw['after']).query)
            changed={key:{'before':before.get(key),'after':after.get(key)} for key in set(before)|set(after) if before.get(key)!=after.get(key)}
            transitions.append({'changed':changed,'evidence_open':raw['evidence_open']})
            (args.output / f'{args.label}-context-transitions.json').write_text(json.dumps(transitions[-200:],indent=2)+'\n')
        page.on('console',record_transition)
        page.on('pageerror', lambda error: errors.append(str(error)))
        entry_diagnostics = EntryDiagnostics(page,args.output,args.label,errors)
        page.on('request', lambda request: queries.append(request.post_data_json) if '/api/ds/query' in request.url and request.method=='POST' else None)
        def capture(name):
            page.screenshot(path=str(args.output / f'{args.label}-{name}.png'), full_page=True)
        url = args.url + '/a/xlayer-telemetry-app?from=now-5m&var-cluster=scenes-demo&var-run_id=verl-agent-demo&var-node=gpu-node-0&var-gpu=0'
        enter_app(page,url,entry_diagnostics)
        selector = page.get_by_role('combobox', name='Completed Step')
        slow = next((option.get_attribute('value') for option in selector.locator('option').all() if '48.00' in option.inner_text()), None)
        assert slow, 'Multi-worker fixture must include a completed regression frame'
        selector.select_option(slow)
        page.wait_for_timeout(700)
        page.get_by_role('button', name=re.compile('Analyze (Step|Trainer update|Observation)')).first.click()
        page.get_by_role('heading', name='Phase × Subsystem', exact=True).wait_for()
        gpu_cell = page.get_by_role('button', name='rollout × gpu evidence', exact=True)
        page.wait_for_function('''document.querySelector('[aria-label="rollout × gpu evidence"]')?.innerText.includes('Ambiguous span')''', timeout=15000)
        assert gpu_cell.locator('b').inner_text() == '—'
        before = parse_qs(urlparse(page.url).query)
        capture('multi-ambiguous')
        checks.append('Multiple measured rollout workers keep execution-path Matrix ambiguous; no arbitrary span or synthetic phase value is selected')
        page.get_by_role('button', name='Worker Comparison', exact=True).click()
        page.get_by_role('heading', name='Measured Worker Comparison', exact=True).wait_for()
        workers = page.get_by_role('heading', name='Measured Worker Comparison', exact=True).locator('..')
        rows = workers.locator('tbody tr').filter(has_text='rollout-')
        assert rows.count() == 4
        outlier = rows.filter(has_text='rollout-3').first
        assert 'No matched peer cohort' not in outlier.inner_text()
        def peer_delta(row):
            text = row.locator('td').nth(3).inner_text()
            match = re.search(r'([-+]?\d[\d,]*(?:\.\d+)?)\s*%', text)
            assert match, ('Comparable worker delta not rendered', text)
            return float(match.group(1).replace(',', ''))
        assert peer_delta(outlier)>100
        assert all(abs(peer_delta(rows.filter(has_text=f'rollout-{index}').first))<20 for index in range(3))
        assert 'sampled' in workers.inner_text() or 'sample unavailable' in workers.inner_text()
        capture('multi-worker-comparison')
        workers.screenshot(path=str(args.output / f'{args.label}-worker-comparison-section.png'))
        outlier.get_by_role('button', name='Inspect worker →', exact=True).click()
        page.wait_for_function("new URLSearchParams(window.location.search).has('var-phase_worker')")
        selected = parse_qs(urlparse(page.url).query)
        key = dict(json.loads(selected['var-phase_worker'][0]))
        assert key['worker_id'] == 'rollout-3'
        assert selected['var-node'] == [key['node']] and selected['var-gpu'] == [str(key['gpu'])]
        for name in ('var-run_id','var-record_id','var-source_node','from','to'):
            assert selected[name] == before[name], (name, selected, before)
        page.wait_for_function('''document.querySelector('[aria-label="rollout × gpu evidence"]')?.innerText.includes('Sampled') && /[0-9]/.test(document.querySelector('[aria-label="rollout × gpu evidence"] b')?.innerText || '')''', timeout=15000)
        assert 'Sampled' in gpu_cell.inner_text()
        page.wait_for_timeout(600)
        selected = parse_qs(urlparse(page.url).query)
        capture('multi-selected-worker')
        checks.append('Four explicit rollout workers form a matched measured cohort; selecting worker sets phase_worker/resource node/GPU while preserving observer/Run/Step/time')
        gpu_cell.click()
        detail = page.get_by_role('complementary', name='Evidence detail')
        detail.wait_for();page.wait_for_timeout(400)
        detail.get_by_role('link', name='compute ↗', exact=True).click()
        page.wait_for_url('**/d/xlayer-compute-communication**')
        # Exercise the loaded dashboard. Its initialization can add native URL
        # state/history; going Back before it mounts races that pending routing.
        page.get_by_text('05 · Compute & Communication',exact=True).first.wait_for(timeout=30000)
        page.wait_for_function("new URLSearchParams(location.search).has('orgId')",timeout=15000)
        deep = parse_qs(urlparse(page.url).query)
        for name in ('var-phase_worker','var-node','var-gpu','var-run_id','var-record_id','var-source_node','from','to'):
            assert deep[name] == selected[name], (name, deep, selected)
        assert deep['var-phase'] == ['rollout']
        return_entries=0
        while return_entries<4:
            page.go_back(wait_until='domcontentloaded')
            return_entries+=1
            if urlparse(page.url).path.startswith('/a/xlayer-telemetry-app'):
                break
            assert urlparse(page.url).netloc==urlparse(args.url).netloc and urlparse(page.url).path.startswith('/d/'), 'Back navigated outside the bounded native dashboard history'
        assert urlparse(page.url).path.startswith('/a/xlayer-telemetry-app'), 'App entry absent from the last four native dashboard history entries'
        page.get_by_role('heading', name='Phase × Subsystem', exact=True).wait_for()
        assert parse_qs(urlparse(page.url).query)['var-phase_worker'] == selected['var-phase_worker']
        checks.append('Selected measured worker → Compute dashboard → browser Back preserves exact resource and observer/Step context')
        page.get_by_role('link', name='Overview', exact=True).last.click()
        page.wait_for_timeout(700)
        lifecycle = page.locator('details').filter(has=page.locator('summary').filter(has_text='Policy / KV Lifecycle'))
        lifecycle.locator('summary').click()
        assert '4 workers with applied-version events' in lifecycle.inner_text()
        assert lifecycle.locator('tbody tr').count() >= 4
        assert 'producer reported' in lifecycle.inner_text() and 'causality' in lifecycle.inner_text()
        capture('multi-policy-applied')
        checks.append('Explicit weights.applied events expose worker-applied policy coverage; trainer version and KV changes are not applied-boundary or causal evidence')
        for width in (1280,390):
            page.set_viewport_size({'width':width,'height':900})
            page.wait_for_timeout(250)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth+1')
            capture(f'multi-policy-{width}')
        page.set_viewport_size({'width':1440,'height':1000})
        datasource_boundary_checks(page, url, checks, capture)
        assert not errors, errors
        report = {'checks':checks, 'browser_errors':errors, 'data_origin':'live synthetic metrics + four explicit SDK rollout worker spans',
                  'grafana_version':'12.1.0', 'scenes_version':'6.20.0', 'browser_version':browser.version, 'playwright_version':version('playwright'), 'browser_locale':'en-US', 'browser_timezone':'UTC', 'selected_context':selected,
                  'multi_worker':True, 'worker_rows':4, 'native_dashboard_back_entries':return_entries, 'context_transition_count':len(transitions),
                  'datasource_fixtures':'Structured error and bounded delay at browser /api/ds/query boundary; not actual backend failure or latency measurement'}
        (args.output / f'{args.label}-validation.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report, indent=2))
        browser.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:23400')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--browser',default=None,help='Optional Chromium executable')
    parser.add_argument('--label',default='final')
    parser.add_argument('--multi-worker',action='store_true',help='Opt-in measured multi-worker comparison journey')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    if args.multi_worker:
        multi_worker_journey(args)
        return
    errors=[];checks=[];queries=[]
    def epoch(value):
        return int(value) if value.isdigit() else round(datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()*1000)
    with sync_playwright() as p:
        options={'headless':True}
        if args.browser:options.update(executable_path=args.browser,args=['--no-sandbox'])
        browser=p.chromium.launch(**options)
        page=browser.new_page(viewport={'width':1440,'height':1000}, locale='en-US', timezone_id='UTC')
        page.on('pageerror',lambda e:errors.append(str(e)))
        entry_diagnostics = EntryDiagnostics(page,args.output,args.label,errors)
        page.on('request',lambda r:queries.append(r.post_data_json) if '/api/ds/query' in r.url and r.method=='POST' else None)
        def capture(name):page.screenshot(path=str(args.output/f'{args.label}-{name}.png'),full_page=True)
        url=args.url+'/a/xlayer-telemetry-app?from=now-5m&var-cluster=scenes-demo&var-run_id=verl-agent-demo&var-node=gpu-node-0&var-gpu=0'
        enter_app(page,url,entry_diagnostics);page.wait_for_timeout(1500)
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
        page.get_by_role('button',name=re.compile('Analyze (Step|Trainer update|Observation)')).first.click()
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
        datasource_boundary_checks(page,url,checks,capture)
        # Optional dashboard availability is deliberately removed at Grafana's
        # metadata boundary. This tests absence handling, not a real Loki outage.
        page.route('**/api/dashboards/uid/xlayer-bottleneck-summary',lambda route:route.fulfill(status=404,json={'message':'Dashboard not found'}))
        page.route('**/api/dashboards/uid/xlayer-cross-layer-timeline',lambda route:route.fulfill(status=404,json={'message':'Dashboard not found'}))
        page.route('**/api/dashboards/uid/xlayer-run-logs',lambda route:route.fulfill(status=404,json={'message':'Dashboard not found'}))
        page.goto(url);page.wait_for_timeout(1800)
        assert 'Loki timeline unavailable' in page.locator('.xlt').inner_text();capture('optional-unavailable')
        checks.append('Optional dashboard 404 fixture degrades to metrics/Deep Dive; it is not reported as real backend outage validation')
        assert not errors,errors
        report={'checks':checks,'browser_errors':errors,'grafana_version':'12.1.0','scenes_version':'6.20.0','browser_version':browser.version,'playwright_version':version('playwright'),'browser_locale':'en-US','browser_timezone':'UTC','data_origin':'live synthetic metrics + SDK-generated step/span/diagnosis fixtures','native_requests_after_step_selection':step_query_count,'bounded_baseline_requests':len(baseline_requests),'native_requests_matching_step_window':len(matching),'selected_context':saved}
        (args.output/f'{args.label}-validation.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2));browser.close()

if __name__=='__main__':main()
