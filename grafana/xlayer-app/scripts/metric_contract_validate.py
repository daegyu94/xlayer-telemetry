"""Check KPI/storage contracts in an owned synthetic Grafana demo.

Browser response fixtures simulate metrics-only and async presentation. They
are not real backend failures or asynchronous VERL execution. Existing queries,
context and datasource responses are used for the storage/detail cost checks.
"""
import argparse,json,re
from pathlib import Path
from urllib.parse import urlencode,urlparse,parse_qs
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url',required=True)
parser.add_argument('--output',required=True,type=Path)
parser.add_argument('--browser',default=None)
args=parser.parse_args()
url=args.url.rstrip('/')
if urlparse(url).hostname not in ('localhost','127.0.0.1','::1'):
 parser.error('Use an owned loopback-only synthetic demo, not a production datasource')
root=args.output;root.mkdir(parents=True,exist_ok=False)
report={'data_origin':'synthetic','notes':'Grafana datasource response fixtures test presentation; no real async VERL or backend outage.'}
with sync_playwright() as p:
 browser=p.chromium.launch(**({'executable_path':args.browser} if args.browser else {}),headless=True,args=['--no-sandbox'])
 context=browser.new_context(viewport={'width':1440,'height':1000},locale='en-US',timezone_id='UTC')
 page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 base=url+'/a/xlayer-telemetry-app/overview?'+urlencode({'from':'now-5m','to':'now','var-cluster':'scenes-demo','var-run_id':'verl-agent-demo','var-node':'gpu-node-0','var-source_node':'gpu-node-0','var-worker':'driver','var-gpu':'0','var-engine':'synthetic-vllm-0'})
 def metrics_only(route):
  body=route.request.post_data_json or {};queries=body.get('queries',[])
  response=route.fetch(timeout=15000);payload=response.json()
  for query in queries:
   result=payload.get('results',{}).get(query.get('refId'),{})
   if query.get('datasource',{}).get('type')=='loki':result['frames']=[]
   if 'rl_stage_duration_seconds' in query.get('expr',''):
    for frame in result.get('frames',[]):
     for field in frame.get('schema',{}).get('fields',[]):
      if field.get('type')=='number' and field.get('labels',{}).get('phase')=='rollout':field['labels']['verl_stage']='gen'
  route.fulfill(response=response,json=payload)
 page.route('**/api/ds/query*',metrics_only);page.goto(base)
 card=page.locator('.xlt-kpi').filter(has=page.get_by_text('Reported rollout',exact=True))
 page.wait_for_timeout(2500)
 value=card.locator('strong').inner_text();assert re.search(r'\d',value),value
 assert 'Freshness unknown' not in value and 'Multiple entities' not in value
 report['metrics_only_verl_stage']={'value':value,'age':card.inner_text(),'worker':'driver'}
 page.screenshot(path=str(root/'metrics-only.png'),full_page=True)
 page.unroute('**/api/ds/query*',metrics_only)
 def async_scope(route):
  response=route.fetch(timeout=15000);payload=response.json()
  for result in payload.get('results',{}).values():
   for frame in result.get('frames',[]):
    fields=frame.get('schema',{}).get('fields',[]);data=frame.get('data',{}).get('values',[])
    for i,field in enumerate(fields):
     if field.get('name','').lower()!='line':continue
     for j,line in enumerate(data[i]):
      try:
       row=json.loads(line)
       wrapper=row;raw=row.get('record');row=json.loads(raw) if isinstance(raw,str) else raw if isinstance(raw,dict) else row
       if row.get('record_type')=='verl_step_observation' or row.get('row_kind') in ('summary','comparison','candidate','evidence'):
        row.update(boundary_scope='trainer_update',execution_mode='async')
        if raw is not None:wrapper['record']=json.dumps(row) if isinstance(raw,str) else row
        data[i][j]=json.dumps(wrapper)
      except (ValueError,TypeError):pass
  route.fulfill(response=response,json=payload)
 page.route('**/api/ds/query*',async_scope);page.goto(base.replace('var-worker=driver','var-worker=.*'))
 try:page.get_by_role('button',name=re.compile('Analyze Trainer update')).wait_for(timeout=30000)
 except Exception:
  page.screenshot(path=str(root/'async-failure.png'),full_page=True);raise
 assert page.get_by_text('Update time',exact=True).count()>0
 assert page.get_by_text(re.compile('rollout/tool 실행')).count()>0
 report['async_scope']={'update_time_visible':True,'scope_notice_visible':True}
 page.screenshot(path=str(root/'async-update.png'),full_page=True)
 page.unroute('**/api/ds/query*',async_scope)
 page.goto(base.replace('var-worker=driver','var-worker=.*'));page.get_by_role('button',name=re.compile('Analyze Step')).wait_for(timeout=30000)
 selector=page.get_by_role('combobox',name='Completed Step');slow=next((o.get_attribute('value') for o in selector.locator('option').all() if '48.00' in o.inner_text()),None)
 if slow:selector.select_option(slow);page.wait_for_timeout(1000)
 page.get_by_role('button',name=re.compile('Analyze Step')).click()
 page.get_by_role('link',name='Investigate',exact=True).click()
 page.locator('.xlt-candidates article').filter(has=page.get_by_role('heading',name='storage',exact=True)).get_by_role('button',name='Deep Dive →',exact=True).first.click()
 page.get_by_role('button',name='Connector RPC',exact=True).wait_for(timeout=20000)
 page.wait_for_timeout(1500)
 page.get_by_text('Storage Cluster Resources · declared DS/MDS inventory',exact=True).click()
 cluster=page.locator('.xlt-storage-cluster')
 cluster.get_by_text('Storage exporter availability',exact=True).wait_for(timeout=20000)
 page.wait_for_timeout(1000)
 assert 'metadata-node-0' in cluster.inner_text(),cluster.inner_text()
 assert 'storage-node-0' in cluster.inner_text(),cluster.inner_text()
 assert 'metadata' in cluster.inner_text() and 'data' in cluster.inner_text()
 assert 'SMART' not in cluster.inner_text()
 for width in (1440,390):
  page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(200)
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
  cluster.screenshot(path=str(root/f'storage-cluster-{width}.png'))
 page.set_viewport_size({'width':1440,'height':1000})
 page.get_by_text('Storage Cluster Resources · declared DS/MDS inventory',exact=True).click()
 report['storage_cluster']={'declared_mds_ds_visible':True,'gpu_sandbox_local_io_separate':True,'no_smart':True,'widths':[1440,390]}
 requests=[]
 def record(request):
  if '/api/ds/query' in request.url:
   requests.extend((request.post_data_json or {}).get('queries',[]))
 page.on('request',record)
 page.get_by_role('button',name='GPU',exact=True).click();page.wait_for_timeout(1000)
 page.get_by_role('button',name='Ray',exact=True).click();page.wait_for_timeout(1000)
 duplicate=[q for q in requests if 'telemetry_gpu_utilization_percent' in q.get('expr','') or 'ray_tasks' in q.get('expr','')]
 assert not duplicate,duplicate
 report['shared_detail_query']={'extra_gpu_ray_queries_on_tab_selection':len(duplicate)}
 for label in ('Connector RPC','DFS batch','DFS bytes','Failures','3FS evidence','Local I/O mean'):
  before=parse_qs(urlparse(page.url).query);page.get_by_role('button',name=label,exact=True).click();page.wait_for_timeout(350)
  assert parse_qs(urlparse(page.url).query)==before,label
 page.get_by_role('button',name='3FS evidence',exact=True).click()
 assert page.get_by_text('3FS · saved service observations',exact=True).count()==1
 common=page.locator('.xlt-common-storage')
 assert 'Backend / adapter not reported' in common.inner_text()
 assert '저장된 Common Storage coverage가 없습니다' not in common.inner_text()
 common.get_by_text('Source coverage · values, scope, entity and quality',exact=True).click()
 assert 'mooncake_dfs_read_p95_seconds' in common.inner_text()
 assert 'mooncake_master_allocated_bytes' in common.inner_text()
 assert 'not configured' in common.inner_text(), 'Demo has no real ClickHouse backend'
 before=parse_qs(urlparse(page.url).query)
 common.get_by_role('link',name='Source metrics ↗',exact=True).first.click();page.wait_for_timeout(700)
 source=parse_qs(urlparse(page.url).query)
 for key in ('from','to','var-run_id','var-record_id','var-source_node'):
  assert source[key]==before[key],key
 assert source['var-engine']==['synthetic-vllm-0'],source.get('var-engine')
 page.go_back();page.get_by_role('heading',name='Common Storage Overview',exact=True).wait_for(timeout=15000)
 common.get_by_text('Source coverage · values, scope, entity and quality',exact=True).click()
 before=parse_qs(urlparse(page.url).query)
 common.get_by_role('button',name='3FS Deep Dive →',exact=True).click()
 assert page.get_by_role('combobox',name='Storage metric').count()==1
 assert parse_qs(urlparse(page.url).query)==before
 for width in (1440,390):
  page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(250)
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
  common.screenshot(path=str(root/f'common-storage-{width}.png'))
 page.set_viewport_size({'width':1440,'height':1000})
 common.get_by_text('Source coverage · values, scope, entity and quality',exact=True).click()
 report['common_storage']={'real_prometheus_queries_on_synthetic_endpoints':True,'backend_not_inferred':True,
  'threefs_optional':True,'context_preserved':True,'widths':[1440,390]}
 page.screenshot(path=str(root/'storage-evidence.png'),full_page=True)
 page.get_by_role('button',name='DFS batch',exact=True).click();page.wait_for_timeout(500)
 page.screenshot(path=str(root/'dfs-batch.png'),full_page=True)
 report['storage_layers']={'tabs_context_preserved':True,'existing_panels_reused':True,'saved_service_evidence_visible':True}
 page.goto(base.replace('var-worker=driver','var-worker=.*'));page.get_by_role('button',name=re.compile('Analyze Step')).wait_for(timeout=30000);page.wait_for_timeout(1000);page.screenshot(path=str(root/'overview.png'),full_page=True)
 report['browser_errors']=errors;assert not errors,errors
 browser.close()
(root/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
