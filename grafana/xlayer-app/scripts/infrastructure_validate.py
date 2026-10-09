"""Explore configured resource mappings in owned synthetic Grafana, not hardware."""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urlparse, parse_qs

from playwright.sync_api import sync_playwright
from browser_validate import EntryDiagnostics
from ci_demo import native_dashboard_back, datasource_error_result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--context',type=Path,required=True);parser.add_argument('--browser')
    args=parser.parse_args()
    if urlparse(args.url).hostname not in ('127.0.0.1','localhost','::1'):parser.error('Use an owned loopback stack')
    args.output.mkdir(parents=True,exist_ok=False)
    record=json.loads(args.context.read_text())
    params=record.get('selected_context') or record.get('context');assert params
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'],**({'executable_path':args.browser} if args.browser else {}))
        page=browser.new_page(viewport={'width':1440,'height':1000},locale='en-US',timezone_id='UTC')
        page.on('pageerror',lambda e:errors.append(str(e)))
        diagnostics=EntryDiagnostics(page,args.output,'infrastructure',errors)
        try:
            page.goto(args.url+'/a/xlayer-telemetry-app/infrastructure?'+urlencode(params,doseq=True))
            page.get_by_role('button',name='Inspect gpu-node-0',exact=True).wait_for(timeout=30000)
            page.get_by_role('button',name='Inspect gpu-node-0',exact=True).click()
            resource=page.get_by_role('combobox',name='Infrastructure resource')
            resource.select_option(label='gpu-node-0/gpu-0 · gpu')
            page.get_by_role('heading',name='Selected Resource · gpu-node-0/gpu-0',exact=True).wait_for()
            gpu=parse_qs(urlparse(page.url).query)
            assert gpu['var-gpu']==['0'] and gpu['var-node']==['gpu-node-0']
            for key in ('var-run_id','var-record_id','var-source_node','from','to'):assert gpu[key]==params[key]
            resource.select_option(label='gpu-node-0/eth0 · nic')
            page.get_by_role('heading',name='Selected Resource · gpu-node-0/eth0',exact=True).wait_for()
            assert parse_qs(urlparse(page.url).query)['var-device']==['eth0']
            page.get_by_role('button',name='Inspect storage-node-0',exact=True).click()
            resource.select_option(label='storage-node-0/nvme0n1 · ssd')
            page.get_by_role('heading',name='Selected Resource · storage-node-0/nvme0n1',exact=True).wait_for()
            storage=parse_qs(urlparse(page.url).query)
            assert storage['var-device']==['nvme0n1'] and storage['var-storage_node']==['storage-node-0']
            for key in ('var-run_id','var-record_id','var-source_node','from','to'):assert storage[key]==params[key]
            for width in (1440,390):
                page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(500)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
                page.screenshot(path=str(args.output/f'infrastructure-{width}.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':1000})
            page.get_by_role('link',name=re.compile('^Full resource metrics')).click();page.wait_for_url('**/d/xlayer-data-storage**')
            native=parse_qs(urlparse(page.url).query);assert native['var-device']==['nvme0n1']
            native_dashboard_back(page,args.url)
            page.get_by_role('link',name='Investigate selected Step →',exact=True).click()
            page.get_by_role('heading',name=re.compile('Step Investigation')).wait_for()
            page.get_by_role('link',name='Deep Dive',exact=True).click()
            page.get_by_role('link',name='Logs & Events',exact=True).click()
            page.get_by_role('heading',name='Logs & Events',exact=True).wait_for()
            logs=parse_qs(urlparse(page.url).query)
            for key in ('var-run_id','var-record_id','var-source_node','from','to'):assert logs[key]==params[key]
            for width in (1440,390):
                page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(500)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
                page.screenshot(path=str(args.output/f'logs-{width}.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':1000})
            def query_error(route):
                request=route.request.post_data_json
                if any('telemetry_topology_component_info' in str(q.get('expr','')) for q in request.get('queries',[])):
                    route.fulfill(status=200,content_type='application/json',body=json.dumps(datasource_error_result(request['queries'])))
                else:route.continue_()
            page.route('**/api/ds/query*',query_error)
            page.goto(args.url+'/a/xlayer-telemetry-app/infrastructure?'+urlencode(params,doseq=True))
            page.get_by_text('Query failure · inventory is unavailable; no healthy state is inferred.',exact=True).wait_for(timeout=30000)
            assert page.get_by_role('button',name='Inspect gpu-node-0',exact=True).count()==0
            page.screenshot(path=str(args.output/'infrastructure-query-failure.png'),full_page=True)
            page.unroute('**/api/ds/query*',query_error)
            page.route('**/api/dashboards/uid/xlayer-run-logs',lambda route:route.fulfill(status=404,content_type='application/json',body='{}'))
            page.goto(args.url+'/a/xlayer-telemetry-app/logs?'+urlencode(params,doseq=True))
            page.get_by_text('Loki / canonical Run Logs is unavailable. This is not an empty successful log query.',exact=True).wait_for(timeout=30000)
            page.screenshot(path=str(args.output/'logs-unavailable.png'),full_page=True)
        except Exception as error:
            diagnostics.fail(error);raise
        finally:browser.close()
    assert not errors,errors
    (args.output/'validation.json').write_text(json.dumps({'browser_errors':errors,'widths':[1440,390],
        'resource_context_preserved':True,'node_gpu_nic_ssd_selection':True,'canonical_metrics_reused':True,
        'topology_query_failure_and_logs_unavailable':True,
        'scope':'Configured synthetic topology and actual Prometheus/Loki/Grafana; no physical connectivity, GPU or 3FS verification'},indent=2)+'\n')


if __name__=='__main__':main()
