"""Native Grafana clock-quality boundary fixture; not physical NTP validation."""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse, urlencode

from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url',required=True)
parser.add_argument('--output',required=True,type=Path)
parser.add_argument('--browser')
args=parser.parse_args()
if urlparse(args.url).hostname not in ('127.0.0.1','localhost','::1'):
    parser.error('Use an owned loopback-only synthetic demo')
args.output.mkdir(parents=True,exist_ok=False)
with sync_playwright() as p:
    browser=p.chromium.launch(**({'executable_path':args.browser} if args.browser else {}),
                             headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1000},locale='en-US',timezone_id='UTC')
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    query=urlencode({'from':'now-5m','to':'now','var-cluster':'scenes-demo','var-run_id':'verl-agent-demo',
                     'var-source_node':'gpu-node-0','var-node':'gpu-node-0','var-gpu':'0','var-engine':'synthetic-vllm-0'})
    page.goto(args.url.rstrip('/')+'/a/xlayer-telemetry-app/overview?'+query)
    page.get_by_role('button',name=re.compile('Analyze Step')).wait_for(timeout=30000)
    selector=page.get_by_role('combobox',name='Completed Step')
    slow=next((o.get_attribute('value') for o in selector.locator('option').all() if '48.00' in o.inner_text()),None)
    assert slow
    selector.select_option(slow)
    page.get_by_role('button',name=re.compile('Analyze Step')).click()
    page.get_by_role('table').filter(has_text='Subsystem').first.wait_for(timeout=15000)
    page.get_by_label('Correlation clock quality').get_by_text('aligned',exact=True).wait_for(timeout=15000)
    saved=parse_qs(urlparse(page.url).query)
    matrix=page.locator('.xlt-matrix')
    page.wait_for_timeout(1000)
    assert 'Clock unverified' not in matrix.inner_text(),matrix.inner_text()
    page.screenshot(path=str(args.output/'clock-pass.png'),full_page=True)

    def unverified_summary(route):
        response=route.fetch()
        try:body=response.json()
        except ValueError:
            route.fulfill(response=response);return
        for result in body.get('results',{}).values():
            for frame in result.get('frames',[]):
                fields=frame.get('schema',{}).get('fields',[])
                values=frame.get('data',{}).get('values',[])
                for field,column in zip(fields,values):
                    if field.get('name')!='Line':continue
                    for index,text in enumerate(column):
                        try:record=json.loads(text)
                        except (ValueError,TypeError):continue
                        row=record.get('record',record)
                        if isinstance(row,dict) and row.get('row_kind')=='summary':
                            row['correlation_clock_status']='unsafe'
                            row['baseline_clock_status']='unknown'
                            column[index]=json.dumps(record)
        route.fulfill(response=response,json=body)

    page.route('**/api/ds/query*',unverified_summary)
    page.reload()
    page.get_by_label('Correlation clock quality').get_by_text('unsafe',exact=True).wait_for(timeout=15000)
    page.get_by_text('Clock unverified',exact=True).first.wait_for(timeout=15000)
    assert 'vs baseline' not in page.locator('.xlt-matrix').inner_text()
    assert page.locator('.xlt-pressure-value strong').count()>0, 'Raw system pressure was erased by the clock gate'
    for width in (1440,390):
        page.set_viewport_size({'width':width,'height':1000})
        page.wait_for_timeout(300)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.screenshot(path=str(args.output/f'clock-withheld-{width}.png'),full_page=True)
    current=parse_qs(urlparse(page.url).query)
    for key in ('from','to','var-run_id','var-record_id','var-source_node'):
        assert current[key]==saved[key],key
    page.unroute('**/api/ds/query*',unverified_summary)
    page.reload()
    page.get_by_label('Correlation clock quality').get_by_text('aligned',exact=True).wait_for(timeout=15000)
    assert not errors,errors
    result={'browser_errors':errors,'clock_pass':True,'unsafe_precision_withheld':True,
            'raw_queries_retained':True,'recovery':True,'widths':[1440,390],
            'scope':'stored summary changed at browser datasource boundary; not OS synchronization or backend NTP failure'}
    (args.output/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    browser.close()
print(json.dumps(result,indent=2))
