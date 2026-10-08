"""Storage inventory is declarative; resource queries never depend on SMART."""
import json
import os
import platform
import subprocess
import sys
import shutil
import pytest
from pathlib import Path

ROOT=Path(__file__).parents[1]


def walk(rows):
    for row in rows:
        yield row
        yield from walk(row.get('panels',[]))


def test_storage_dashboard_has_performance_inventory_and_no_smart_queries():
    dashboard=json.loads((ROOT/'examples/dashboards/data-storage.json').read_text())
    assert 'smartctl' not in json.dumps(dashboard) and 'SMART' not in json.dumps(dashboard)
    variables={v['name']:v for v in dashboard['templating']['list']}
    assert 'ssd' not in variables
    assert 'resource_node' in variables['storage_node']['query']
    panels=list(walk(dashboard['panels']))
    assert {'Storage Cluster Overview','Storage node CPU and memory','Storage node network throughput','Storage exporter availability','Declared storage node inventory'} <= {p['title'] for p in panels}
    assert {1,2,3,4,5,7,8,30,31,32,33,34,101,102,103,104} <= {p['id'] for p in panels}
    for p in panels:
        for target in p.get('targets',[]):
            expr=target.get('expr','')
            if p['title'].startswith('Storage node') or p['title']=='Storage exporter availability':
                assert 'resource_node' in expr and 'on (cluster, nodename)' in expr
                assert 'count by (cluster, component)' in expr


def test_smart_scrape_job_and_installer_dependency_are_removed():
    assert 'storage-smart' not in (ROOT/'examples/dashboards/prometheus.yml').read_text()
    assert 'smartctl' not in (ROOT/'scripts/install_telemetry_tools.sh').read_text()
    assert 'start_smartctl_exporter' not in (ROOT/'scripts/run_telemetry.sh').read_text()


def test_storage_launcher_uses_node_exporter_without_gpu_or_hardware_health_dependency(tmp_path):
    arch='arm64' if platform.machine() in ('aarch64','arm64') else 'amd64'
    tools=tmp_path/'tools'
    exporter=tools/f'node_exporter-1.9.1.linux-{arch}/node_exporter'
    exporter.parent.mkdir(parents=True)
    output=tmp_path/'state';capture=tmp_path/'captured.json'
    exporter.write_text(f'#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\n'
        f'Path({str(capture)!r}).write_text(json.dumps({{"argv":sys.argv[1:],"config":Path({str(output/"textfile/collector.prom")!r}).read_text()}}))\n')
    exporter.chmod(0o755)
    env=os.environ|{'TOOLS_DIR':str(tools),'OUTPUT_DIR':str(output),'NODE_ADDR':'127.0.0.1',
        'ENABLE_GPU_METRICS':'1','TELEMETRY_METRICS_DIR':'','LOKI_PUSH_URL':'','TELEMETRY_LOG_ROOTS':'',
        'DURATION':'','TOPOLOGY_DIR':''}
    result=subprocess.run(['bash',str(ROOT/'scripts/run_telemetry.sh'),'storage'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    recorded=json.loads(capture.read_text())
    assert '--web.listen-address=127.0.0.1:19100' in recorded['argv']
    assert 'telemetry_gpu_collection_enabled 0' in recorded['config']


def test_registered_storage_mapping_configuration_reaches_existing_topology_collector(tmp_path,monkeypatch):
    from xlayer_telemetry.operations.config import load_config, snapshot
    path=tmp_path/'config.toml'
    path.write_text('[telemetry]\nTOPOLOGY_DIR="'+str(tmp_path/'topology')+'"\n')
    config,_=load_config(path)
    assert config['TOPOLOGY_DIR']==str(tmp_path/'topology')
    config['TELEMETRY_HOME']=str(tmp_path/'owned-home')
    assert 'TOPOLOGY_DIR=' in snapshot(config).read_text()


def test_storage_membership_with_real_promql_keeps_nodes_and_ambiguity_distinct(tmp_path):
    tool=os.environ.get('PROMTOOL') or shutil.which('promtool')
    if not tool:pytest.skip('set PROMTOOL for actual storage roster/PromQL evaluation')
    d=json.loads((ROOT/'examples/dashboards/data-storage.json').read_text())
    panels={p['id']:p for p in d['panels']}
    def expr(id,index=0):
        query=panels[id]['targets'][index]['expr']
        for key,value in {'cluster':'lab','node':'.*','storage_node':'.*','storage_system':'.*'}.items():query=query.replace('$'+key,value)
        return query.replace('$__rate_interval','1m')
    inputs=[]
    def series(name,values,**labels):
        fields=','.join(key+'='+json.dumps(value) for key,value in {'job':'telemetry','cluster':'lab',**labels}.items())
        inputs.append({'series':name+'{'+fields+'}','values':values})
    # The publisher is a trainer. It must never be used as the resource owner.
    series('telemetry_topology_component_info','1+0x4',kind='storage',component='ds',role='data',resource_node='data-a',instance='trainer',nodename='trainer')
    series('telemetry_topology_component_info','1+0x4',kind='storage',component='mds',role='metadata',resource_node='meta-a',instance='trainer',nodename='trainer')
    for owner in ('conflict-a','conflict-b'):
        series('telemetry_topology_component_info','1+0x4',kind='storage',component='conflict',role='data',resource_node=owner,instance=owner,nodename=owner)
    series('telemetry_topology_component_info','1+0x4',kind='storage',component='unmapped',role='data',instance='trainer',nodename='trainer')
    series('telemetry_topology_component_info','1+0x4',kind='storage',component='gpu-local-device',role='ssd',resource_node='gpu-a',device='nvme0n1',instance='trainer',nodename='trainer')
    for node,up in [('gpu-a',1),('data-a',1),('meta-a',0),('trainer',1),('conflict-a',1),('conflict-b',1)]:
        series('up',str(up)+'+0x4',nodename=node,instance=node)
        series('node_cpu_seconds_total','0+15x4',nodename=node,instance=node,cpu='0',mode='idle')
    fixture={'evaluation_interval':'30s','tests':[{'interval':'30s','input_series':inputs,'promql_expr_test':[
        {'expr':expr(112),'eval_time':'2m','exp_samples':[
            {'labels':'{__name__="up",cluster="lab",instance="data-a",job="telemetry",nodename="data-a"}','value':1},
            {'labels':'{__name__="up",cluster="lab",instance="meta-a",job="telemetry",nodename="meta-a"}','value':0},
        ]},
        {'expr':expr(113),'eval_time':'2m','exp_samples':[
            {'labels':'{cluster="lab",instance="data-a",job="telemetry",nodename="data-a"}','value':.5},
        ]},
    ]}]}
    path=tmp_path/'storage-roster.json';path.write_text(json.dumps(fixture))
    result=subprocess.run([tool,'test','rules',str(path)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
