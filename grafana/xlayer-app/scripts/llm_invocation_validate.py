#!/usr/bin/env python3
"""Actual projection -> Loki -> App isolation; synthetic responses, no LLM inference."""
import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from xlayer_telemetry.analysis.llm_diagnosis import packet_from_report
from xlayer_telemetry.analysis.llm_investigation import project_result


def validate(args):
    from playwright.sync_api import sync_playwright
    connection=json.loads((args.state/'connection.json').read_text())
    if connection['grafana'].rstrip('/')!=args.url.rstrip('/') or any(
            urlparse(connection[key]).hostname not in ('127.0.0.1','localhost','::1') for key in ('grafana','loki')):
        raise ValueError('Use the matching disposable loopback demo and connection.json')
    params=json.loads(args.context.read_text())['selected_context']
    selected=params['var-record_id'][0]
    reports=[json.loads(line) for path in args.state.glob('fixture-*/diagnostics/diagnostics.jsonl')
             for line in path.read_text().splitlines()]
    report=next(row for row in reports if row.get('trigger_record_id')==selected)
    packet=packet_from_report(report)
    observation=next(row for row in packet['observations'] if row['observation_scope']!='application')
    outputs=[]
    for index,name in enumerate(('A','B')):
        result={'record_type':'llm_diagnosis','data_origin':'synthetic','model':'synthetic-model-'+name,
            'generated_at':(datetime.now(timezone.utc)+timedelta(seconds=index)).isoformat(),
            'semantic_review':{'decision':'accept'},'current_interval':report['analysis_window'],
            'context':{key:report.get(key) for key in ('run_id','node','step','trigger_record_id')},
            'observation_packet':packet,'diagnosis':{'assessment':'bottleneck_suspected',
                'summary':'Synthetic invocation '+name+' response; not a causal conclusion.',
                'candidates':[{'title':'Synthetic invocation '+name+' hypothesis','explanation':'Projection isolation fixture.',
                    'evidence_ids':[observation['id']],'counter_evidence_ids':[],
                    'missing_evidence':['Synthetic response; no model inference'],
                    'observation_scope':observation['observation_scope']}]}}
        path=project_result(args.state/'invocation-fixtures',result)
        rows=[json.loads(line) for line in path.read_text().splitlines()]
        outputs.append(rows)
        try:
            end_ms=int(float(params['to'][0]))
        except ValueError:
            end_ms=int(datetime.fromisoformat(params['to'][0].replace('Z','+00:00')).timestamp()*1000)
        end_ns=end_ms*1000000
        stream={'signal':'xlayer_diagnosis','cluster':params['var-cluster'][0],
                'node':report['node'],'run_id':report['run_id'],'data_origin':'synthetic'}
        values=[[str(end_ns-10000+index*1000+i),json.dumps(row)] for i,row in enumerate(rows)]
        request=Request(connection['loki']+'/loki/api/v1/push',data=json.dumps({'streams':[{'stream':stream,'values':values}]}).encode(),
                        headers={'Content-Type':'application/json'},method='POST')
        with urlopen(request,timeout=10) as response:assert response.status==204
    assert outputs[0][0]['diagnosis_invocation_id']!=outputs[1][0]['diagnosis_invocation_id']
    args.output.mkdir(parents=True,exist_ok=False)
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,**({'executable_path':args.browser} if args.browser else {}))
        page=browser.new_page(locale='en-US',timezone_id='UTC')
        page.on('pageerror',lambda error:errors.append(str(error)))
        params['var-diagnosis_method']=['llm']
        params.pop('var-candidate_id',None)
        for theme,width in (('light',1440),('dark',1440),('light',390),('dark',390)):
            page.set_viewport_size({'width':width,'height':1000})
            params['theme']=[theme]
            page.goto(args.url+'/a/xlayer-telemetry-app/investigate?'+urlencode(params,doseq=True),wait_until='domcontentloaded')
            page.get_by_text('Synthetic invocation B hypothesis',exact=False).first.wait_for(timeout=30000)
            assert page.get_by_text('Synthetic invocation A hypothesis',exact=False).count()==0
            assert page.get_by_text('Synthetic invocation A response',exact=False).count()==0
            page.screenshot(path=str(args.output/f'llm-invocation-{theme}-{width}.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            page.get_by_role('button',name='Deep Dive →',exact=True).click()
            page.get_by_text('Synthetic invocation B hypothesis',exact=False).first.wait_for(timeout=20000)
            assert page.get_by_text('Synthetic invocation A hypothesis',exact=False).count()==0
            context=parse_qs(urlparse(page.url).query)
            assert context['var-record_id']==[selected] and context['var-diagnosis_method']==['llm']
            page.go_back(wait_until='domcontentloaded')
            page.get_by_text('Synthetic invocation B hypothesis',exact=False).first.wait_for(timeout=20000)
        browser.close()
    assert not errors,errors
    (args.output/'validation.json').write_text(json.dumps({'status':'passed','browser_errors':errors,
        'invocations':2,'models':['synthetic-model-A','synthetic-model-B'],'themes':['light','dark'],'widths':[1440,390],
        'scope':'Actual saved-report projection/Loki/Grafana; synthetic model responses, no inference or physical cluster'},indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('state','context','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--url',required=True);parser.add_argument('--browser')
    validate(parser.parse_args())


if __name__=='__main__':main()
