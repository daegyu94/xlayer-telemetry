"""Candidate-less cached Scene → candidate selection → evidence → history."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs
from design_validate import owned_origin


def run(args):
    from playwright.sync_api import sync_playwright, expect
    args.output.mkdir(parents=True, exist_ok=False)
    base = owned_origin(args.url) + '/a/xlayer-telemetry-app/'
    report = {'scope': 'Actual Grafana + synthetic SDK/query/diagnosis', 'checks': [], 'failures': []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox'], **({'executable_path': args.browser} if args.browser else {}))
        for theme in ('light', 'dark'):
            page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='en-US')
            try:
                params = {'var-cluster': 'scenes-demo', 'var-run_id': 'verl-agent-demo', 'var-node': 'gpu-node-0',
                    'var-source_node': 'gpu-node-0', 'from': 'now-30m', 'to': 'now', 'theme': theme, 'timezone': 'UTC'}
                page.goto(base+'deep-dive?'+urlencode(params))
                expect(page.get_by_role('heading', name='Key Findings', exact=True)).to_be_visible(timeout=30000)
                assert not parse_qs(urlparse(page.url).query).get('var-candidate_id')
                # Stay inside the SPA: a page.goto/reload here would hide the cache defect.
                page.get_by_role('navigation', name='XLayer investigation').get_by_role('link', name='Run Overview', exact=True).click()
                page.locator('.xlt-slow-steps li button').first.wait_for(timeout=30000)
                page.locator('.xlt-slow-steps li button').first.click()
                card = page.locator('.xlt-candidates .xlt-card').first
                card.wait_for(timeout=20000)
                card.get_by_role('button', name='Deep Dive →', exact=True).click()
                page.wait_for_url('**/deep-dive?**')
                selected = parse_qs(urlparse(page.url).query)
                assert selected.get('var-candidate_id') and selected.get('var-record_id')
                findings = page.locator('.xlt-workspace-grid > div').first
                expect(findings.get_by_role('heading', name='Against / Missing', exact=True)).to_be_visible(timeout=20000)
                expect(findings.locator('p > b').first).to_be_visible()
                proof = findings.locator('p > b').first.inner_text()
                page.go_back()
                page.wait_for_url('**/investigate?**')
                card.wait_for()
                page.go_forward()
                page.wait_for_url('**/deep-dive?**')
                expect(findings.locator('p > b').first).to_have_text(proof, timeout=15000)
                for key in ('var-candidate_id', 'var-record_id', 'var-run_id', 'var-source_node', 'timezone', 'theme'):
                    assert parse_qs(urlparse(page.url).query).get(key) == selected.get(key), key
                page.set_viewport_size({'width':390, 'height':844})
                expect(findings.locator('p > b').first).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                report['checks'].append(theme+': first empty entry, cached evidence, Back/Forward identity, 390px')
            except Exception as error:
                report['failures'].append({'theme': theme, 'error': str(error)[:2000], 'url': page.url})
                (args.output / (theme+'.html')).write_text(page.content())
            finally:
                page.screenshot(path=str(args.output / (theme+'.png')), full_page=True)
                page.close()
        browser.close()
    (args.output / 'report.json').write_text(json.dumps(report, indent=2))
    assert not report['failures'], report['failures']


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser')
    run(parser.parse_args())
