"""Validate saved storage queries and visible native quality cells in Grafana.

Requires --storage-series. Optional response delay is browser fault injection;
3FS collection values are synthetic, not a physical ClickHouse/3FS validation.
"""
import argparse
import asyncio
import json
import math
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlencode
from playwright.async_api import async_playwright, expect


async def quality_cell(panel, column, text):
    """Activate an offscreen table, then reveal its virtualized quality column."""
    await panel.scroll_into_view_if_needed()
    grid = panel.get_by_role('grid')
    await expect(grid.get_by_role('gridcell').first).to_be_visible(timeout=15000)
    width, maximum = await grid.evaluate('(el)=>[el.clientWidth,el.scrollWidth-el.clientWidth]')
    step = max(1, width / 2)
    header = grid.get_by_role('columnheader').filter(has_text=re.compile(rf'^{re.escape(column)}$'))
    for index in range(math.ceil(maximum / step) + 1):
        await grid.evaluate('(el,left)=>{el.scrollLeft=left}', min(index * step, maximum))
        # Wait for the virtualized column at this viewport, not arbitrary sleeps.
        try:
            await expect(header).to_be_visible(timeout=500)
        except AssertionError:
            continue
        await header.scroll_into_view_if_needed()
        cell = grid.get_by_role('gridcell').filter(has_text=text).first
        await expect(cell).to_be_visible(timeout=5000)
        await cell.scroll_into_view_if_needed()
        await expect(cell).to_be_in_viewport(ratio=.9)
        return
    raise AssertionError(f'Native quality column {column} was never rendered')


def storage_rows(payload):
    rows = []
    for result in payload.get('results', {}).values():
        for frame in result.get('frames', []):
            for field, values in zip(frame.get('schema', {}).get('fields', []), frame.get('data', {}).get('values', [])):
                if field.get('name') != 'Line':
                    continue
                for value in values:
                    entry = json.loads(value)
                    row = entry.get('record', entry)
                    rows.append(json.loads(row) if isinstance(row, str) else row)
    return rows


async def run(args):
    out=args.output;out.mkdir(parents=True,exist_ok=False)
    url=args.url.rstrip('/')
    result={'status':'running','browser_errors':[],'responses':[], 'response_delay_ms':args.delay_storage_ms}
    responses={};ready={kind:asyncio.Event() for kind in ('storage_comparison','storage_series_status')}
    async with async_playwright() as p:
        browser=await p.chromium.launch(**({'executable_path':args.browser} if args.browser else {}),headless=True,args=['--no-sandbox'])
        page=await browser.new_page(viewport={'width':1440,'height':1000},locale='en-US',timezone_id='UTC')
        page.on('pageerror',lambda error:result['browser_errors'].append(str(error)))
        def kinds(request):
            if '/api/ds/query' not in request.url:return []
            queries=(request.post_data_json or {}).get('queries',[])
            return [kind for kind in ready if any(f'row_kind="{kind}"' in q.get('expr','') for q in queries)]
        async def capture(response):
            matched=kinds(response.request)
            if not matched:return
            try:
                payload=await response.json();rows=storage_rows(payload)
                result['responses'].append({'kinds':matched,'http_status':response.status,'rows':rows[:20],
                    'errors':[r.get('error') for r in payload.get('results',{}).values() if r.get('error')]})
                for kind in matched:
                    responses[kind]=[row for row in rows if row.get('row_kind')==kind]
                    ready[kind].set()
            except Exception as error:
                result['responses'].append({'kinds':matched,'capture_error':str(error)[:300]})
                for kind in matched:ready[kind].set()
        page.on('response',capture)
        if args.delay_storage_ms:
            async def delayed(route):
                if not kinds(route.request):
                    await route.continue_();return
                response=await route.fetch(timeout=15000)
                # Deliberate latency injection; readiness checks themselves never sleep.
                await asyncio.sleep(args.delay_storage_ms/1000)
                await route.fulfill(response=response)
            await page.route('**/api/ds/query*',delayed)
        try:
            await page.goto(url+'/a/xlayer-telemetry-app/overview?'+urlencode({'from':'now-5m','to':'now','var-cluster':'scenes-demo','var-run_id':'verl-agent-demo','var-node':'gpu-node-0','var-source_node':'gpu-node-0','var-gpu':'0','var-engine':'synthetic-vllm-0'}))
            await page.get_by_role('button',name=re.compile('Analyze Step')).wait_for(timeout=30000)
            selector=page.get_by_role('combobox',name='Completed Step')
            slow=selector.locator('option').filter(has_text='48.00').first
            await expect(slow).to_be_attached(timeout=15000)
            await selector.select_option(await slow.get_attribute('value'))
            await page.get_by_role('button',name=re.compile('Analyze Step')).click()
            await page.get_by_role('link',name='Investigation',exact=True).click()
            await page.locator('.xlt-candidates article').filter(has=page.get_by_role('heading',name='storage',exact=True)).get_by_role('button',name='Deep Dive →',exact=True).first.click()
            await page.get_by_role('button',name='3FS evidence',exact=True).click()
            metric=page.get_by_role('combobox',name='Storage metric')
            await metric.select_option('storage_client.overall_latency')
            section=page.get_by_role('region',name='3FS collection context')
            await expect(section).to_contain_text('Current collection point',timeout=15000)
            await expect(section).to_contain_text('Collection interval')
            await expect(section).to_contain_text('Phase 또는 Run별 사용량이 아닙니다')
            await expect(section).to_contain_text('Baseline')
            plot=section.locator('.xlt-panel').first
            await plot.scroll_into_view_if_needed()
            await expect(plot.locator('canvas').first).to_be_visible(timeout=15000)
            await expect(plot).not_to_contain_text(re.compile('No data|N/A'))
            saved=parse_qs(urlparse(page.url).query)
            for width in (1440,1280,900,390):
                await page.set_viewport_size({'width':width,'height':1000})
                await page.wait_for_function('document.documentElement.scrollWidth<=innerWidth+1')
                await page.screenshot(path=str(out/f'series-{width}.png'),full_page=True)
            await page.set_viewport_size({'width':1440,'height':1000})
            await metric.select_option('storage_client.data_payload_bytes')
            await expect(section).to_contain_text('Unit: bytes')
            await expect(plot).not_to_contain_text(re.compile('ms|N/A'))
            await expect(plot.locator('canvas').first).to_be_visible()
            await page.screenshot(path=str(out/'reset-amounts.png'),full_page=True)
            comparison=section.locator('details').filter(has=page.get_by_text('Comparable collection windows',exact=True))
            await comparison.locator('summary').click()
            await comparison.locator('.xlt-panel').scroll_into_view_if_needed()
            await asyncio.wait_for(ready['storage_comparison'].wait(),15)
            rows=[row for row in responses.get('storage_comparison',[]) if row.get('record_id') in saved['var-record_id'] and row.get('run_id') in saved['var-run_id']]
            assert rows, 'No comparison rows for the selected Run/record in the actual query response'
            assert any(row.get('comparison_status')=='clock_unverified' for row in rows), rows
            assert all(row.get('delta_percent') is None for row in rows if row.get('comparison_status')=='clock_unverified'), rows
            await quality_cell(comparison.locator('.xlt-panel'),'comparison_status','clock_unverified')
            await page.screenshot(path=str(out/'comparison.png'),full_page=True)
            coverage=section.locator('details').filter(has=page.get_by_text(re.compile('Saved source coverage')))
            await coverage.locator('summary').click()
            await coverage.locator('.xlt-panel').scroll_into_view_if_needed()
            await asyncio.wait_for(ready['storage_series_status'].wait(),15)
            assert any('collection_interval_unknown' in str(row.get('quality_issues','')) for row in responses.get('storage_series_status',[]))
            await quality_cell(coverage.locator('.xlt-panel'),'quality_issues','collection_interval_unknown')
            await page.screenshot(path=str(out/'coverage.png'),full_page=True)
            current=parse_qs(urlparse(page.url).query)
            for key in ('from','to','var-run_id','var-record_id','var-node','var-source_node'):
                assert current[key]==saved[key],key
            assert not result['browser_errors'],result['browser_errors']
            result.update(status='passed',plot_finite_saved_points=True,no_phase_attribution_notice=True,
                reset_report_amount_units='bytes, not B/s',clock_unverified_delta=True,
                widths=[1440,1280,900,390],context=current)
        except Exception as error:
            result.update(status='failed',error=str(error)[:2000],url=page.url,body=(await page.locator('body').inner_text())[:24000])
            await page.screenshot(path=str(out/'failure.png'),full_page=True)
            raise
        finally:
            (out/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
            await browser.close()
    print(json.dumps({key:value for key,value in result.items() if key!='responses'},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--browser')
    parser.add_argument('--delay-storage-ms',type=int,default=0,choices=[0,10000],help='Delay real storage query responses for a browser-boundary readiness regression')
    args=parser.parse_args()
    if urlparse(args.url).hostname not in ('127.0.0.1','localhost','::1'):
        parser.error('Use an owned loopback-only synthetic demo')
    asyncio.run(run(args))
