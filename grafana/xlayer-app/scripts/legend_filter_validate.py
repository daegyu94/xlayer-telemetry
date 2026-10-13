"""Resource Matrix, selected series and KPI layout inside existing Scenes."""
import argparse,json
from pathlib import Path
from urllib.parse import urlencode,parse_qs,urlparse
from playwright.sync_api import sync_playwright, expect

from design_validate import owned_origin
from readability_validate import effective_colors, contrast_ratio


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
                        navigation=page.locator('.xlt').get_by_role('link',name='Run Overview',exact=True,include_hidden=True)
                        navigation.wait_for(state='attached',timeout=30000)
                        assert navigation.count()==1
                        matrix=page.locator('.xlt-resource-panel').filter(has=page.get_by_role('heading',name=title,exact=True))
                        matrix.wait_for(timeout=30000)
                        cell=matrix.locator('.xlt-resource-cell[data-state=observed]').first
                        cell.wait_for(timeout=30000)
                        assert matrix.locator('table').count()==1
                        styles=matrix.locator('.xlt-resource-cell[data-state=observed] b').evaluate_all("""els=>els.map(e=>{const color=getComputedStyle(e).color,layers=[];for(let n=e;n;n=n.parentElement){const s=getComputedStyle(n);layers.push({color:s.backgroundColor,opacity:Number(s.opacity)})}return {color,layers}})""")
                        ratios=[contrast_ratio(*effective_colors(s['color'],s['layers'])) for s in styles]
                        assert min(ratios)>=4.5,min(ratios)
                        before_selection=parse_qs(urlparse(page.url).query)
                        cell.click()
                        matrix.locator('.xlt-resource-selection canvas').first.wait_for(timeout=30000)
                        assert matrix.locator('.xlt-resource-selection').get_by_role('button',name='Clear selection').count()==1
                        selected_plot=matrix.locator('.xlt-resource-selection')
                        expect(selected_plot.locator('[data-testid^="data-testid VizLegend series "]')).to_have_count(1,timeout=10000)
                        assert 'No data' not in selected_plot.inner_text()
                        assert parse_qs(urlparse(page.url).query)==before_selection
                        matrix.get_by_role('button',name='Clear selection').click()
                        assert matrix.locator('.xlt-resource-selection').count()==0
                        matrix.get_by_label('Matrix calculation').select_option('mean')
                        matrix.get_by_label('Matrix calculation').select_option('max')
                        matrix.get_by_label('Matrix calculation').select_option('latest')
                        matrix.screenshot(path=str(args.output/f'matrix-{route}-{title.lower().replace(" ", "-").replace("/", "-")}-{theme}-{width}.png'))
                        matrix.get_by_role('button',name='Time series',exact=True).click()
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
                                       'minimum_cell_contrast':round(min(ratios),2),'matrix_selection':True,'reductions':True,'label_selection':True,'select_deselect_clear':True,'context_preserved':True})
                    page.goto(origin+'/a/xlayer-telemetry-app/overview?'+urlencode(params))
                    page.locator('.xlt-secondary-signals > summary').click()
                    page.locator('.xlt-kpi .xlt-spark').first.wait_for(timeout=30000)
                    overlaps=page.locator('.xlt-kpi').evaluate_all("""cards=>cards.flatMap(card=>{const s=card.querySelector('.xlt-spark');if(!s)return [];const a=s.getBoundingClientRect();return [...card.querySelectorAll('strong,small,.xlt-eyebrow')].filter(e=>{const b=e.getBoundingClientRect();return a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top}).map(e=>e.innerText)})""")
                    assert not overlaps,overlaps
                    page.locator('.xlt-primary-kpis').screenshot(path=str(args.output/f'kpis-{theme}-{width}.png'))
                    page.close()
        finally:browser.close()
    assert not errors,errors
    result={'checks':checks,'browser_errors':errors,'scope':'Scenes resource Matrix + native legend + KPI overlap;  synthetic data; no operation attribution'}
    (args.output/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--browser',default=None)
    validate(parser.parse_args())
