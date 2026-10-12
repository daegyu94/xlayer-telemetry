"""Saved rule/LLM clock parity and Matrix entity → canonical query regressions.

Uses one owned Grafana/Loki/Prometheus demo. LLM answers, clock states, and the second vLLM
endpoint are explicit fixtures, not model inference or physical hardware.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen
from design_validate import owned_origin
from ci_demo import native_dashboard_back

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from xlayer_telemetry.analysis.diagnostic_artifacts import write_report
from xlayer_telemetry.analysis.llm_diagnosis import packet_from_report, validate_diagnosis
from xlayer_telemetry.analysis.llm_investigation import project_result


def fixtures(args, connection):
    source=next(path for path in sorted(args.state.glob('fixture-*/diagnostics/latest.json'))
                if json.loads(path.read_text()).get('clock_quality',{}).get('status')=='aligned')
    original=json.loads(source.read_text()); source_root=source.parent.parent
    result={}
    for status in ('aligned','unsafe','unknown'):
        report=deepcopy(original)
        run='matrix-'+status+'-'+str(time.time_ns())
        record_map={}
        def clone(row):
            row=deepcopy(row);row['run_id']=run
            if row.get('record_id'):
                record_map[row['record_id']]=row['record_id']+'-'+status
                row['record_id']=record_map[row['record_id']]
            return row
        streams=[]
        end_ns=int(report['analysis_window']['end']*1000)*1000000
        labels={'cluster':'scenes-demo','node':report['node'],'run_id':run,'data_origin':'synthetic'}
        def stream(signal, rows):
            streams.append({'stream':{**labels,'signal':signal},
                'values':[[str(end_ns-len(rows)+index),json.dumps(row)] for index,row in enumerate(rows)]})
        for path in sorted((source_root/'telemetry-events').glob('*.jsonl')):
            rows=[clone(json.loads(line)) for line in path.read_text().splitlines()]
            if rows:stream('verl_step' if path.name=='verl-steps.jsonl' else 'xlayer_event',rows)
        report['run_id']=run
        report['trigger_record_id']=record_map[report['trigger_record_id']]
        baseline=report['comparison'].get('baseline_record_id')
        if baseline:report['comparison']['baseline_record_id']=record_map[baseline]
        report['clock_quality']['status']=status
        report['clock_quality']['baseline']['status']=status
        root=args.output/'fixtures'/status
        write_report(root/'diagnostics',report)
        packet=packet_from_report(report)
        answer={'record_type':'llm_diagnosis','generated_at':datetime.now(timezone.utc).isoformat(),
            'data_origin':'synthetic','model':'synthetic-clock-review','semantic_review':{'decision':'accept'},
            'context':packet['context'],'current_interval':packet['current_interval'],'observation_packet':packet,
            'diagnosis':{'assessment':'insufficient_evidence','summary':'Synthetic reviewed answer; no model inference.', 'candidates':[], 'limitations':['Synthetic response; no model inference.']}}
        validate_diagnosis(answer['diagnosis'],packet)
        llm_path=project_result(root,answer)
        rule_path=root/'diagnostics/investigation'/f"{report['trigger_record_id']}.jsonl"
        rows=[json.loads(line) for path in (rule_path,llm_path) for line in path.read_text().splitlines()]
        stream('xlayer_diagnosis',rows)
        request=Request(connection['loki']+'/loki/api/v1/push',data=json.dumps({'streams':streams}).encode(),headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=10) as response:assert response.status==204
        result[status]={'var-cluster':'scenes-demo','var-run_id':run,'var-source_node':report['node'],'var-node':report['node'],
            'var-record_id':report['trigger_record_id'],'var-diagnosis_method':'rule','var-gpu':'.*','var-engine':'.*',
            'from':str(int(report['analysis_window']['start']*1000)),'to':str(int(report['analysis_window']['end']*1000)),
            'theme':'light','timezone':'UTC'}
    return result


def run(args):
    from playwright.sync_api import sync_playwright,expect
    origin=owned_origin(args.url);connection=json.loads((args.state/'connection.json').read_text())
    assert owned_origin(connection['grafana'])==origin
    owned_origin(connection['loki'])
    args.output.mkdir(parents=True,exist_ok=False)
    contexts=fixtures(args,connection)
    report={'checks':[],'failures':[],'queries':[], 'scope':'Actual saved rule/LLM projections + Loki/Grafana; synthetic metrics, no inference/hardware'}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],**({'executable_path':args.browser} if args.browser else {}))
        def check(name,test):
            page=browser.new_page(viewport={'width':1440,'height':1000},locale='en-US',timezone_id='UTC')
            try:
                test(page);report['checks'].append(name)
            except Exception as error:
                report['failures'].append({'check':name,'error':str(error)[:1800],'url':page.url})
                print(name+': '+str(error)[:700],flush=True)
                (args.output/(name+'.html')).write_text(page.content())
            finally:
                page.screenshot(path=str(args.output/(name+'.png')),full_page=True);page.close()
                (args.output/'report.json').write_text(json.dumps(report,indent=2))
        def open_matrix(page, context):
            page.goto(origin+'/a/xlayer-telemetry-app/analyze?'+urlencode(context))
            page.locator('.xlt-matrix').wait_for(timeout=30000)
        def choose(page,subsystem,predicate):
            title={'gpu':'GPU','vllm':'vLLM'}[subsystem]
            selector=page.get_by_role('combobox',name=title+' entity',exact=True)
            selector.locator('option').nth(1).wait_for(state='attached',timeout=20000)
            value=next(option.get_attribute('value') for option in selector.locator('option').all()
                       if option.get_attribute('value') and predicate(dict(json.loads(option.get_attribute('value')))))
            selector.select_option(value)
            return selector,value,page.locator(f'[data-matrix-cell="rollout × {subsystem} evidence"]')
        for theme in ('light','dark'):
            for context in contexts.values():context['theme']=theme
            for status in contexts:
                def clocks(page):
                    open_matrix(page,contexts[status])
                    selector,key,cell=choose(page,'gpu',lambda labels:labels.get('gpu')=='1')
                    expect(page.get_by_label('Correlation clock quality').locator('b')).to_have_text(status,timeout=20000)
                    if status=='aligned':expect(cell.locator('b')).to_have_text(re.compile(r'^\d'),timeout=15000)
                    else:expect(cell.locator('b')).to_have_text('Clock unverified',timeout=15000)
                    value=cell.locator('b').inner_text()
                    page.locator('.xlt-filters > summary').click()
                    method=page.locator('.xlt-filters [data-testid="data-testid template variable"]').filter(has_text='Method').get_by_role('textbox')
                    with page.expect_response(lambda response: response.request.method=='POST' and '/api/ds/query' in response.url and any(
                        'row_kind="summary"' in q.get('expr','') and 'diagnosis_method=~"llm"' in q.get('expr','')
                        for q in (response.request.post_data_json or {}).get('queries',[])),timeout=20000) as fetched:
                        method.fill('llm');method.press('Enter')
                    payload=fetched.value.json(); summaries=[]
                    for result in payload.get('results',{}).values():
                        for frame in result.get('frames',[]):
                            for field,column in zip(frame['schema']['fields'],frame['data']['values']):
                                if field['name']=='Line':
                                    for line in column:
                                        row=json.loads(line);row=row.get('record',row)
                                        if row.get('row_kind')=='summary':summaries.append(row)
                    assert any(row.get('diagnosis_method')=='llm' and row.get('correlation_clock_status')==status for row in summaries),summaries
                    (args.output/('llm-query-'+status+'-'+theme+'.json')).write_text(json.dumps(payload))
                    page.wait_for_function("new URLSearchParams(location.search).get('var-diagnosis_method')==='llm'")
                    expect(page.get_by_label('Correlation clock quality').locator('b')).to_have_text(status,timeout=20000)
                    expect(cell.locator('b')).to_have_text(value,timeout=20000)
                    expect(selector).to_have_value(key)
                    report.setdefault('clock_values',{})[status+'-'+theme]={'rule':value,'llm':cell.locator('b').inner_text()}
                check('clock-'+status+'-'+theme,clocks)
            for subsystem in ('gpu','vllm'):
                def entity(page):
                    queries=[]
                    if subsystem=='vllm':
                        def endpoints(route):
                            request=route.request.post_data_json or {}
                            relevant=[q for q in request.get('queries',[]) if 'vllm:num_requests_waiting' in q.get('expr','')]
                            if not relevant or urlparse(page.url).path.startswith('/d/'):
                                route.continue_();return
                            response=route.fetch(timeout=15000);body=response.json()
                            for query in relevant:
                                result=body['results'][query['refId']];assert result.get('frames')
                                rows=[]
                                for index,instance in enumerate(('matrix-endpoint-a','matrix-endpoint-b')):
                                    frame=deepcopy(result['frames'][0])
                                    for field,column in zip(frame['schema']['fields'],frame['data']['values']):
                                        if field.get('type')=='number':
                                            field.setdefault('labels',{})['instance']=instance
                                            column[:]=[5+12*index]*len(column)
                                    rows.append(frame)
                                result['frames']=rows
                            route.fulfill(response=response,json=body)
                        page.route('**/api/ds/query*',endpoints)
                    def native(request):
                        if request.method=='POST' and '/api/ds/query' in request.url and urlparse(page.url).path.startswith('/d/'):
                            for query in (request.post_data_json or {}).get('queries',[]):
                                expr=query.get('expr','')
                                if ('telemetry_gpu_utilization_percent' if subsystem=='gpu' else 'vllm:num_requests_waiting') in expr:queries.append(expr)
                    page.on('request',native)
                    open_matrix(page,contexts['aligned'])
                    predicate=(lambda labels:labels.get('gpu')=='1') if subsystem=='gpu' else (lambda labels:labels.get('instance')=='matrix-endpoint-b')
                    selector,key,cell=choose(page,subsystem,predicate)
                    expect(cell.locator('b')).to_have_text(re.compile(r'^\d'),timeout=15000)
                    before=parse_qs(urlparse(page.url).query)
                    cell.click()
                    evidence=page.get_by_role('complementary',name='Evidence detail')
                    destination='compute' if subsystem=='gpu' else 'stage'
                    link=evidence.get_by_role('link',name=destination+' ↗',exact=True)
                    href=parse_qs(urlparse(link.get_attribute('href')).query)
                    variable,target=('var-gpu','1') if subsystem=='gpu' else ('var-engine','matrix-endpoint-b')
                    assert href.get(variable)==[target],href
                    for name in ('var-run_id','var-source_node','var-record_id','from','to'):
                        assert href.get(name)==before.get(name),name
                    link.click();page.wait_for_url('**/d/**')
                    page.locator('.xlt-matrix').wait_for(state='detached',timeout=30000)
                    page.wait_for_function("new URLSearchParams(location.search).has('orgId')",timeout=15000)
                    # The relevant native panel must enter the viewport to issue its query.
                    panel=page.get_by_text('GPU utilization' if subsystem=='gpu' else 'Live rollout request queue',exact=True)
                    panel.scroll_into_view_if_needed()
                    with page.expect_response(lambda response: response.request.method=='POST' and '/api/ds/query' in response.url and any(
                        ('telemetry_gpu_utilization_percent' if subsystem=='gpu' else 'vllm:num_requests_waiting') in q.get('expr','')
                        for q in (response.request.post_data_json or {}).get('queries',[])),timeout=20000):
                        page.get_by_role('button',name='Refresh',exact=True).click()
                    matcher='gpu=~"1"' if subsystem=='gpu' else 'instance=~"matrix-endpoint-b"'
                    assert queries and all(matcher in expr for expr in queries),queries
                    native_dashboard_back(page,origin)
                    expect(selector).to_have_value(key,timeout=20000)
                    expect(cell.locator('b')).to_have_text(re.compile(r'^\d'),timeout=15000)
                    assert parse_qs(urlparse(page.url).query).get('var-matrix_'+subsystem+'_entity')==before.get('var-matrix_'+subsystem+'_entity')
                    report['queries'].append({'theme':theme,'subsystem':subsystem,'selected':target,'queries':queries})
                check('entity-'+subsystem+'-'+theme,entity)
        browser.close()
    assert not report['failures'],report['failures']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True);parser.add_argument('--state',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path);parser.add_argument('--browser')
    run(parser.parse_args())
