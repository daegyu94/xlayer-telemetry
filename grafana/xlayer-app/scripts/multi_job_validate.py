"""Actual loopback Grafana/Prometheus/Loki multi-job journey; no GPU workload."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

from playwright.sync_api import sync_playwright
from browser_validate import EntryDiagnostics


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
    assert all(row['labels']['instance']=='synthetic-job-llama' for row in reasons.values())
    for report in reports.values():
        for candidate in report['candidates']:
            assert candidate['resource_attribution'] == 'not_established'
            assert 'run_resource_attribution_unverified' in candidate['missing_evidence']
    query = 'vllm:num_requests_waiting{cluster="scenes-demo"}'
    with urlopen(connection['prometheus'] + '/api/v1/query?' + urlencode({'query': query}), timeout=10) as response:
        native = json.load(response)['data']['result']
    assert len(native) == 3 and all('run_id' not in row['metric'] for row in native)
    def query_prom(expression):
        with urlopen(connection['prometheus'] + '/api/v1/query?' + urlencode({'query': expression}), timeout=10) as response:
            return json.load(response)['data']['result']
    assert not query_prom('reward_mean{run_id="demo-llama"}')
    assert not query_prom('vllm:num_preemptions_total{instance="synthetic-job-deepseek"}')
    assert not query_prom('up{telemetry_source="vllm",instance="synthetic-vllm-0"}')
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
                page.get_by_role('button', name='Analyze Step').click()
                page.get_by_role('link', name='Investigate', exact=True).click()
                page.get_by_role('heading', name=re.compile('Step Investigation')).wait_for(timeout=30000)
                page.wait_for_timeout(1200)
                body=page.locator('body').inner_text()
                assert 'reported Run metadata' in body
                if run != 'demo-qwen':
                    assert 'Run relation' in body or 'Not linked to selected Run' in body
                    page.locator('.xlt-candidates article').get_by_role('button', name='Open Evidence →', exact=True).first.click()
                    page.get_by_text('run_resource_attribution_unverified', exact=True).wait_for(timeout=10000)
                    page.locator('.xlt-candidates article').get_by_role('button', name='Deep Dive →', exact=True).first.click()
                    page.get_by_role('link', name='Full Storage', exact=False).first.wait_for(timeout=20000)
                    page.get_by_role('link', name='Full Storage', exact=False).first.click()
                    page.wait_for_url('**/d/**', timeout=20000)
                    current=parse_qs(urlparse(page.url).query)
                    assert current['var-run_id'] == [run] and current['var-record_id'] == [reports[run]['trigger_record_id']]
                    page.go_back(); page.wait_for_url('**/a/xlayer-telemetry-app/**')
                    assert parse_qs(urlparse(page.url).query)['var-run_id'] == [run]
                for width in (1440, 390):
                    page.set_viewport_size({'width':width,'height':1000}); page.wait_for_timeout(300)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    page.screenshot(path=str(args.output/f'{run}-{width}.png'), full_page=True)
                page.set_viewport_size({'width':1440,'height':1000})
        except Exception as error:
            diagnostics.fail(error)
            raise
        finally:
            browser.close()
        assert not errors, errors
    result={'runs':{run:{'step':r['step'],'candidate_ids':[c['id'] for c in r['candidates']],
        'baseline':r['comparison']['baseline_record_id'],'prometheus_requests':r['query_execution']['sources']['prometheus']['attempted']} for run,r in reports.items()},
        'native_endpoints_without_run_labels':3, 'browser_errors':errors, 'widths':[1440,390],
        'scope':'SDK + actual synthetic scrapes/query/diagnosis; no model execution, physical cluster or operation attribution'}
    (args.output/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__': main()
