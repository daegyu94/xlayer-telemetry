"""Actual owned Grafana navigation with explicit browser-boundary HTTP faults."""
import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.sync_api import sync_playwright


def query(page):
    return parse_qs(urlparse(page.url).query)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser')
    args = parser.parse_args()
    if urlparse(args.url).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('Use an owned loopback Grafana demo')
    args.output.mkdir(parents=True, exist_ok=False)
    errors, checks = [], []
    context = {'var-cluster': 'scenes-demo', 'var-run_id': 'verl-agent-demo',
               'from': 'now-30m', 'to': 'now', 'timezone': 'UTC', 'theme': 'light'}
    base = args.url + '/a/xlayer-telemetry-app/'
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'],
            **({'executable_path': args.browser} if args.browser else {}))
        page = browser.new_page(viewport={'width': 1440, 'height': 1100}, locale='en-US', timezone_id='Asia/Seoul')
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            def ready():
                page.get_by_role('navigation', name='XLayer investigation').wait_for(timeout=30000)

            for uid, required in [('xlayer-run-logs', False), ('telemetry-overview', True)]:
                endpoint = '**/api/dashboards/uid/' + uid
                page.route(endpoint, lambda route: route.fulfill(status=503, json={}))
                page.goto(base + 'overview?' + urlencode(context))
                if required:
                    page.get_by_text('필수 Dashboard를 불러오지 못했습니다', exact=False).wait_for()
                else:
                    ready()
                    page.get_by_label('Dashboard availability').get_by_text('logs: HTTP 503', exact=True).wait_for()
                page.unroute(endpoint)
                page.get_by_role('button', name='Retry dashboards', exact=True).click()
                ready()
                page.get_by_label('Dashboard availability').wait_for(state='detached')
                assert all(query(page).get(key) == [value] for key, value in context.items())
                checks.append(('required' if required else 'optional') + ' dashboard HTTP 503 / retry retains context')

            for patch in [{'from': '99999999999999999999'}, {'from': 'not-a-date'}, {'from': '2000', 'to': '1000'}, {'timezone': 'Invalid/Zone'}]:
                page.goto(base + 'overview?' + urlencode({**context, **patch}))
                page.get_by_role('button', name='Use last 30 minutes', exact=True).wait_for()
                page.get_by_role('button', name='Use last 30 minutes', exact=True).click()
                ready()
                restored = query(page)
                assert restored['from'] == ['now-30m'] and restored['to'] == ['now']
                assert restored['var-run_id'] == [context['var-run_id']]
                assert restored['var-cluster'] == [context['var-cluster']]
            checks.append('invalid numeric/reversed/timezone URL recovery preserves Run and Cluster')

            # The browser uses Asia/Seoul, while the investigation explicitly uses UTC.
            absolute = {**context, 'from': '1791687600000', 'to': '1791687660000'}
            page.goto(base + 'overview?' + urlencode(absolute))
            ready()
            # The auxiliary range text is hidden by the compact header layout.
            page.locator('.xlt-visible-range').get_by_text('03:00:00 UTC', exact=False).wait_for(state='attached')
            page.get_by_text('2026-10-11 03:00:00 to 2026-10-11 03:01:00', exact=True).wait_for()
            checks.append('selected UTC presentation overrides Asia/Seoul browser timezone')

            page.goto(base + 'deep-dive?' + urlencode({**context, 'var-detail_tab': 'Connector RPC'}))
            ready()
            page.get_by_role('button', name='GPU', exact=True).click()
            page.wait_for_function("new URLSearchParams(location.search).get('var-detail_tab') === 'GPU'")
            page.reload()
            ready()
            assert page.get_by_role('button', name='GPU', exact=True).get_attribute('aria-pressed') == 'true'
            page.get_by_role('button', name='3FS evidence', exact=True).click()
            page.wait_for_function("new URLSearchParams(location.search).get('var-detail_tab') === '3FS evidence'")
            page.go_back()
            page.wait_for_function("document.querySelector('button[aria-pressed=\"true\"]')?.textContent === 'GPU'")
            page.go_forward()
            page.wait_for_function("document.querySelector('button[aria-pressed=\"true\"]')?.textContent === '3FS evidence'")
            assert query(page)['var-run_id'] == [context['var-run_id']]
            checks.append('Deep Dive tab URL / reload / Back / Forward')

            # ci_demo publishes these explicitly synthetic saved artifacts before this validator.
            page.goto(base + 'runs?' + urlencode({**context, 'var-run_id': '.*'}))
            ready()
            for name in ('compare-a', 'compare-b'):
                page.get_by_role('checkbox', name='Compare ' + name, exact=True).check()
            third = page.get_by_role('checkbox', name='Compare compare-other', exact=True)
            assert third.is_disabled()
            selected_b = query(page)['var-compare_run_b']
            page.get_by_role('button', name='Clear Run A', exact=True).click()
            assert 'var-compare_run_a' not in query(page)
            assert query(page)['var-compare_run_b'] == selected_b
            third.check()
            chosen = {key: value for key, value in query(page).items() if key.startswith('var-compare_run_')}
            page.get_by_label('Search Runs', exact=True).fill('does-not-exist')
            tray = page.get_by_label('Comparison selection', exact=True)
            assert 'compare-other' in tray.inner_text() and 'compare-b' in tray.inner_text()
            page.reload()
            ready()
            page.get_by_role('heading', name='Run Comparison', exact=True).wait_for()
            page.get_by_text('Comparability: Incomparable', exact=True).wait_for()
            assert all(query(page).get(key) == value for key, value in chosen.items())
            page.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: () => Promise.reject(new Error('Synthetic clipboard denial'))}})")
            page.get_by_role('button', name='Share', exact=True).click()
            page.get_by_role('alert').filter(has_text='링크를 복사하지 못했습니다').wait_for()
            share = page.locator('details[open]').filter(has=page.get_by_text('Share link', exact=True)).get_by_role('link')
            shared = parse_qs(urlparse(share.get_attribute('href')).query)
            assert all(shared.get(key) == value for key, value in chosen.items())
            checks.append('third comparison blocked / A-B slots / hidden selections / reload / clipboard fallback')
            # A reset replay must not replace the caller's theme/timezone with browser defaults.
            endpoint='**/api/plugin-proxy/xlayer-telemetry-app/demo-control/status'
            page.route(endpoint,lambda route:route.fulfill(status=200,json={'generation':2,'status':'ready'}))
            page.goto(base+'overview?'+urlencode({**context,'theme':'dark','var-demo_generation':'1'}))
            page.get_by_text('Replay expired',exact=True).wait_for(timeout=15000)
            page.get_by_role('link',name='Open Scenario Explorer →',exact=True).click()
            page.wait_for_url('**/demo?**')
            restored=query(page)
            assert restored['theme']==['dark'] and restored['timezone']==['UTC']
            assert 'var-demo_generation' not in restored and 'var-run_id' not in restored
            page.unroute(endpoint)
            checks.append('Expired replay recovery retains theme/timezone and clears old generation/run context')
            assert not errors, errors
            page.screenshot(path=str(args.output / 'comparison-recovery.png'), full_page=True)
        except Exception:
            page.screenshot(path=str(args.output / 'failure.png'), full_page=True)
            (args.output / 'failure.json').write_text(json.dumps({'url': page.url, 'browser_errors': errors,
                'body': page.locator('body').inner_text()[:24000]}, indent=2) + '\n')
            raise
        finally:
            browser.close()
    result = {'status': 'passed', 'checks': checks, 'browser_errors': errors,
              'scope': 'Actual single-host Grafana; synthetic artifacts and browser HTTP/clipboard faults. No GPU, physical multi-node, or backend outage validation.'}
    (args.output / 'validation.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
