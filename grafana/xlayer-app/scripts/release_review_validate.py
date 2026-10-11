"""Release boundary regressions in actual Grafana, with explicit HTTP fixtures.

These test UI fault handling, not backend failures or hardware behavior.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from design_validate import owned_origin


def run(args):
    from playwright.sync_api import sync_playwright, expect, Error as PlaywrightError
    origin = owned_origin(args.url)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'scope': 'Actual Grafana; browser HTTP/clock fixtures only', 'checks': [], 'failures': []}
    endpoint = '**/api/plugin-proxy/xlayer-telemetry-app/demo-control/status'
    params = {'var-cluster': 'scenes-demo', 'var-run_id': 'verl-agent-demo', 'theme': 'light', 'timezone': 'UTC'}
    base = origin + '/a/xlayer-telemetry-app/'
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox'], **({'executable_path': args.browser} if args.browser else {}))
        def check(name, test):
            page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='en-US')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            try:
                test(page)
                assert not errors, errors
                report['checks'].append(name)
            except Exception as error:
                report['failures'].append({'check': name, 'error': (str(error) or type(error).__name__)[:1800], 'browser_errors': errors})
            finally:
                page.screenshot(path=str(args.output / (name + '.png')), full_page=True)
                page.close()

        # Use the real scenario definitions; override only control responses.
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from xlayer_telemetry.demos.control import ScenarioControl
        catalog = ScenarioControl().snapshot()
        catalog.update(selected='slow-rollout', generation=7, status='ready', validation={
            stage: {'state': 'passed', 'checks': ['browser fixture'], 'message': 'Slow rollout validation'}
            for stage in ('generation', 'collection', 'query', 'diagnosis', 'ui')})

        def selected_validation(page):
            page.route(endpoint, lambda route: route.fulfill(json=catalog))
            page.goto(base + 'demo?var-demo_scenario=slow-rollout')
            expect(page.locator('[data-stage=query]')).to_have_attribute('data-state', 'passed')
            page.locator('.xlt-scenario-list a[href*="demo_scenario=normal"]').click()
            expect(page.locator('[data-stage=query]')).to_have_attribute('data-state', 'pending')
            expect(page.locator('.xlt-scenario-controls [role=status]')).to_contain_text('Not started')
            expect(page.get_by_role('button', name='Stop scenario', exact=True)).to_be_disabled()
        check('scenario-validation-ownership', selected_validation)

        def boundary_recovery(page):
            calls = []
            def respond(route):
                calls.append(1)
                route.fulfill(status=503, json={}) if len(calls)==1 else route.fulfill(json=catalog)
            page.route(endpoint, respond)
            target = base + 'overview?' + urlencode({**params, 'var-demo_generation': '7'})
            page.goto(target)
            page.get_by_text('Replay verification unavailable', exact=True).wait_for(timeout=15000)
            page.locator('.xlt-primary-kpis').wait_for(timeout=12000)
            assert 'var-demo_generation=7' in page.url and 'var-run_id=verl-agent-demo' in page.url
        check('replay-verification-recovery', boundary_recovery)

        def poll_lifetime(page):
            calls = []
            def respond(route):
                calls.append(1)
                route.fulfill(json={**catalog, 'generation': 7+len(calls)})
            page.clock.install()
            page.route(endpoint, respond)
            page.goto(base + 'demo')
            status = page.locator('.xlt-scenario-controls [role=status]')
            expect(status).to_have_text('ready · generation 8', timeout=30000)
            for index in range(122):
                with page.expect_response(lambda response: 'demo-control/status' in response.url, timeout=2500):
                    page.clock.fast_forward(2600)
                expect(status).to_have_text(f'ready · generation {index+9}')
            assert len(calls)>121
        check('scenario-poll-after-five-minutes', poll_lifetime)

        def explorer_recovery(page):
            failing = [False]
            legacy = []
            page.route(endpoint, lambda route: route.fulfill(status=503, json={}) if failing[0] else route.fulfill(json=catalog))
            page.route('**/api/dashboards/uid/xlayer-demo-scenarios', lambda route: (legacy.append(1), route.fulfill(json={'dashboard': {'xlayerDemoScenarios': catalog}})))
            page.goto(base+'demo')
            expect(page.locator('[data-stage=query]')).to_have_attribute('data-state','passed')
            failing[0] = True
            page.get_by_role('button', name='Refresh scenarios', exact=True).click()
            expect(page.locator('.xlt-scenario-controls [role=status]')).to_contain_text('Unverified')
            expect(page.get_by_role('button', name='Replay scenario', exact=True)).to_be_disabled()
            assert not legacy, 'A known live controller must not fall back to a saved catalog'
            failing[0] = False
            expect(page.locator('[data-stage=query]')).to_have_attribute('data-state','passed', timeout=12000)
            expect(page.get_by_role('button', name='Replay scenario', exact=True)).to_be_enabled()
        check('explorer-outage-recovery', explorer_recovery)

        def native_provenance(page):
            page.route(endpoint, lambda route: route.fulfill(json=catalog))
            page.goto(base+'compute?'+urlencode({**params, 'var-demo_generation':'7'}))
            page.locator('.xlt-native-heading').wait_for(timeout=30000)
            expect(page.get_by_role('link', name='Synthetic demo', exact=True)).to_be_visible()
            page.set_viewport_size({'width':390, 'height':844})
            expect(page.get_by_role('link', name='Synthetic demo', exact=True)).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        check('native-metric-synthetic-provenance', native_provenance)

        def mobile_selection(page):
            page.set_viewport_size({'width':390,'height':844})
            page.route(endpoint, lambda route: route.fulfill(json=catalog))
            page.goto(base+'demo?theme=dark&timezone=UTC')
            picker=page.get_by_role('combobox', name='Choose scenario', exact=True)
            expect(picker).to_have_value('slow-rollout', timeout=30000)
            replay=page.get_by_role('button', name='Replay scenario', exact=True)
            box=replay.bounding_box()
            assert box and box['y']+box['height']<844, 'Selected scenario action must be in the first mobile viewport'
            picker.select_option('normal')
            expect(page.locator('[data-stage=query]')).to_have_attribute('data-state','pending')
            page.go_back()
            expect(picker).to_have_value('slow-rollout')
            page.go_forward()
            expect(picker).to_have_value('normal')
            page.reload()
            expect(picker).to_have_value('normal',timeout=30000)
            assert 'theme=dark' in page.url and 'timezone=UTC' in page.url
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        check('mobile-scenario-selection-context', mobile_selection)

        def mobile_keyboard(page):
            page.set_viewport_size({'width':390,'height':844})
            page.route(endpoint, lambda route: route.fulfill(json=catalog))
            page.goto(base+'demo')
            toggle=page.get_by_role('button',name='Open navigation',exact=True)
            toggle.focus();page.keyboard.press('Enter')
            close=page.get_by_role('button',name='Close navigation',exact=True)
            expect(close).to_be_focused()
            page.keyboard.press('Shift+Tab')
            expect(page.locator('.xlt-sidebar .xlt-demo-link')).to_be_focused()
            page.keyboard.press('Tab')
            expect(close).to_be_focused()
            page.keyboard.press('Escape')
            expect(toggle).to_be_focused()
            expect(page.get_by_role('navigation',name='XLayer investigation',include_hidden=True)).not_to_be_visible()
        check('mobile-navigation-keyboard', mobile_keyboard)

        def loading_state(page):
            held = []
            holding = True
            def hold(route):
                if holding:held.append(route)
                else:route.continue_()
            page.route('**/api/ds/query*', hold)
            # Loading can render before Scenes dispatches its first query. Expose
            # only fixture readiness; do not read browser state inside a route.
            page.expose_function('xlayerHeldQueryCount', lambda: len(held))
            try:
                page.goto(base + 'overview?' + urlencode(params))
                page.wait_for_function('async () => (await window.xlayerHeldQueryCount()) > 0', timeout=15000)
                card = page.locator('.xlt-kpi').filter(has=page.get_by_role('button', name='Worker throughput 설명', exact=True))
                expect(card.locator('strong')).to_have_text('Loading…', timeout=15000)
                assert held, 'A native datasource request must be held before checking Loading'
                expect(card).not_to_contain_text('일치하는 observation이 없습니다')
                page.screenshot(path=str(args.output/'kpi-loading-held.png'),full_page=True)
            finally:
                report['held_native_query_count']=len(held)
                holding = False
                for route in held:
                    try:
                        queries=(route.request.post_data_json or {}).get('queries',[])
                        route.fulfill(json={'results':{q['refId']:{'status':200,'frames':[]} for q in queries}})
                    except PlaywrightError as error:
                        if 'Route is already handled!' not in str(error):
                            raise
                page.unroute('**/api/ds/query*', hold)
        check('kpi-loading-is-not-no-data', loading_state)
        browser.close()
    report['status'] = 'failed' if report['failures'] else 'passed'
    (args.output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if report['failures']:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser')
    run(parser.parse_args())
