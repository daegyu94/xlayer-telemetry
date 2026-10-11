"""Decision-first product journey against the owned, real Grafana demo.

No metrics are fabricated here: fixture data comes from the live SDK/demo.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlencode
from design_validate import owned_origin


def run(args):
    from playwright.sync_api import sync_playwright, expect
    origin = owned_origin(args.url)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'checks': [], 'captures': [], 'browser_errors': [], 'data_origin': 'synthetic SDK + native Grafana queries'}
    params = {'var-cluster': 'scenes-demo', 'var-run_id': 'verl-agent-demo', 'var-node': 'gpu-node-0',
              'var-source_node': 'gpu-node-0', 'var-gpu': '0', 'var-engine': 'synthetic-vllm-0',
              'from': 'now-30m', 'to': 'now', 'timezone': 'UTC', 'theme': 'light'}
    url = origin + '/a/xlayer-telemetry-app/overview?' + urlencode(params)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox'], **({'executable_path': args.browser} if args.browser else {}))
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, timezone_id='Asia/Seoul')
        page.on('pageerror', lambda error: report['browser_errors'].append(str(error)))
        def capture(name):
            page.screenshot(path=str(args.output / (name+'.png')), full_page=True)
            report['captures'].append(name+'.png')
        def context():
            return parse_qs(urlparse(page.url).query)
        def ready():
            page.locator('.xlt-primary-kpis .xlt-progress strong').filter(has_text='Step').wait_for(timeout=30000)
            page.locator('.xlt-slow-steps li button').first.wait_for(timeout=15000)
        page.goto(url); ready()
        assert page.locator('nav[aria-label="XLayer investigation"] a').count() == 4
        assert page.locator('.xlt-primary-kpis .xlt-kpi').count() == 4
        assert page.locator('.xlt-secondary-signals').get_attribute('open') is None
        assert page.locator('.xlt-execution-details').get_attribute('open') is None
        report['checks'].append('Four primary destinations and four KPIs; learning/resource and execution details collapsed by default')
        for width in (1440, 1024, 390):
            page.set_viewport_size({'width': width, 'height': 1000 if width>390 else 844})
            for theme in ('light', 'dark'):
                page.goto(url.replace('theme=light', 'theme='+theme)); ready()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'), (width, theme)
                assert page.locator('.xlt').get_attribute('data-theme') == theme
                if width==390:
                    expect(page.get_by_role('navigation',name='XLayer investigation',include_hidden=True)).not_to_be_visible()
                    page.get_by_role('button',name='Open navigation',exact=True).click()
                    expect(page.get_by_role('navigation',name='XLayer investigation')).to_be_visible()
                    page.get_by_role('button',name='Close navigation',exact=True).click()
                if width==1440:
                    box=page.locator('.xlt-primary-action').bounding_box()
                    assert box and box['y']+box['height']<=1000, 'Primary investigation action falls below first viewport'
                capture(f'run-{theme}-{width}')
        report['checks'].append('Light/Dark × 1440/1024/390px, no document overflow, desktop action above fold')
        page.set_viewport_size({'width':1440,'height':1000});page.goto(url);ready()
        page.locator('.xlt-slow-steps li button').first.click()
        page.wait_for_url('**/investigate?**')
        page.locator('.xlt-candidate-section').wait_for(timeout=20000)
        selected = context()
        for key in ('var-cluster','var-run_id','var-record_id','var-source_node','from','to','timezone'):
            assert selected.get(key), key
        page.locator('.xlt-candidates .xlt-card').first.wait_for(timeout=15000)
        capture('investigation')
        page.locator('.xlt-candidates .xlt-card').first.get_by_role('button',name='Open Evidence →',exact=True).click()
        page.get_by_role('complementary',name='Evidence detail').wait_for(timeout=15000)
        capture('evidence')
        page.keyboard.press('Escape')
        page.locator('.xlt-candidates .xlt-card').first.get_by_role('button',name='Deep Dive →',exact=True).click()
        page.wait_for_url('**/deep-dive?**')
        page.get_by_role('heading',name='Key Findings',exact=True).wait_for(timeout=15000)
        for key in ('var-cluster','var-run_id','var-record_id','var-source_node','from','to','timezone'):
            # Grafana may normalize epoch milliseconds to ISO on scene activation.
            if key not in ('from','to'): assert context().get(key)==selected.get(key), key
        page.get_by_role('button',name='GPU',exact=True).click()
        page.wait_for_function("new URLSearchParams(location.search).get('var-detail_tab')==='GPU'")
        shared=page.url;page.reload()
        page.get_by_role('button',name='GPU',exact=True).wait_for(timeout=30000)
        expect(page.get_by_role('button',name='GPU',exact=True)).to_have_attribute('aria-pressed','true')
        page.go_back();page.wait_for_function("new URLSearchParams(location.search).get('var-detail_tab')!=='GPU'")
        page.go_forward();page.wait_for_function("new URLSearchParams(location.search).get('var-detail_tab')==='GPU'")
        report['checks'].append('Slow observation → candidate → evidence → metric; shared URL reload and tab Back/Forward retain selected identity')
        report['shared_url']=shared
        page.goto(url)
        ready();page.locator('.xlt-execution-details > summary').click()
        page.locator('.xlt-execution-details canvas').first.wait_for(timeout=20000)
        page.locator('.xlt-execution-details > summary').click()
        page.locator('.xlt-execution-details > summary').click()
        page.locator('.xlt-execution-details canvas').first.wait_for(timeout=20000)
        report['checks'].append('Lazy native timeline activates and survives fold/unfold')
        page.goto(url.replace('verl-agent-demo','missing-greenfield-run'))
        page.get_by_text('No diagnosis recorded',exact=True).wait_for(timeout=20000)
        expect(page.locator('.xlt-progress strong')).to_have_text('Not reported')
        expect(page.locator('.xlt-primary-kpis .xlt-kpi').last.locator('strong')).to_have_text('Not observed')
        capture('missing-run')
        report['checks'].append('Unknown run: no invented progress, health or zero errors')
        assert not report['browser_errors'], report['browser_errors']
        browser.close()
    (args.output/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--browser')
    run(parser.parse_args())
