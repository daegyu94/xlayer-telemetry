"""Same SDK record ID across two clusters through actual Loki/Grafana; synthetic."""
import argparse
import json
from datetime import datetime
from pathlib import Path
import sys
import time
import uuid
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from xlayer_telemetry.step_history import StepHistoryWriter


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--browser')
    args=parser.parse_args()
    connection=json.loads((args.state/'connection.json').read_text())
    if any(urlparse(connection[key]).hostname not in ('127.0.0.1','localhost','::1') for key in ('grafana','loki')):
        parser.error('Use an owned loopback demo')
    args.output.mkdir(parents=True,exist_ok=False)
    raw={'step':500,'data':{'perf/time_per_step':2,'timing_s/gen':1,'perf/total_num_tokens':128}}
    rows=[];end=time.time()-2
    run_id='cluster-collision-'+uuid.uuid4().hex[:8]
    for cluster,stamp in [('collision-a',end-20),('collision-b',end)]:
        writer=StepHistoryWriter(args.output/cluster/'steps.jsonl',run_id=run_id,node='gpu-node-0',worker_id='driver',clock=lambda:stamp)
        row=writer.append(raw);row['data_origin']='synthetic'
        rows.append((cluster,row))
    assert rows[0][1]['record_id']==rows[1][1]['record_id']
    streams=[{'stream':{'signal':'verl_step','cluster':cluster,'node':'gpu-node-0','run_id':row['run_id'],'data_origin':'synthetic'},
              'values':[[str(int(row['observed_at']*1e9)),json.dumps(row)]]} for cluster,row in rows]
    request=Request(connection['loki']+'/loki/api/v1/push',data=json.dumps({'streams':streams}).encode(),headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=10) as response:assert response.status==204
    def epoch(value):return int(value) if value.isdigit() else round(datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()*1000)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'],**({'executable_path':args.browser} if args.browser else {}))
        page=browser.new_page(viewport={'width':1440,'height':1000})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        base=connection['grafana']+'/a/xlayer-telemetry-app/overview?'+urlencode({'from':'now-5m','to':'now','var-cluster':'$__all','var-run_id':run_id,'var-source_node':'gpu-node-0','var-node':'gpu-node-0'})
        for index,(cluster,row) in enumerate(rows):
            page.goto(base)
            options=page.locator('select[aria-label="Completed Step"] option[data-record-id="'+row['record_id']+'"]')
            options.nth(1).wait_for(state='attached',timeout=30000)
            assert options.count()==2
            values=options.evaluate_all('(options)=>options.map(option=>({value:option.value,text:option.innerText}))')
            assert len({item['value'] for item in values})==2
            assert all(any(name in item['text'] for item in values) for name,_ in rows)
            page.get_by_text('Completed Steps · choose another investigation',exact=True).click()
            table=page.get_by_role('table').filter(has=page.get_by_role('columnheader',name='Cluster',exact=True))
            assert table.get_by_role('row').count()==3
            table.screenshot(path=str(args.output/'all-clusters.png'))
            if index==0:
                option=next(item['value'] for item in values if item['text'].startswith(cluster+' /'))
                page.get_by_role('combobox',name='Completed Step').select_option(option)
            else:
                table.get_by_role('row').filter(has_text=cluster).get_by_role('button',name='Analyze Step 500 →',exact=True).click()
            page.wait_for_function('(cluster)=>new URLSearchParams(location.search).get("var-cluster")===cluster',arg=cluster)
            params=parse_qs(urlparse(page.url).query)
            assert params['var-run_id']==[run_id] and params['var-source_node']==['gpu-node-0']
            assert params['var-record_id']==[row['record_id']]
            assert epoch(params['from'][0])==row['window_start_ms'] and epoch(params['to'][0])==row['window_end_ms']
            page.get_by_role('combobox',name='Completed Step').locator('option:checked').filter(has_text=cluster).wait_for(state='attached',timeout=15000)
            page.screenshot(path=str(args.output/(cluster+'-selected.png')))
        assert not errors,errors
        browser.close()
    result={'same_producer_record_id':True,'both_observations_visible':True,'dropdown_and_table_context':True,
            'clusters':[cluster for cluster,_ in rows],'browser_errors':errors,'scope':'SDK + real Loki/Grafana on one host; synthetic records, no physical multi-cluster workload'}
    (args.output/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
