#!/usr/bin/env python3
"""Exercise previously blank/loading panels and presentation controls in owned Grafana."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode, parse_qs, urlparse
from playwright.sync_api import sync_playwright, expect
from design_validate import owned_origin

def validate(args):
    origin=owned_origin(args.url)
    args.output.mkdir(parents=True,exist_ok=False)
    checks=[];errors=[]
    params={'from':'now-5m','to':'now','var-cluster':'scenes-demo','var-run_id':'verl-agent-demo',
            'var-source_node':'gpu-node-0','var-node':'gpu-node-0','var-gpu':'0','var-engine':'synthetic-vllm-0'}
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'],**({'executable_path':args.browser} if args.browser else {}))
        try:
            for theme in ('light','dark'):
                for width in (1440,1024,390):
                    page=browser.new_page(viewport={'width':width,'height':1000},locale='en-US',timezone_id='UTC',color_scheme=theme)
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    for route,ids in [('start-here',[7]),('stage-correlation',[3,28,40]),('storage',[2,33]),('compute',[35,52,56])]:
                        page.goto(origin+'/a/xlayer-telemetry-app/'+route+'?'+urlencode({**params,'theme':theme}))
                        page.get_by_role('navigation',name='XLayer investigation',include_hidden=True).wait_for(state='attached',timeout=30000)
                        page.locator('.xlt-content').wait_for(timeout=30000)
                        for group in page.locator('details.xlt-native-group').all():
                            group.evaluate('(element)=>{element.open=true}')
                            group.locator('.xlt-native-grid').wait_for(state='attached',timeout=10000)
                        for number in ids:
                            panel=page.locator(f'[data-canonical-panel="{number}"]')
                            panel.wait_for(timeout=20000);panel.scroll_into_view_if_needed()
                            if route=='start-here':
                                expect(panel.get_by_text('Down / Stale / N/A:',exact=False)).to_be_visible()
                                assert panel.locator('a').count()>=4
                                last=panel.locator('a').last
                                assert last.bounding_box()['y']+last.bounding_box()['height']<=panel.bounding_box()['y']+panel.bounding_box()['height']+1,'Text link clipped'
                            elif route=='stage-correlation' and number==28:
                                panel.locator('[data-testid^="data-testid VizLegend series "]').filter(has_text='local').wait_for(timeout=30000)
                                assert 'external_' in panel.inner_text()
                            elif route=='storage' and number==2:
                                panel.locator('.xlt-resource-cell[data-state=observed]').first.wait_for(timeout=30000)
                                panel.get_by_label('Matrix metric').select_option('B')
                                panel.locator('.xlt-resource-cell[data-state=observed]').first.wait_for(timeout=30000)
                            elif (route=='stage-correlation' and number==3) or (route=='compute' and number==52):
                                expect(panel).to_contain_text('Query evaluated at',timeout=30000)
                                assert panel.get_attribute('data-panel-type')=='table'
                                expect(panel.get_by_text('node',exact=True)).to_be_visible()
                                expect(panel.get_by_text('worker_id' if number==3 else 'gpu',exact=True)).to_be_visible()
                            else:
                                assert panel.get_attribute('data-panel-type')=='state-timeline'
                                panel.locator('canvas').first.wait_for(timeout=30000)
                            # Native chrome / canvas must fit its panel, not just the viewport.
                            bounds=panel.evaluate('''e=>{const p=e.getBoundingClientRect();return [...e.querySelectorAll('[data-viz-panel-key],canvas')].map(n=>{const b=n.getBoundingClientRect();return {height:b.height,width:b.width,inside:b.bottom<=p.bottom+1&&b.right<=p.right+1}})}''')
                            assert all(b['inside'] and b['height']>0 and b['width']>0 for b in bounds),(route,number,bounds)
                            panel.screenshot(path=str(args.output/f'{route}-{number}-{theme}-{width}.png'))
                            checks.append({'route':route,'panel':number,'theme':theme,'width':width,'panel_bounds':True})
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    # The terminal prefix-cache result survives a mounted page reload.
                    page.goto(origin+'/a/xlayer-telemetry-app/stage-correlation?'+urlencode({**params,'theme':theme}))
                    page.get_by_role('navigation',name='XLayer investigation',include_hidden=True).wait_for(state='attached',timeout=30000)
                    page.locator('.xlt-content').wait_for(timeout=30000)
                    for group in page.locator('details.xlt-native-group').all():
                        group.evaluate('(e)=>{e.open=true}')
                        group.locator('.xlt-native-grid').wait_for(state='attached',timeout=10000)
                    page.locator('[data-canonical-panel="28"]').scroll_into_view_if_needed()
                    page.locator('[data-canonical-panel="28"] canvas').first.wait_for(timeout=30000)
                    page.reload()
                    page.get_by_role('navigation',name='XLayer investigation',include_hidden=True).wait_for(state='attached',timeout=30000)
                    page.locator('.xlt-content').wait_for(timeout=30000)
                    for group in page.locator('details.xlt-native-group').all():
                        group.evaluate('(e)=>{e.open=true}')
                        group.locator('.xlt-native-grid').wait_for(state='attached',timeout=10000)
                    page.locator('[data-canonical-panel="28"]').scroll_into_view_if_needed()
                    page.locator('[data-canonical-panel="28"] canvas').first.wait_for(timeout=30000)
                    page.goto(origin+'/a/xlayer-telemetry-app/overview?'+urlencode({**params,'theme':theme}))
                    selector=page.get_by_role('combobox',name='Completed Step')
                    selector.locator('option[data-record-id]').first.wait_for(state='attached',timeout=30000)
                    options=selector.locator('option[data-record-id]').all()
                    assert options
                    selected=next((o for o in options if '48.00' in o.inner_text()),options[0])
                    assert selected.inner_text().startswith(('Step','Trainer update','Observation'))
                    selector.select_option(selected.get_attribute('value'))
                    expect(page.locator('.xlt-visible-range')).to_be_visible()
                    before=parse_qs(urlparse(page.url).query)
                    page.get_by_role('button',name='Compact table density').click()
                    assert page.locator('.xlt').get_attribute('data-density')=='compact'
                    assert parse_qs(urlparse(page.url).query)==before
                    page.get_by_role('button',name='Compact table density').click()
                    page.get_by_role('button',name='Analyze Step',exact=False).first.click()
                    focus=page.get_by_role('combobox',name='Phase focus');focus.wait_for(timeout=30000)
                    choices=focus.locator('option').all()
                    assert len(choices)>2
                    before=parse_qs(urlparse(page.url).query)
                    focus.select_option(choices[-1].get_attribute('value'))
                    expect(page.locator('.xlt-matrix thead th')).to_have_count(2)
                    assert parse_qs(urlparse(page.url).query)==before
                    focus.select_option('all')
                    assert page.locator('.xlt-matrix thead th').count()>2
                    checks.append({'route':'analyze','theme':theme,'width':width,'phase_focus':True,'density':True,'context_preserved':True,'reload':True})
                    page.close()
            # Public datasource boundary: one query is missing while its sibling remains real.
            page=browser.new_page(viewport={'width':1440,'height':1000},locale='en-US',timezone_id='UTC')
            page.on('pageerror',lambda error:errors.append(str(error)))
            intercepted=[]
            def missing_local(route):
                response=route.fetch()
                body=response.json()
                for query in (route.request.post_data_json or {}).get('queries',[]):
                    expr=query.get('expr','')
                    if 'vllm:prefix_cache_hits_total' in expr and 'external' not in expr:
                        ref=query['refId']
                        if ref in body.get('results',{}):
                            body['results'][ref]['frames']=[]
                            intercepted.append(ref)
                route.fulfill(response=response,json=body)
            page.route('**/api/ds/query*',missing_local)
            page.goto(origin+'/a/xlayer-telemetry-app/stage-correlation?'+urlencode(params))
            card=page.locator('.xlt-native-highlights .xlt-card').filter(has_text='vLLM prefix cache token hit ratio')
            expect(card).to_contain_text('2 signals',timeout=30000)
            expect(card).to_contain_text('No data')
            expect(card.locator('.xlt-highlight-signals')).to_contain_text('%')
            assert intercepted,'Missing-source boundary fixture was not exercised'
            checks.append({'route':'stage-correlation','partial_query_missing':True,'sibling_value_preserved':True})
            page.close()
        finally:browser.close()
    assert not errors,errors
    result={'checks':checks,'browser_errors':errors,'scope':'Owned synthetic Grafana, DOM bounds and manually inspected panel captures; no physical GPU/cluster or WCAG certification.'}
    (args.output/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'checks':len(checks),'browser_errors':errors}),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True);parser.add_argument('--output',required=True,type=Path);parser.add_argument('--browser')
    validate(parser.parse_args())
