#!/usr/bin/env python3
"""Capture all Workspace pages and assert the supplied design's geometry on Grafana."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode,urlparse
from playwright.sync_api import sync_playwright

PAGES=('overview','analyze','investigate','deep-dive','infrastructure','logs')


def validate(args):
    args.output.mkdir(parents=True,exist_ok=True)
    report={'fixture':'Actual Grafana on live synthetic telemetry; not physical infrastructure','reference_size':[1672,941],'pages':{},'errors':[]}
    with sync_playwright() as p:
        browser=p.chromium.launch(**({'executable_path':args.browser} if args.browser else {}),args=['--no-sandbox'],headless=True)
        page=browser.new_page(viewport={'width':1672,'height':941},locale='en-US',timezone_id='UTC')
        page.on('pageerror',lambda error:report['errors'].append(str(error)[:1000]))
        if args.reference:
            page.goto(args.reference.resolve().as_uri())
            images=page.locator('img').count()
            assert images>=1,images
            for index,label in enumerate(('Overview','Analyze','Investigate','Deep Dive','Infrastructure','Logs & Events'),1):
                button=page.get_by_role('button').nth(index-1)
                assert label in button.inner_text(),button.inner_text()
                button.click()
                page.wait_for_function('document.querySelector("img").complete && document.querySelector("img").naturalWidth===1672')
                page.screenshot(path=str(args.output/f'reference-{PAGES[index-1]}-browser.png'),full_page=False)
            report['reference_html']={'browser_rendered':True,'image_count':images}
        base=args.url.rstrip('/')+'/a/xlayer-telemetry-app/'
        page.goto(base+'overview?'+urlencode({'var-cluster':'scenes-demo','var-run_id':'verl-agent-demo','var-node':'gpu-node-0','var-source_node':'gpu-node-0','var-gpu':'0','var-engine':'synthetic-vllm-0','from':'now-10m','to':'now'}))
        page.wait_for_function("Array.from(document.querySelectorAll('[aria-label=\"Completed Step\"] option')).some(o=>o.innerText.includes('48.00'))",timeout=30000)
        choices=page.get_by_role('combobox',name='Completed Step')
        choices.select_option(next(option.get_attribute('value') for option in choices.locator('option').all() if '48.00' in option.inner_text()))
        page.wait_for_timeout(500)
        query=urlparse(page.url).query
        for name in PAGES:
            page.goto(base+'v2/'+name+'?'+query+('&var-candidate_id=device_limited_storage' if name=='deep-dive' else ''))
            page.locator('.xlt-header h2').wait_for(timeout=30000)
            page.wait_for_timeout(1200)
            if name in ('overview','infrastructure'):
                page.get_by_role('button',name='Inspect resource gpu-node-0 (compute)',exact=True).click()
                page.wait_for_timeout(600)
            if name=='logs':
                page.get_by_role('button',name='Inspect event',exact=True).first.click()
            frame=page.locator('.xlt-frame').bounding_box()
            rail=page.locator('.xlt-workspace-nav').bounding_box()
            context=page.locator('.xlt-context-top').bounding_box()
            assert frame and abs(frame['x'])<=1 and abs(frame['width']-1672)<=1,frame
            assert rail and abs(rail['width']-235)<=1,rail
            assert context and abs(context['height']-81)<=1,context
            assert page.locator('.xlt-header .xlt-badge').filter(has_text='Synthetic demo').is_visible()
            assert not page.locator('.xlt-error').count(),name
            if name=='overview':
                # A native panel header selector must never hide the whole panel.
                plots=page.locator('.xlt-overview-metric-strip [data-testid="data-testid panel content"]')
                assert plots.count()==4
                for plot in plots.all():
                    box=plot.bounding_box();assert box and box['height']>20,box
                tiles=page.locator('.xlt-overview-clusters').bounding_box();strip=page.locator('.xlt-overview-metric-strip').bounding_box()
                assert tiles and strip and tiles['y']+tiles['height']<=strip['y']+1,(tiles,strip)
            geometries=[]
            for width,height in ((1672,941),(1280,900),(390,844)):
                page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(250)
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1'),(name,width)
                page.evaluate('scrollTo(0,0)')
                page.screenshot(path=str(args.output/f'{name}-{width}.png'),full_page=False)
                if width==1672:
                    page.screenshot(path=str(args.output/f'{name}.jpg'),type='jpeg',quality=85)
                    page.screenshot(path=str(args.output/f'{name}-full.png'),full_page=True)
                geometries.append({'width':width,'no_document_overflow':True})
            report['pages'][name]={'frame':frame,'sidebar':rail,'context':context,'viewports':geometries}
            page.set_viewport_size({'width':1672,'height':941})
            print(name+' geometry / rendering verified',flush=True)
        assert not report['errors'],report['errors']
        browser.close()
    (args.output/'validation.json').write_text(json.dumps(report,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reference',type=Path,help='Optional supplied local mockup HTML; never required in CI')
    parser.add_argument('--browser')
    args=parser.parse_args()
    if urlparse(args.url).hostname not in ('localhost','127.0.0.1'):
        parser.error('Use an owned loopback-only Grafana')
    validate(args)


if __name__=='__main__':main()
