"""Validate native saved storage plots in an owned synthetic live demo.

Requires --storage-series; physical 3FS/workload attribution is not tested.
"""
import argparse,json,re
from pathlib import Path
from urllib.parse import parse_qs,urlparse,urlencode
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url',required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--browser',default=None)
args=parser.parse_args()
if urlparse(args.url).hostname not in ('127.0.0.1','localhost','::1'):
 parser.error('Use an owned loopback-only synthetic demo')
out=args.output;out.mkdir(parents=True,exist_ok=False)
url=args.url.rstrip('/')
with sync_playwright() as p:
 browser=p.chromium.launch(**({'executable_path':args.browser} if args.browser else {}),headless=True,args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':1440,'height':1000},locale='en-US',timezone_id='UTC')
 errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto(url+'/a/xlayer-telemetry-app/overview?'+urlencode({'from':'now-5m','to':'now','var-cluster':'scenes-demo','var-run_id':'verl-agent-demo','var-node':'gpu-node-0','var-source_node':'gpu-node-0','var-gpu':'0','var-engine':'synthetic-vllm-0'}))
 page.get_by_role('button',name=re.compile('Analyze Step')).wait_for(timeout=30000)
 selector=page.get_by_role('combobox',name='Completed Step')
 slow=next((o.get_attribute('value') for o in selector.locator('option').all() if '48.00' in o.inner_text()),None)
 assert slow;selector.select_option(slow);page.wait_for_timeout(700)
 page.get_by_role('button',name=re.compile('Analyze Step')).click()
 page.get_by_role('link',name='Investigate',exact=True).click()
 page.locator('.xlt-candidates article').filter(has=page.get_by_role('heading',name='storage',exact=True)).get_by_role('button',name='Deep Dive →',exact=True).first.click()
 page.get_by_role('button',name='3FS evidence',exact=True).click()
 page.get_by_role('combobox',name='Storage metric').wait_for(timeout=15000)
 page.wait_for_timeout(800)
 page.get_by_role('combobox',name='Storage metric').select_option('storage_client.overall_latency')
 try:page.get_by_text(re.compile('Current collection points')).wait_for(timeout=8000)
 except Exception:
  page.screenshot(path=str(out/'selection-failure.png'),full_page=True);(out/'failure-body.txt').write_text(page.locator('body').inner_text());raise
 page.wait_for_timeout(900)
 section=page.get_by_role('region',name='3FS collection context')
 assert 'Collection interval' in section.inner_text()
 assert 'phase or Run usage' in section.inner_text()
 assert 'Baseline' in section.inner_text()
 plot=section.locator('.xlt-panel').first
 assert 'No data' not in plot.inner_text() and 'N/A' not in plot.inner_text(),plot.inner_text()
 saved=parse_qs(urlparse(page.url).query)
 for width in (1440,1280,900,390):
  page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(300)
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
  page.screenshot(path=str(out/f'series-{width}.png'),full_page=True)
 page.set_viewport_size({'width':1440,'height':1000})
 page.get_by_role('combobox',name='Storage metric').select_option('storage_client.data_payload_bytes')
 page.get_by_text(re.compile('Current collection points')).wait_for();page.wait_for_timeout(700)
 assert 'bytes' in section.inner_text()
 assert 'ms' not in section.locator('.xlt-panel').first.inner_text(), 'Previous latency frame was reused for a byte amount'
 assert 'N/A' not in section.locator('.xlt-panel').first.inner_text()
 assert section.locator('.xlt-panel').first.locator('canvas').count()>0
 page.screenshot(path=str(out/'reset-amounts.png'),full_page=True)
 page.get_by_text('Comparable collection windows',exact=True).click();page.wait_for_timeout(600)
 (out/'comparison-body.txt').write_text(section.inner_text());page.screenshot(path=str(out/'comparison.png'),full_page=True)
 assert 'clock_unverified' in section.inner_text()
 page.get_by_text(re.compile('Saved source coverage')).click();page.wait_for_timeout(600)
 assert 'collection_interval_unknown' in section.inner_text()
 page.screenshot(path=str(out/'coverage.png'),full_page=True)
 current=parse_qs(urlparse(page.url).query)
 for key in ('from','to','var-run_id','var-record_id','var-node','var-source_node'):
  assert current[key]==saved[key],key
 assert not errors,errors
 result={'browser_errors':errors,'plot_finite_saved_points':True,'no_phase_attribution_notice':True,'reset_report_amount_units':'bytes, not B/s','clock_unverified_delta':True,'widths':[1440,1280,900,390],'context':current}
 (out/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
 browser.close()
print(json.dumps(result,indent=2))
