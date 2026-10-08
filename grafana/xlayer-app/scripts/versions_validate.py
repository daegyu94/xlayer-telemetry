#!/usr/bin/env python3
"""Classic/Workspace parity on an owned Grafana; no services or workloads launched."""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.sync_api import sync_playwright
from ci_demo import datasource_error_result

PAGES = ('overview', 'analyze', 'investigate', 'deep-dive', 'infrastructure', 'logs')


def context(url):
    return {key: values for key, values in parse_qs(urlparse(url).query, keep_blank_values=True).items()
            if key.startswith('var-') or key in ('from', 'to', 'timezone')}


def signature(query):
    # UI versions may have different request IDs; compare actual native targets.
    return json.dumps({key: query[key] for key in ('expr', 'query', 'queryType', 'instant', 'range', 'format', 'maxLines', 'datasource')
                       if key in query}, sort_keys=True)


def validate(args):
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'fixture': 'synthetic live telemetry; real Grafana/Prometheus/Loki, not physical infrastructure', 'pages': {}, 'boundary': {}}
    base = args.url.rstrip('/') + '/a/xlayer-telemetry-app/'
    errors, queries = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**({'executable_path': args.browser} if args.browser else {}), headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='en-US', timezone_id='UTC')
        page.on('pageerror', lambda error: errors.append(str(error)[:1000]))
        def request(request):
            if '/api/ds/query' in request.url and len(queries) < 500:
                queries.extend((request.post_data_json or {}).get('queries', []))
        page.on('request', request)
        initial = urlencode({'var-cluster': 'scenes-demo', 'var-run_id': 'verl-agent-demo', 'var-node': 'gpu-node-0',
                             'var-source_node': 'gpu-node-0', 'var-gpu': '0', 'var-engine': 'synthetic-vllm-0', 'from': 'now-10m', 'to': 'now'})
        page.goto(base+'overview?'+initial)
        selector = page.get_by_role('combobox', name='Completed Step')
        page.wait_for_function("Array.from(document.querySelectorAll('[aria-label=\"Completed Step\"] option')).some(o=>o.innerText.includes('48.00'))", timeout=30000)
        record = next(option.get_attribute('value') for option in selector.locator('option').all() if '48.00' in option.inner_text())
        selector.select_option(record)
        page.wait_for_timeout(700)
        selected_context = context(page.url)
        selected_query = urlparse(page.url).query
        for version in ('v1', 'v2'):
            prefix = '' if version == 'v1' else 'v2/'
            for name in PAGES:
                queries.clear()
                suffix = '&var-candidate_id=device_limited_storage' if name == 'deep-dive' else ''
                page.goto(base+prefix+name+'?'+selected_query+suffix)
                page.locator('.xlt-header h2').wait_for(timeout=30000)
                page.wait_for_timeout(1400)
                if name == 'infrastructure':
                    page.get_by_role('button', name='Inspect resource roce-fabric (compute)', exact=True).click()
                    page.get_by_text('Resource metrics withheld:', exact=False).wait_for(timeout=15000)
                    assert page.locator('.xlt-infrastructure-metrics .xlt-panel').count() == 0
                    page.get_by_role('button', name='Inspect resource storage-node-0 (storage)', exact=True).click()
                    page.wait_for_function("document.querySelector('.xlt-resource-detail')?.innerText.includes('storage-node-0')", timeout=15000)
                    selected = context(page.url)
                    assert selected['var-storage_node'] == ['storage-node-0'], selected
                    for key in ('var-record_id', 'var-source_node', 'var-run_id', 'from', 'to'):
                        assert selected[key] == selected_context[key], (key, selected)
                    assert page.get_by_role('button', name='storage', exact=True).get_attribute('aria-pressed') == 'true'
                    page.wait_for_timeout(1000)
                    # Return to the same context to compare initial query contracts.
                elif name == 'logs':
                    assert page.get_by_role('heading', name='Raw Logs', exact=True).count() == 1
                    page.get_by_role('button', name='Inspect event', exact=True).first.click()
                    assert 'record_type' in page.locator('.xlt-event-detail').inner_text()
                    colors = page.locator('.xlt-event-detail pre').evaluate("el=>[getComputedStyle(el).color,getComputedStyle(el).backgroundColor]")
                    def luminance(color):
                        channels = [float(value)/255 for value in re.findall(r'\d+', color)[:3]]
                        linear = [value/12.92 if value <= .04045 else ((value+.055)/1.055)**2.4 for value in channels]
                        return sum(value*weight for value, weight in zip(linear, (.2126, .7152, .0722)))
                    fg, bg = sorted(map(luminance, colors))
                    assert (bg+.05)/(fg+.05) >= 4.5, (version, colors)
                label = version+'-'+name
                values = {'title': page.locator('.xlt-header h2').inner_text(), 'kpis': page.locator('.xlt-kpi').all_text_contents(),
                          'matrix': page.locator('.xlt-matrix').all_text_contents(), 'candidates': page.locator('.xlt-candidates').all_text_contents(),
                          'query_contracts': sorted(set(signature(query) for query in queries)), 'query_count': len(queries)}
                report['pages'][label] = values
                assert not page.locator('.xlt-error').count(), label
                for width, height in ((1440, 1000), (390, 844)):
                    page.set_viewport_size({'width': width, 'height': height})
                    page.wait_for_timeout(200)
                    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1'), (label, width)
                    page.evaluate('window.scrollTo(0,0)')
                    page.screenshot(path=str(args.output/(label+('-mobile' if width==390 else '')+'.png')), full_page=True)
                    if width == 1440:
                        page.screenshot(path=str(args.output/(label+'-viewport.jpg')), type='jpeg', quality=82)
                page.set_viewport_size({'width': 1440, 'height': 1000})
                print(label+' verified', flush=True)
        (args.output/'validation.json').write_text(json.dumps(report, indent=2)+'\n')
        for name in PAGES:
            first, second = report['pages']['v1-'+name], report['pages']['v2-'+name]
            for key in ('title', 'kpis', 'matrix', 'candidates', 'query_contracts'):
                assert first[key] == second[key], (name, key)
        # Native dashboard remains the detail surface; Back must retain V2.
        page.goto(base+'v2/infrastructure?'+selected_query)
        page.get_by_role('button', name='Inspect resource storage-node-0 (storage)', exact=True).click()
        page.wait_for_function("document.querySelector('.xlt-resource-detail')?.innerText.includes('storage-node-0')", timeout=15000)
        resource_context = context(page.url)
        page.get_by_role('link', name='Detailed resource dashboard', exact=False).click()
        page.wait_for_url('**/d/**', timeout=15000)
        native_context = context(page.url)
        for key in ('var-cluster', 'var-run_id', 'var-record_id', 'var-node', 'var-source_node', 'from', 'to'):
            assert native_context[key] == resource_context[key], (key, native_context)
        page.go_back()
        page.locator('.xlt-frame[data-ui=workspace]').wait_for(timeout=15000)
        for key, value in resource_context.items():
            assert context(page.url)[key] == value, (key, context(page.url))
        report['native_dashboard_back'] = 'Storage dashboard and browser Back retain V2 / Step / resource / observer / time'
        # Real version switch preserves selected URL state and restores Light locally.
        page.goto(base+'v2/analyze?'+selected_query)
        page.get_by_role('link', name='V1 Classic', exact=True).click()
        page.locator('.xlt-frame[data-ui=classic]').wait_for()
        switched = context(page.url)
        for key, value in selected_context.items():
            assert switched[key] == value, (key, switched)
        assert page.locator('.xlt-workspace-nav').count() == 0
        report['version_switch'] = 'Run / record / worker / resource / observer / time preserved; local Light restored'
        # Literal log filters retain Grafana's datasource/Explore; no regex guessing.
        log_context = {**selected_context, 'var-log_search': ['policy.updated'], 'var-log_severity': ['']}
        page.goto(base+'v2/logs?'+urlencode(log_context, doseq=True))
        page.locator('.xlt-header h2').wait_for()
        page.wait_for_timeout(1000)
        assert any('policy.updated' in str(query.get('expr','')) and '|=' in str(query.get('expr','')) for query in queries)
        report['literal_log_filter'] = True
        def missing_catalog(route):
            route.fulfill(status=404, json={'message': 'Optional dashboard not provisioned'})
        optional = ('xlayer-run-logs', 'xlayer-cross-layer-timeline', 'xlayer-bottleneck-summary')
        for uid in optional:
            page.route('**/api/dashboards/uid/'+uid, missing_catalog)
        for version in ('v1', 'v2'):
            prefix = '' if version == 'v1' else 'v2/'
            page.goto(base+prefix+'logs?'+initial)
            page.locator('.xlt-header h2').wait_for()
            assert 'Loki / Run Logs not provisioned' in page.locator('.xlt-logs-grid').inner_text()
            report['boundary'][version+'-missing-source'] = 'optional Loki/catalog absent; App remains usable, no measured zero'
        for uid in optional:
            page.unroute('**/api/dashboards/uid/'+uid, missing_catalog)
        def failed(route):
            route.fulfill(status=200, json=datasource_error_result((route.request.post_data_json or {}).get('queries', [])))
        def empty(route):
            route.fulfill(status=200, json={'results': {str(q.get('refId','A')): {'status': 200, 'frames': []}
                for q in (route.request.post_data_json or {}).get('queries', [])}})
        for fixture, handler in (('query-failure', failed), ('empty', empty)):
            page.route('**/api/ds/query*', handler)
            for version in ('v1', 'v2'):
                prefix = '' if version == 'v1' else 'v2/'
                page.goto(base+prefix+'infrastructure?'+initial)
                page.locator('.xlt-header h2').wait_for()
                page.wait_for_timeout(1000)
                assert 'No configured inventory' in page.locator('.xlt-topology').inner_text()
                if fixture == 'query-failure':
                    assert page.locator('.xlt-error').count() > 0
                page.screenshot(path=str(args.output/(version+'-'+fixture+'.png')), full_page=True)
                report['boundary'][version+'-'+fixture] = 'missing inventory/relationship, no health or zero inferred'
            page.unroute('**/api/ds/query*', handler)
        def stale_age(route):
            native_queries = (route.request.post_data_json or {}).get('queries', [])
            age_refs = [str(q.get('refId','A')) for q in native_queries
                        if str(q.get('expr','')).lstrip().startswith('time() - training_sample_timestamp_seconds')]
            if not age_refs:
                route.continue_()
                return
            response = route.fetch()
            payload = response.json()
            for ref in age_refs:
                for frame in payload.get('results', {}).get(ref, {}).get('frames', []):
                    for index, field in enumerate(frame.get('schema', {}).get('fields', [])):
                        if field.get('type') == 'number':
                            frame['data']['values'][index] = [90000 for value in frame['data']['values'][index]]
            route.fulfill(response=response, json=payload)
        page.route('**/api/ds/query*', stale_age)
        for version in ('v1', 'v2'):
            prefix = '' if version == 'v1' else 'v2/'
            page.goto(base+prefix+'overview?'+initial)
            page.locator('.xlt-header h2').wait_for()
            page.wait_for_timeout(1000)
            reward = page.locator('.xlt-kpi').first.inner_text()
            assert re.search('Stale|Freshness|Missing|No data', reward, re.I), reward
            report['boundary'][version+'-stale'] = reward
        page.unroute('**/api/ds/query*', stale_age)
        assert not errors, errors
        report['browser_errors'] = errors
        report['parity'] = '6/6: identical primary values, candidates, matrix and native query target sets'
        browser.close()
    (args.output/'validation.json').write_text(json.dumps(report, indent=2)+'\n')
    print(report['parity'], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser')
    args = parser.parse_args()
    if urlparse(args.url).hostname not in ('127.0.0.1', 'localhost'):
        parser.error('Use an owned loopback-only Grafana demo')
    validate(args)


if __name__ == '__main__':
    main()
