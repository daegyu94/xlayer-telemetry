"""Panel-level rendering/reading contracts against an owned synthetic Grafana.

Actual SDK/Prometheus/Loki data exercises readiness. The 1/4/16-worker response
fixture checks table presentation only, not real distributed worker execution.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
from urllib.parse import urlencode

from design_validate import owned_origin,selected_context


def run(args):
    from playwright.sync_api import sync_playwright
    origin=owned_origin(args.url)
    args.output.mkdir(parents=True,exist_ok=False)
    raw=json.loads(args.context.read_text()) if args.context else {}
    context=selected_context(args.url,raw)
    context.setdefault('var-cluster',['scenes-demo'])
    context.setdefault('var-run_id',['verl-agent-demo'])
    context.setdefault('var-source_node',['gpu-node-0'])
    context.setdefault('var-node',['gpu-node-0'])
    context.update({'var-role':['.*'],'var-engine':['synthetic-vllm-0'],'timezone':['UTC']})
    context.setdefault('from',['now-5m']);context.setdefault('to',['now'])
    viewports=[(1440,1000)]+([(1024,900),(390,844)] if args.full_matrix else [])
    report={'data_origin':'synthetic','captures':[],'checks':[],'failures':[],
            'limits':'CPU/synthetic Browser validation; worker-count response fixture is not multi-node execution.'}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],**({'executable_path':args.browser} if args.browser else {}))
        session=browser.new_context(locale='en-US',timezone_id='Asia/Seoul')
        page=session.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        def go(screen,theme='light',patch=None):
            params={**context,'theme':[theme],**(patch or {})}
            page.goto(origin+'/a/xlayer-telemetry-app/'+screen+'?'+urlencode(params,doseq=True))
            page.locator('.xlt-content').wait_for(timeout=30000)
            return params
        def terminal_highlights():
            page.wait_for_function("(()=>{const cards=Array.from(document.querySelectorAll('.xlt-native-highlights [data-query-state]'));return cards.length>0&&cards.every(x=>!['Loading','NotStarted'].includes(x.dataset.queryState));})()",timeout=20000)
        def open_groups():
            for group in page.locator('.xlt-native-group').all():
                if group.get_attribute('open') is None:group.locator('summary').click()
        def capture(name):
            path=args.output/(name+'.png');page.screenshot(path=str(path),full_page=True)
            report['captures'].append(path.name)
        for width,height in viewports:
            page.set_viewport_size({'width':width,'height':height})
            for theme in ('light','dark'):
                for screen in ('start-here','stage-correlation','storage','investigate','timeline'):
                    name=f'{screen}-{theme}-{width}'
                    try:
                        go(screen,theme,{'var-node':['gpu-node-0'],'var-source_node':['gpu-node-0']} if screen in ('start-here','stage-correlation','storage') else None)
                        if screen=='start-here':
                            text=page.locator('[data-canonical-panel="7"]')
                            text.get_by_text('Down / Stale / N/A:',exact=False).wait_for(timeout=15000)
                            assert text.get_by_role('link',name='Run Overview',exact=True).count()
                        elif screen=='stage-correlation':
                            terminal_highlights()
                            age=page.locator('[data-canonical-panel="3"]')
                            age.get_by_text('Sample age',exact=True).wait_for(timeout=15000)
                            open_groups()
                            cache=page.locator('[data-canonical-panel="28"]')
                            cache.locator('canvas').first.wait_for(timeout=15000)
                            assert 'Loading' not in cache.inner_text()
                        elif screen=='storage':
                            terminal_highlights();open_groups()
                            iops=page.locator('[data-canonical-panel="2"] [data-query-state]')
                            page.wait_for_function("document.querySelector('[data-canonical-panel=\"2\"] [data-query-state]')?.dataset.queryState==='Done'",timeout=15000)
                            assert 'Loading' not in iops.inner_text()
                            page.wait_for_function("(()=>{const panel=document.querySelector('[data-canonical-panel=\"2\"] [data-query-state]');return !!panel&&(!!panel.querySelector('canvas')||!!panel.querySelector('[data-state=\"observed\"]'));})()",timeout=15000)
                        elif screen=='investigate':
                            comparison=page.locator('.xlt-investigation-section')
                            comparison.wait_for(state='visible',timeout=20000)
                            if comparison.count():
                                comparison.locator('tbody tr').first.wait_for(timeout=15000)
                                if width>=1024:
                                    change=comparison.get_by_role('columnheader',name='Change',exact=True).bounding_box()
                                    box=comparison.bounding_box();assert change['x']+change['width']<=box['x']+box['width'], 'Change column clipped'
                        elif screen=='timeline':
                            panel=page.locator('.xlt-native-timeline').first
                            page.wait_for_function("document.querySelector('.xlt-native-timeline')?.dataset.queryState==='Done'",timeout=15000)
                            assert panel.bounding_box()['height']>=320
                            panel.locator('canvas').first.wait_for(timeout=15000)
                            for canvas in panel.locator('canvas').all():
                                a,b=canvas.bounding_box(),panel.bounding_box()
                                assert a['y']+a['height']<=b['y']+b['height']+1,'Timeline canvas clipped'
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'Page overflow'
                        capture(name);report['checks'].append(name)
                    except Exception as error:
                        capture(name+'-failure');report['failures'].append({'check':name,'error':str(error)[:1200]});print(name+': '+str(error)[:500],flush=True)
        page.set_viewport_size({'width':1440,'height':1000})
        for count in (1,4,16):
            def workers(route):
                body=route.request.post_data_json or {}
                if not any('time() - training_sample_timestamp_seconds' in q.get('expr','') for q in body.get('queries',[])):
                    route.continue_();return
                response=route.fetch(timeout=15000);payload=response.json()
                for result in payload.get('results',{}).values():
                    frames=result.get('frames',[])
                    if not frames:continue
                    source=next((frame for frame in frames if any(field.get('labels',{}).get('worker_id') for field in frame.get('schema',{}).get('fields',[]))),None)
                    if not source:continue
                    rows=[]
                    for index in range(count):
                        row=deepcopy(source)
                        for field_index,field in enumerate(row['schema']['fields']):
                            if field.get('type')!='number':continue
                            field.setdefault('labels',{})['worker_id']=f'worker-{index}-long-identity'
                            field['config']={**field.get('config',{}),'displayNameFromDS':f'worker-{index}'}
                            row['data']['values'][field_index]=[0 if index==0 else index]*len(row['data']['values'][field_index])
                        rows.append(row)
                    result['frames']=rows
                route.fulfill(response=response,json=payload)
            try:
                page.route('**/api/ds/query*',workers);go('stage-correlation',patch={'var-node':['gpu-node-0'],'var-source_node':['gpu-node-0']})
                panel=page.locator('[data-canonical-panel="3"]')
                panel.get_by_text('worker-0-long-identity',exact=True).wait_for(timeout=15000)
                assert re.search(r'\b0(?:\.0+)?\s*s\b',panel.inner_text()),'Measured zero lost its seconds unit'
                assert not panel.get_by_role('combobox').count(),'Worker frames remain in a hidden frame selector'
                if count==16:
                    panel.locator('div').evaluate_all('(elements)=>elements.forEach(el=>{if(el.scrollHeight>el.clientHeight+2)el.scrollTop=el.scrollHeight;})')
                    panel.get_by_text('worker-15-long-identity',exact=True).wait_for(timeout=10000)
                capture(f'workers-{count}');report['checks'].append(f'worker-count-{count}-zero-identity')
            except Exception as error:
                capture(f'workers-{count}-failure');report['failures'].append({'check':f'workers-{count}','error':str(error)[:1200]})
            finally:page.unroute('**/api/ds/query*',workers)
        report['page_errors']=errors
        if errors:report['failures'].append({'check':'page-errors','error':errors})
        browser.close()
    (args.output/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'checks':len(report['checks']),'captures':len(report['captures']),'failures':report['failures']},ensure_ascii=False))
    return bool(report['failures'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--context',type=Path,required=True);parser.add_argument('--browser')
    parser.add_argument('--full-matrix',action='store_true')
    raise SystemExit(run(parser.parse_args()))
