"""Native Legend controls inside the unchanged Scenes resource screens."""
import argparse,json
from pathlib import Path
from urllib.parse import urlencode,parse_qs,urlparse
from playwright.sync_api import sync_playwright

from design_validate import owned_origin


def validate(args):
    origin=owned_origin(args.url)
    args.output.mkdir(parents=True,exist_ok=False)
    errors=[];checks=[]
    cases=(('compute','GPU utilization','nodename','gpu-node-0'),
           ('storage','Disk read and write throughput','device','nvme0n1'),
           ('compute','TCP/Ethernet throughput by interface','nodename','gpu-node-0'))
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'],**({'executable_path':args.browser} if args.browser else {}))
        try:
            for theme in ('light','dark'):
                for width in (1440,390):
                    page=browser.new_page(viewport={'width':width,'height':1000},color_scheme=theme,locale='en-US',timezone_id='UTC')
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    for route,title,label,value in cases:
                        params={'theme':theme,'from':'now-5m','to':'now','var-cluster':'scenes-demo',
                                'var-run_id':'verl-agent-demo','var-node':'$__all','var-gpu':'$__all','var-device':'$__all'}
                        page.goto(origin+'/a/xlayer-telemetry-app/'+route+'?'+urlencode(params))
                        page.locator('.xlt').wait_for(timeout=30000)
                        navigation=page.locator('.xlt').get_by_role('link',name='Overview',exact=True,include_hidden=True)
                        navigation.wait_for(state='attached',timeout=30000)
                        assert navigation.count()==1
                        panel=page.get_by_test_id('data-testid Panel header '+title)
                        control=panel.get_by_role('button',name='Series visibility',exact=True)
                        control.wait_for(timeout=30000);control.scroll_into_view_if_needed()
                        assert panel.locator('canvas').count()>0
                        saved=parse_qs(urlparse(page.url).query)
                        control.click()
                        page.get_by_text('By labels',exact=True).wait_for()
                        page.get_by_text(label,exact=True).click()
                        page.get_by_text(value,exact=True).last.click()
                        assert any(page.get_by_role('checkbox').evaluate_all('(els)=>els.map(e=>e.checked)'))
                        page.get_by_text('Clear all',exact=True).click()
                        assert not any(page.get_by_role('checkbox').evaluate_all('(els)=>els.map(e=>e.checked)'))
                        page.get_by_text('Select all',exact=True).last.click()
                        page.get_by_text('Deselect all',exact=True).wait_for()
                        page.get_by_text('Deselect all',exact=True).click()
                        page.keyboard.press('Escape')
                        current=parse_qs(urlparse(page.url).query)
                        for key in ('from','to','var-cluster','var-run_id','var-node','var-gpu','var-device'):
                            assert current.get(key)==saved.get(key),(key,current,saved)
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                        panel.screenshot(path=str(args.output/f'{route}-{title.lower().replace(" ", "-").replace("/", "-")}-{theme}-{width}.png'))
                        checks.append({'route':route,'panel':title,'theme':theme,'width':width,
                                       'label_selection':True,'select_deselect_clear':True,'context_preserved':True})
                    page.close()
        finally:browser.close()
    assert not errors,errors
    result={'checks':checks,'browser_errors':errors,'scope':'Scenes UI + native legend; synthetic data; no operation attribution'}
    (args.output/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--browser',default=None)
    validate(parser.parse_args())
