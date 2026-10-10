"""Actual loopback Grafana/Prometheus/Loki multi-job journey; no GPU workload."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

from playwright.sync_api import sync_playwright
from browser_validate import EntryDiagnostics
from ci_demo import native_dashboard_back


def saved_run_journey(page, args, reports):
    # Consume the demo's actual published producer/query/diagnosis artifacts,
    # rather than constructing reader-shaped fixtures in the browser.
    path = args.state / 'dashboards/xlayer-run-catalog.json'
    catalog = json.loads(path.read_text())['xlayerRunCatalog']
    entries = {row['run_id']: row for row in catalog['runs']}
    assert set(entries) == set(reports)
    for run, report in reports.items():
        assert entries[run]['model'] == report['run_model']['identifier']
        assert entries[run]['data_origin'] == 'synthetic' and entries[run]['cluster'] == 'scenes-demo'
        assert entries[run]['execution_mode'] == 'sync'
    base = {'from':'now-10m','to':'now','var-cluster':'scenes-demo','var-run_id':'.*','theme':'dark'}
    page.goto(args.url.rstrip('/')+'/a/xlayer-telemetry-app/runs?'+urlencode(base))
    for run in entries:
        checkbox=page.get_by_role('checkbox',name='Compare '+run,exact=True)
        checkbox.wait_for(timeout=30000)
        row=page.get_by_role('row').filter(has=checkbox)
        assert entries[run]['model'] in row.inner_text()
        assert 'Synthetic' in row.inner_text() and 'sync' in row.inner_text()
    page.get_by_role('checkbox',name='Compare demo-qwen',exact=True).check()
    page.get_by_role('checkbox',name='Compare demo-llama',exact=True).check()
    page.get_by_text('Comparability: Incomparable',exact=True).wait_for(timeout=15000)
    page.get_by_label('Search Runs',exact=True).fill('demo')
    page.get_by_label('Filter Model',exact=True).select_option(entries['demo-qwen']['model'])
    page.get_by_label('Filter Run status',exact=True).select_option('unknown')
    page.get_by_label('Filter Run source',exact=True).select_option('stored_artifact')
    page.get_by_label('Limit saved Runs to selected time range',exact=True).check()
    def assert_state():
        page.get_by_role('checkbox',name='Compare demo-qwen',exact=True).wait_for(timeout=15000)
        assert page.get_by_label('Search Runs',exact=True).input_value()=='demo'
        assert page.get_by_label('Filter Model',exact=True).input_value()==entries['demo-qwen']['model']
        assert page.get_by_label('Filter Run status',exact=True).input_value()=='unknown'
        assert page.get_by_label('Filter Run source',exact=True).input_value()=='stored_artifact'
        assert page.get_by_label('Limit saved Runs to selected time range',exact=True).is_checked()
        page.get_by_role('checkbox',name='Compare demo-qwen',exact=True).wait_for(timeout=15000)
        assert page.get_by_role('checkbox',name='Compare demo-qwen',exact=True).is_checked()
        assert page.get_by_role('checkbox',name='Compare demo-llama',exact=True).count()==0
        page.get_by_text('Comparability: Incomparable',exact=True).wait_for(timeout=15000)
        params=parse_qs(urlparse(page.url).query)
        assert params['var-compare_run_a']==[entries['demo-qwen']['key']]
        assert params['var-compare_run_b']==[entries['demo-llama']['key']]
    assert_state()
    saved=page.url
    page.get_by_role('link',name='demo-qwen: Saved Step Evidence →',exact=True).click()
    page.get_by_role('navigation',name='XLayer investigation').wait_for(timeout=15000)
    assert parse_qs(urlparse(page.url).query)['var-run_id']==['demo-qwen']
    page.go_back(wait_until='domcontentloaded')
    assert_state()
    page.reload(wait_until='domcontentloaded')
    assert_state()
    page.get_by_text('Share link',exact=True).click()
    share=page.locator('details').filter(has=page.get_by_text('Share link',exact=True)).locator('a').get_attribute('href')
    page.goto(args.url.rstrip('/')+share)
    assert_state()
    page.screenshot(path=str(args.output/'saved-runs-filters-comparison.png'),full_page=True)
    return {'actual_producer_artifacts':True,'models':[row['model'] for row in entries.values()],
            'synthetic_retained':True,'filters_back_reload_share':True,'comparison_withheld_for_different_models':True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser')
    args = parser.parse_args()
    if urlparse(args.url).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('Use an owned loopback synthetic demo')
    args.output.mkdir(parents=True, exist_ok=False)
    connection = json.loads((args.state / 'connection.json').read_text())
    fixture = next(root for root in sorted(args.state.glob('fixture-*'), key=lambda p: int(p.name.split('-')[-1]), reverse=True)
                   if len(list(root.glob('*/diagnostics/latest.json'))) == 3)
    reports = {path.parent.parent.name: json.loads(path.read_text()) for path in fixture.glob('*/diagnostics/latest.json')}
    assert set(reports) == {'demo-qwen', 'demo-llama', 'demo-deepseek'}
    for run, report in reports.items():
        assert report['run_id'] == run and report['data_origin'] == 'synthetic'
        steps = [json.loads(line) for line in (fixture/run/'telemetry-events/verl-steps.jsonl').read_text().splitlines()]
        assert report['comparison']['baseline_record_id'] == steps[0]['record_id']
        assert report['trigger_record_id'] == steps[1]['record_id']
        assert report['query_execution']['sources']['prometheus']['attempted'] > 12
        assert 'threefs_p99_latency' not in {row['signal'] for row in report['comparison']['signals']}
    assert reports['demo-qwen']['candidates'] == [], reports['demo-qwen']['candidates']
    publications=[json.loads(line) for line in (fixture/'publications.jsonl').read_text().splitlines()]
    assert {row['run_id'] for row in publications} == set(reports)
    by_run={row['run_id']:row for row in publications}
    assert by_run['demo-qwen']['submitted_at'] < reports['demo-llama']['analysis_window']['end']
    assert all(row['analysis_status']=='completed' for row in publications)
    assert any(c['id'] == 'rollout_queue_backlog' for c in reports['demo-llama']['candidates'])
    assert any(c['id'].startswith(('actor_update_', 'checkpoint_')) for c in reports['demo-deepseek']['candidates'])
    reasons={row['signal']:row for row in reports['demo-llama']['comparison']['signals'] if row['signal'].startswith('vllm_waiting_')}
    assert set(reasons)=={'vllm_waiting_capacity_requests','vllm_waiting_deferred_requests'}
    assert reasons['vllm_waiting_capacity_requests']['current']==10
    assert reasons['vllm_waiting_deferred_requests']['current']==4
    assert all(row['labels']['instance']=='synthetic-job-llama-peer' for row in reasons.values())
    replica_rows = reports['demo-llama']['rollout_replicas']
    assert len(replica_rows) == 2
    assert replica_rows[0]['signals']['vllm_requests_waiting']['current'] == 0
    assert replica_rows[1]['signals']['vllm_requests_waiting']['current'] == 14
    assert replica_rows[1]['status'] == 'partial_evidence'
    assert 'gpu-node-2' in replica_rows[1]['nodes']
    assert any('vllm_preemptions_delta:missing' in issue for issue in replica_rows[1]['missing_sources'])
    primary=replica_rows[0]['serving_context']['current']; peer=replica_rows[1]['serving_context']['current']
    assert primary['router_registered'] is False and primary['serving_state']=='sleeping'
    assert primary['applied_policy_version'] is None
    assert peer['router_registered'] is True and peer['serving_state']=='serving'
    assert peer['applied_policy_version'] == reports['demo-llama']['comparison']['current_workload']['policy_version']-1
    assert primary['workload']['prompt_tokens'] != peer['workload']['prompt_tokens']
    assert replica_rows[1]['signals']['vllm_requests_waiting']['delta'] is None
    assert reports['demo-deepseek']['async_decision']['remaining']==20
    for report in reports.values():
        for candidate in report['candidates']:
            assert candidate['resource_attribution'] == 'not_established'
            assert 'run_resource_attribution_unverified' in candidate['missing_evidence']
    query = 'vllm:num_requests_waiting{cluster="scenes-demo"}'
    with urlopen(connection['prometheus'] + '/api/v1/query?' + urlencode({'query': query}), timeout=10) as response:
        native = json.load(response)['data']['result']
    assert len(native) == 4 and all('run_id' not in row['metric'] for row in native)
    def query_prom(expression):
        with urlopen(connection['prometheus'] + '/api/v1/query?' + urlencode({'query': expression}), timeout=10) as response:
            return json.load(response)['data']['result']
    assert not query_prom('reward_mean{run_id="demo-llama"}')
    assert not query_prom('vllm:num_preemptions_total{instance="synthetic-job-deepseek"}')
    assert not query_prom('up{telemetry_source="vllm",instance="synthetic-vllm-0"}')
    up=query_prom('up{telemetry_source="vllm",instance="synthetic-job-llama"}')
    assert len(up)==1 and float(up[0]['value'][1])==1
    decision=query_prom('training_async_samples_remaining{run_id="demo-deepseek"}')
    assert len(decision)==1 and float(decision[0]['value'][1])==20
    stale = query_prom('time() - training_sample_timestamp_seconds{run_id="demo-deepseek",role="trainer"}')
    assert stale and all(float(row['value'][1]) > 300 for row in stale)
    for run, report in reports.items():
        window=report['analysis_window']
        expression='{cluster="scenes-demo",run_id="'+run+'"} | json | log_file="agent.log"'
        params={'query':expression, 'start':str(int(window['start']*1e9)), 'end':str(int(window['end']*1e9)+1000000)}
        with urlopen(connection['loki']+'/loki/api/v1/query_range?'+urlencode(params), timeout=10) as response:
            logs=json.load(response)['data']['result']
        assert logs and all(json.loads(line)['run_id']==run for stream in logs for _,line in stream['values'])
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'], **({'executable_path': args.browser} if args.browser else {}))
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='en-US', timezone_id='UTC')
        errors=[]; page.on('pageerror', lambda e: errors.append(str(e)))
        base = {'from': 'now-10m', 'to': 'now', 'var-cluster': 'scenes-demo', 'var-run_id': 'demo-qwen',
                'var-node': 'gpu-node-0', 'var-source_node': 'gpu-node-0', 'var-worker': 'driver',
                'var-gpu': '0', 'var-engine': '$__all'}
        diagnostics = EntryDiagnostics(page, args.output, 'multi-job', errors)
        try:
            page.goto(args.url.rstrip('/') + '/a/xlayer-telemetry-app/overview?' + urlencode({**base, 'var-run_id': '$__all'}))
            page.get_by_role('heading', name='Run Overview', exact=True).wait_for(timeout=30000)
            page.get_by_text('Completed Steps · choose another investigation', exact=True).click()
            page.get_by_role('columnheader', name='Run', exact=True).wait_for(timeout=30000)
            all_runs = page.locator('table').filter(has=page.get_by_role('columnheader', name='Run', exact=True))
            for run in reports: assert run in all_runs.inner_text()
            page.screenshot(path=str(args.output/'all-runs.png'), full_page=True)
            page.goto(args.url.rstrip('/') + '/a/xlayer-telemetry-app/overview?' + urlencode(base))
            for run in ('demo-qwen', 'demo-llama', 'demo-deepseek'):
                if run != 'demo-qwen':
                    # A selected Step narrows time to that Step. Explicitly return
                    # to the common range before changing Jobs with different ends.
                    old=parse_qs(urlparse(page.url).query)['var-run_id'][0]
                    page.goto(args.url.rstrip('/') + '/a/xlayer-telemetry-app/overview?' + urlencode({**base, 'var-run_id': old}))
                    box=page.locator('.xlt-context-variable').nth(1)
                    box.get_by_role('combobox').fill(run)
                    page.get_by_role('option').filter(has_text=run).first.click()
                    page.keyboard.press('Escape'); page.keyboard.press('Tab')
                    for button in box.get_by_role('button', name='Remove', exact=True).all():
                        if button.locator('..').inner_text().strip() == old: button.click(); break
                    page.keyboard.press('Escape'); page.keyboard.press('Tab')
                    page.wait_for_function('(run)=>new URLSearchParams(location.search).getAll("var-run_id").join()===run', arg=run)
                step=page.get_by_role('combobox', name='Completed Step')
                step.locator('option[value="' + reports[run]['trigger_record_id'] + '"]').wait_for(state='attached', timeout=30000)
                step.select_option(reports[run]['trigger_record_id'])
                if run == 'demo-deepseek':
                    decision = page.locator('details').filter(has=page.get_by_text('Reported Async Trainer Decision', exact=True))
                    decision.locator('summary').click()
                    assert 'Next-update sample gap' in decision.inner_text() and '20' in decision.inner_text()
                    page.screenshot(path=str(args.output/'async-decision.png'),full_page=True)
                page.get_by_role('button', name='Analyze Step').click()
                if run == 'demo-qwen':
                    cell=page.locator('.xlt-cell:not(:disabled)').first
                    cell.wait_for(timeout=15000)
                    identity=cell.get_attribute('data-matrix-cell')
                    cell.click()
                    page.locator('.xlt-evidence').wait_for(timeout=5000)
                    page.keyboard.press('Escape')
                    page.locator('.xlt-evidence').wait_for(state='detached',timeout=5000)
                    page.wait_for_function('(identity)=>document.activeElement?.dataset.matrixCell===identity',arg=identity,timeout=5000)
                if run == 'demo-llama':
                    page.get_by_role('heading', name='Rollout Replica Coverage', exact=True).wait_for(timeout=30000)
                    replica_table = page.locator('section').filter(has=page.get_by_role('heading', name='Rollout Replica Coverage', exact=True))
                    replica_table.get_by_text('llama-1', exact=True).wait_for(timeout=30000)
                    assert 'partial evidence' in replica_table.inner_text()
                    assert 'gpu-node-1, gpu-node-2' in replica_table.inner_text()
                    assert 'Serving: sleeping' in replica_table.inner_text() and 'Not registered' in replica_table.inner_text()
                    assert 'Serving: serving' in replica_table.inner_text() and 'worker report' in replica_table.inner_text()
                    for width in (1440, 390):
                        page.set_viewport_size({'width': width, 'height': 1000})
                        replica_table.scroll_into_view_if_needed()
                        page.screenshot(path=str(args.output / f'rollout-replicas-{width}.png'), full_page=True)
                    page.set_viewport_size({'width': 1440, 'height': 1000})
                    replica_table.get_by_role('link', name=re.compile('^Inspect endpoint')).last.click()
                    page.wait_for_url('**/d/**', timeout=20000)
                    replica_context = parse_qs(urlparse(page.url).query)
                    assert replica_context['var-engine'] == ['synthetic-job-llama-peer']
                    assert replica_context['var-node'] == ['gpu-node-1']
                    assert replica_context['var-run_id'] == [run] and replica_context['var-record_id'] == [reports[run]['trigger_record_id']]
                    native_dashboard_back(page, args.url)
                page.get_by_role('link', name='Investigate', exact=True).click()
                page.get_by_role('heading', name=re.compile('Step Investigation')).wait_for(timeout=30000)
                page.wait_for_timeout(1200)
                body=page.locator('body').inner_text()
                assert 'Run metadata에 보고된 값' in body
                if run != 'demo-qwen':
                    assert '소유 관계는 검증되지 않았습니다' in body or '관계가 미확인' in body or '연결이 확인되지 않은 Shared signal' in body
                    page.locator('.xlt-candidates article').get_by_role('button', name='Open Evidence →', exact=True).first.click()
                    page.get_by_text('run_resource_attribution_unverified', exact=True).wait_for(timeout=10000)
                    page.locator('.xlt-candidates article').get_by_role('button', name='Deep Dive →', exact=True).first.click()
                    page.get_by_role('link', name='Full Storage', exact=False).first.wait_for(timeout=20000)
                    page.get_by_role('link', name='Full Storage', exact=False).first.click()
                    page.wait_for_url('**/d/**', timeout=20000)
                    current=parse_qs(urlparse(page.url).query)
                    assert current['var-run_id'] == [run] and current['var-record_id'] == [reports[run]['trigger_record_id']]
                    native_dashboard_back(page, args.url)
                    assert parse_qs(urlparse(page.url).query)['var-run_id'] == [run]
                for width in (1440, 390):
                    page.set_viewport_size({'width':width,'height':1000}); page.wait_for_timeout(300)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    page.screenshot(path=str(args.output/f'{run}-{width}.png'), full_page=True)
                page.set_viewport_size({'width':1440,'height':1000})
            saved_result=saved_run_journey(page,args,reports)
        except Exception as error:
            diagnostics.fail(error)
            raise
        finally:
            browser.close()
        assert not errors, errors
    result={'saved_run_pipeline':saved_result,'runs':{run:{'step':r['step'],'candidate_ids':[c['id'] for c in r['candidates']],
        'baseline':r['comparison']['baseline_record_id'],'prometheus_requests':r['query_execution']['sources']['prometheus']['attempted']} for run,r in reports.items()},
        'native_endpoints_without_run_labels':len(native),
        'rollout_replicas':[{key:row[key] for key in ('id','instance','nodes','status','clock_status')} for row in replica_rows],
        'browser_errors':errors, 'widths':[1440,390],
        'scope':'SDK + actual synthetic scrapes/query/diagnosis; no model execution, physical cluster or operation attribution'}
    (args.output/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__': main()
