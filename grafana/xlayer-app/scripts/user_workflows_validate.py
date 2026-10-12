"""Rule → stored projection → Loki → cards, and measured-host query drill-down.

Network uses the actual rule engine and saved SDK artifacts. Sandbox uses a
labeled two-host datasource response fixture; downstream PromQL is real Grafana
interpolation. Neither fixture claims hardware measurements or attribution.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen
from design_validate import owned_origin

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def network_fixture(output, loki):
    from xlayer_telemetry.demos.diagnosis import generate
    from xlayer_telemetry.analysis.diagnosis_analysis import evaluate_rules, compare_signals
    from xlayer_telemetry.analysis.diagnostic_artifacts import write_report
    run_id = 'network-review-' + str(time.time_ns())
    report = generate(output, run_id=run_id, node='gpu-node-0', publish_diagnosis=False)
    baseline = {'communication_duration_seconds':1, 'rdma_bytes_per_second':10, 'gpu_utilization_percent':90}
    current = {'communication_duration_seconds':3, 'rdma_bytes_per_second':30, 'gpu_utilization_percent':50}
    report['candidates'] = evaluate_rules(current, baseline, thresholds={},
        context={'window': report['analysis_window'], 'node':'gpu-node-0'})
    report['comparison']['signals'] = compare_signals(current, baseline)
    candidate = next(c for c in report['candidates'] if c['id']=='communication_bound')
    assert candidate['component']=='network' and candidate['state']=='strong_signal'
    write_report(output/'diagnostics', report)
    projection = output/'diagnostics/investigation'/f"{report['trigger_record_id']}.jsonl"
    saved = [json.loads(line) for line in projection.read_text().splitlines()]
    assert any(r.get('candidate_id')=='communication_bound' and r.get('component')=='network' for r in saved)
    streams=[]
    for signal,path in [('xlayer_diagnosis',projection),('verl_step',output/'telemetry-events/verl-steps.jsonl')]:
        rows=[json.loads(line) for line in path.read_text().splitlines()]
        end=report['analysis_window']['end']
        streams.append({'stream':{'signal':signal,'cluster':'scenes-demo','node':'gpu-node-0','run_id':run_id,'data_origin':'synthetic'},
            'values':[[str(int((r.get('observed_at') or end)*1000)*1000000-len(rows)+i),json.dumps(r)] for i,r in enumerate(rows)]})
    request=Request(loki.rstrip('/')+'/loki/api/v1/push',data=json.dumps({'streams':streams}).encode(),headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=10) as response: assert response.status==204
    return report


def run(args):
    from playwright.sync_api import sync_playwright, expect
    origin=owned_origin(args.url)
    args.output.mkdir(parents=True,exist_ok=False)
    connection=json.loads((args.state/'connection.json').read_text())
    assert owned_origin(connection['grafana'])==origin
    stored=network_fixture(args.output/'network-fixture',owned_origin(connection['loki']))
    report={'checks':[],'failures':[],'network_scope':'actual rule + saved projection + real Loki/Grafana',
            'sandbox_scope':'two-host HTTP response fixture + actual outbound Grafana query', 'native_queries':[]}
    params={'var-cluster':'scenes-demo','var-run_id':stored['run_id'],'var-node':'gpu-node-0','var-source_node':'gpu-node-0',
        'var-record_id':stored['trigger_record_id'],'from':str(int(stored['analysis_window']['start']*1000)),
        'to':str(int(stored['analysis_window']['end']*1000)),'timezone':'UTC','var-sandbox_node':'.*'}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],**({'executable_path':args.browser} if args.browser else {}))
        def check(name, action):
            page=browser.new_page(viewport={'width':1440,'height':1000},locale='en-US')
            try:
                action(page)
                report['checks'].append(name)
            except Exception as error:
                report['failures'].append({'check':name,'error':str(error)[:1800],'url':page.url})
                print(name+': '+str(error)[:1000],flush=True)
            finally:
                page.screenshot(path=str(args.output/(name+'.png')),full_page=True)
                page.close()
                (args.output/'report.json').write_text(json.dumps(report,indent=2))
        for theme in ('light','dark'):
            for screen in ('overview','analyze'):
                def network(page):
                    with page.expect_response(lambda response: '/api/ds/query' in response.url and response.request.method=='POST' and
                        any('row_kind="candidate"' in q.get('expr','') for q in (response.request.post_data_json or {}).get('queries',[])),timeout=30000) as response:
                        page.goto(origin+'/a/xlayer-telemetry-app/'+screen+'?'+urlencode({**params,'theme':theme}))
                    queried=response.value.json()
                    assert 'communication_bound' in json.dumps(queried), 'Saved network candidate absent from actual Grafana query response'
                    (args.output/('network-query-'+screen+'-'+theme+'.json')).write_text(json.dumps(queried))
                    page.locator('.xlt-content').wait_for(timeout=30000)
                    if screen=='overview':page.locator('.xlt-execution-details > summary').click()
                    card=page.locator('.xlt-health-grid article').filter(has=page.get_by_text('Network',exact=True))
                    expect(card).to_be_visible(timeout=15000)
                    expect(card).to_contain_text('Strong signal',timeout=15000)
                    expect(card).not_to_contain_text('Not assessed')
                    page.set_viewport_size({'width':390,'height':844})
                    card.scroll_into_view_if_needed()
                    expect(card).to_be_visible()
                    expect(card).to_contain_text('Strong signal')
                    card.screenshot(path=str(args.output/('network-card-'+screen+'-'+theme+'.png')))
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                check('network-'+screen+'-'+theme,network)
            for host in ('sandbox-a','sandbox-b'):
                def sandbox(page):
                    seen=[]
                    def response_fixture(route):
                        queries=(route.request.post_data_json or {}).get('queries',[])
                        relevant=[q for q in queries if 'sandbox_io_pressure_ratio' in q.get('expr','')]
                        if not relevant:route.continue_();return
                        if urlparse(page.url).path.startswith('/d/'):
                            seen.extend(q['expr'] for q in relevant)
                            route.continue_();return
                        response=route.fetch(timeout=15000);body=response.json()
                        for query in relevant:
                            result=body.get('results',{}).get(query['refId'],{})
                            sources=result.get('frames',[])
                            assert sources,'Live sandbox source must exist before applying the two-host fixture'
                            frames=[]
                            for index,node in enumerate(('sandbox-a','sandbox-b')):
                                frame=deepcopy(sources[0])
                                for field,column in zip(frame['schema']['fields'],frame['data']['values']):
                                    if field.get('type')=='number':
                                        field.setdefault('labels',{}).update(nodename=node,cluster='scenes-demo')
                                        column[:]=[.2+.4*index]*len(column)
                                frames.append(frame)
                            result['frames']=frames
                        route.fulfill(response=response,json=body)
                    page.route('**/api/ds/query*',response_fixture)
                    context={**params,'var-run_id':'verl-agent-demo','var-record_id':'.*','from':'now-5m','to':'now','theme':theme}
                    page.goto(origin+'/a/xlayer-telemetry-app/analyze?'+urlencode(context))
                    pressure=page.locator('.xlt-card').filter(has=page.get_by_text('Sandbox I/O PSI',exact=True))
                    expect(pressure.locator('.xlt-pressure-value')).to_have_count(2,timeout=30000)
                    selected=pressure.locator('.xlt-pressure-value').filter(has=page.locator('small.xlt-entity').filter(has_text=host))
                    selected.get_by_role('link',name='Inspect entity → ↗',exact=True).click()
                    page.wait_for_url('**/d/agent-rl-stage-correlation/**')
                    selected_context=parse_qs(urlparse(page.url).query)
                    assert selected_context.get('var-sandbox_node')==[host],selected_context
                    assert selected_context.get('var-source_node')==['gpu-node-0']
                    page.get_by_text('Sandbox signals (optional)',exact=True).click()
                    panel=page.get_by_text('Sandbox worker and device pressure',exact=True)
                    panel.scroll_into_view_if_needed()
                    with page.expect_response(lambda response: response.request.method=='POST' and '/api/ds/query' in response.url and
                        any('sandbox_io_pressure_ratio' in q.get('expr','') for q in (response.request.post_data_json or {}).get('queries',[])),timeout=20000):
                        page.get_by_role('button',name='Refresh',exact=True).click()
                    assert seen and all(f'nodename=~"{host}"' in expr for expr in seen),seen
                    from ci_demo import native_dashboard_back
                    native_dashboard_back(page,origin)
                    assert parse_qs(urlparse(page.url).query).get('var-sandbox_node')==['.*']
                    page.go_forward()
                    page.wait_for_url('**/d/agent-rl-stage-correlation/**')
                    page.reload()
                    # Native collapsed rows are intentionally reset on reload.
                    # Wait for dashboard activation before checking restored scope.
                    page.get_by_role('button',name='Refresh',exact=True).wait_for(timeout=30000)
                    page.wait_for_function("new URLSearchParams(location.search).has('orgId')",timeout=15000)
                    restored=parse_qs(urlparse(page.url).query)
                    assert restored.get('var-sandbox_node')==[host] and restored.get('var-source_node')==['gpu-node-0']
                    assert all(f'nodename=~"{host}"' in expr for expr in seen),seen
                    report['native_queries'].append({'theme':theme,'host':host,'queries':seen})
                check('sandbox-'+host+'-'+theme,sandbox)
        browser.close()
    (args.output/'report.json').write_text(json.dumps(report,indent=2))
    assert not report['failures'],report['failures']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True);parser.add_argument('--state',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path);parser.add_argument('--browser')
    run(parser.parse_args())
