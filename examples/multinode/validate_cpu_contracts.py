"""Three disposable CPU network/PID/filesystem namespaces; shared host kernel.

Requires Docker, existing Prometheus/Node Exporter tools and curl test image.
Actual xltel config generation, role-node lifecycle, SDK, native registration,
Prometheus scrape/relabel and diagnosis; no actual GPU/vLLM workload is run.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from urllib.parse import urlencode
from urllib.request import urlopen

from xlayer_telemetry.operations.config import load_config, snapshot
from xlayer_telemetry.prometheus import PrometheusClient
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine

ROOT = Path(__file__).resolve().parents[2]


def command(*args, **kwargs):
    return subprocess.run(list(args),check=True,capture_output=True,text=True,timeout=90,**kwargs).stdout.strip()


def wait_for(callback, seconds=30):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            value=callback()
            if value: return value
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError): pass
        time.sleep(.25)
    raise TimeoutError('CPU contract condition not observed before deadline')


def validate(output, tools, image):
    output=output.resolve(); tools=tools.resolve()
    output.mkdir(parents=True,exist_ok=False)
    prefix='xltel-cpu-'+uuid.uuid4().hex[:12]
    network=prefix+'-net'; names=[]; checks={}
    def docker(*args): return command('docker',*args)
    def execute(name,*args): return docker('exec',name,*args)
    try:
        docker('network','create','--label','xlayer-test='+prefix,network)
        inventory={'schema_version':1,'cluster':{'name':prefix},'nodes':[
            {'name':node,'address':node,'roles':['compute'],'telemetry_home':str(output/node)} for node in ('node-a','node-b')],
            'services':[{'name':node+'-vllm','kind':'vllm','node':node,'target':node+':8000'} for node in ('node-a','node-b')]}
        source=output/'inventory.json'; source.write_text(json.dumps(inventory))
        command(sys.executable,'-m','xlayer_telemetry.cli','cluster','render','--inventory',str(source),
                '--output',str(output/'bundle'),'--prometheus-url','http://monitoring:19090')
        # Explicit topology deployment: generation alone does not publish it.
        for node in ('node-a','node-b'):
            config=output/f'bundle/nodes/{node}.toml'
            with config.open('a') as stream:
                stream.write(f'TOOLS_DIR={json.dumps(str(tools))}\nENABLE_LOGS=true\nLOKI_URL="http://monitoring:13100"\n'
                    f'TELEMETRY_LOG_ROOTS={json.dumps("verl="+str(output/node/"runs"))}\n')
                if node=='node-a': stream.write(f'TOPOLOGY_DIR={json.dumps(str(output/"bundle/topology"))}\n')
        config,_=load_config(output/'bundle/server.toml')
        config.update(TELEMETRY_HOME=str(output/'monitoring-home'),TOOLS_DIR=str(tools),ENABLE_LOGS='1',OUTPUT_DIR=str(output/'monitor-config'),SERVER_CONFIG_ONLY='1',
                      NATIVE_SCRAPE_INTERVAL_SECONDS='2',NATIVE_SCRAPE_TIMEOUT_SECONDS='1',PROMETHEUS_SAMPLE_LIMIT='100000')
        shell=snapshot(config)
        command('bash',str(ROOT/'scripts/run_telemetry.sh'),'server','--config',str(shell),cwd=ROOT,
                env={**os.environ,'PYTHON':sys.executable,'SERVER_CONFIG_ONLY':'1','OUTPUT_DIR':str(output/'monitor-config')})
        # Test-only per-native-source limit; host exporter remains within its
        # generated normal budget. Exceeding this limit fails the whole scrape.
        prometheus_config=output/'monitor-config/prometheus.yml'
        prometheus_config.write_text(prometheus_config.read_text().replace('  - job_name: native\n',
            '  - job_name: native\n    sample_limit: 100\n'))
        loki_config=output/'monitor-config/loki.yaml'
        loki_config.write_text(loki_config.read_text().replace('http_listen_address: 127.0.0.1','http_listen_address: 0.0.0.0'))
        mounts=['--mount',f'type=bind,src={ROOT},dst={ROOT},readonly','--mount',f'type=bind,src={tools},dst={tools},readonly',
                '--mount',f'type=bind,src={output},dst={output}']
        for alias in ('monitoring','node-a','node-b'):
            name=prefix+'-'+alias; names.append(name)
            args=['run','-d','--name',name,'--label','xlayer-test='+prefix,'--network',network,'--network-alias',alias,
                  '--user',f'{os.getuid()}:{os.getgid()}', '--memory','768m','--cpus','1','--pids-limit','256',
                  *mounts,'-w',str(ROOT),'-e','PYTHONPATH='+str(ROOT)]
            if alias=='monitoring':
                args += ['-p','127.0.0.1::19090','-p','127.0.0.1::13100',image,'python',
                         str(ROOT/'examples/multinode/cpu_contract_monitor.py'),str(tools),str(output)]
            else:
                args += [image,'python',str(ROOT/'examples/multinode/cpu_contract_node.py'),str(output/f'bundle/nodes/{alias}.toml')]
            docker(*args)
        monitor=names[0]; a,b=names[1:]
        endpoint='http://'+docker('port',monitor,'19090/tcp')
        loki='http://'+docker('port',monitor,'13100/tcp')
        def query(expression):
            with urlopen(endpoint+'/api/v1/query?'+urlencode({'query':expression}),timeout=3) as response:
                return json.load(response)['data']['result']
        def up(node,job='telemetry'):
            rows=query(f'up{{job="{job}",'+('nodename' if job=='telemetry' else 'node')+f'="{node}"}}')
            return bool(rows) and float(rows[0]['value'][1])==1
        wait_for(lambda:up('node-a') and up('node-b'))
        wait_for(lambda:len(query('training_step_duration_seconds'))==2)
        values={row['metric']['nodename']:float(row['value'][1]) for row in query('training_step_duration_seconds')}
        assert values=={'node-a':10,'node-b':20},values
        assert all(row['metric']['worker_id']=='0' for row in query('training_step_duration_seconds'))
        assert query('telemetry_topology_component_info'), 'explicit publisher did not publish topology'
        checks['generated_config_sdk_node_isolation']=values
        def events():
            with urlopen(loki+'/loki/api/v1/query_range?'+urlencode({'query':'{signal="xlayer_event"}','limit':100}),timeout=3) as response:
                return json.load(response)['data']['result']
        logs=wait_for(lambda:events() if {row['stream'].get('node') for row in events()}=={'node-a','node-b'} else None)
        assert all(json.loads(value[1])['run_id']=='shared-run' for row in logs for value in row['values'])
        checks['sdk_event_alloy_loki_node_isolation']=True
        for name,node in ((a,'node-a'),(b,'node-b')):
            def status():
                response=subprocess.run(['docker','exec',name,'python','-m','xlayer_telemetry.cli','--config',
                    str(output/f'bundle/nodes/{node}.toml'),'status','--role','node','--json'],capture_output=True,text=True,timeout=15)
                state=json.loads(response.stdout)
                (output/(node+'-status.json')).write_text(json.dumps(state,indent=2))
                return state if state['status']=='healthy' else None
            state=wait_for(status)
            assert state['services']['node']['configuration_status']=='matched',state
        checks['remote_prometheus_status']='matched on both isolated localhost namespaces'
        wait_for(lambda:up('node-a','native') and up('node-b','native'))
        native=query('vllm:num_requests_waiting')
        assert {row['metric']['node']:float(row['value'][1]) for row in native}=={'node-a':0,'node-b':18}
        assert all(row['metric']['telemetry_source']=='vllm' and row['metric']['cluster']==prefix and 'run_id' not in row['metric'] for row in native)
        client=PrometheusClient(endpoint)
        window=lambda:{'start':time.time()-12,'end':time.time(),'accuracy':'approximate'}
        current={'run_id':'shared-run','node':'node-a','worker_id':'0','step':1,'step_duration_seconds':20,
                 'analysis_window':window(),'stage_durations_seconds':{'gen':15}}
        settings={'run_id':'shared-run','cluster':prefix,'node':'node-a','prometheus':{'url':endpoint,'query_step_seconds':1},
            'rollout_replicas':[{'id':node,'endpoint_node':node,'nodes':[node],'instance':node+':8000'} for node in ('node-a','node-b')]}
        def analyze():
            current['analysis_window']=window()
            return DiagnosticEngine(settings,prometheus=client).analyze(current,[])
        wait_for(lambda:client.query_range('vllm:num_requests_waiting',time.time()-10,time.time(),1) is not None)
        report=analyze()
        (output/'diagnosis.json').write_text(json.dumps(report,indent=2))
        assert report['clock_quality']['status']!='aligned' and not report['candidates']
        replicas={row['id']:row for row in report['rollout_replicas']}
        entity=replicas['node-b']['entities'][0]
        assert entity['signals']['vllm_requests_waiting']['current']==18
        assert 'vllm_preemptions_delta' not in entity['signals']
        assert any('vllm_preemptions_delta' in value for value in entity['missing_sources'])
        assert not query('histogram_quantile(0.95, sum by (le) (rate(vllm:request_queue_time_seconds_bucket[10s])))')
        checks['partial_replica_and_disabled_histogram']='no peer fill, fake zero or percentile'
        checks['unverified_multinode_clock']='raw observations retained; candidates withheld'
        fault=output/'node-b/fault.json'
        for mode in ({'missing':True},{'delay':True},{'error':True},{'sample_limit':True},{}):
            fault.write_text(json.dumps(mode))
            if mode.get('missing'):
                wait_for(lambda:len(query('vllm:num_requests_waiting'))==1)
                assert up('node-b','native')
            elif mode:
                wait_for(lambda:not up('node-b','native'))
            else: wait_for(lambda:up('node-b','native') and len(query('vllm:num_requests_waiting'))==2)
            assert up('node-a') and up('node-a','native')
        checks['native_partial_timeout_http_sample_limit_recovery']=True
        (output/'node-a/fault.json').write_text(json.dumps({'counter_reset':True}))
        wait_for(lambda:query('vllm:num_preemptions_total')[0]['value'][1]=='0')
        rates=wait_for(lambda:query('rate(vllm:num_preemptions_total[10s])'))
        assert all(float(row['value'][1])>=0 for row in rates)
        checks['native_counter_reset_no_negative_rate']=True
        execute(b,'python','-m','xlayer_telemetry.cli','--config',str(output/'bundle/nodes/node-b.toml'),'down','--role','node')
        wait_for(lambda:not up('node-b')); assert up('node-a')
        execute(b,'python','-m','xlayer_telemetry.cli','--config',str(output/'bundle/nodes/node-b.toml'),'up','--role','node')
        wait_for(lambda:up('node-b'))
        checks['node_stop_restart_preserves_peer']=True
        # Validate the existing fail-fast node-role policy, rather than silently
        # introducing a supervisor/restart policy for optional children.
        execute(a,'python','-c',"import os,pathlib,signal; pids=[int(p.name) for p in pathlib.Path('/proc').iterdir() "
            "if p.name.isdigit() and (p/'cmdline').is_file() and b'xlayer_telemetry.collectors.topology_textfile' "
            "in (p/'cmdline').read_bytes().split(bytes([0]))]; assert len(pids)==1; os.kill(pids[0],signal.SIGKILL)")
        wait_for(lambda:not up('node-a')); assert up('node-b') and up('node-a','native')
        checks['optional_collector_exit_policy']='node role stops siblings; peer node and external native source remain'
        result={'data_origin':'synthetic_sdk_and_vllm_exposition','checks':checks,
                'limitations':['Shared host kernel; no physical clock/GPU/vLLM/3FS validation.','Monitoring uses a test-only container listener; production bind/security policy unchanged.']}
        (output/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
        return result
    finally:
        for name in names:
            logs=subprocess.run(['docker','logs','--tail','80',name],capture_output=True,text=True,timeout=20)
            (output/(name.split(prefix+'-')[-1]+'.log')).write_text((logs.stdout+logs.stderr)[-16000:])
            subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=20)
        subprocess.run(['docker','network','rm',network],capture_output=True,timeout=20)


if __name__=='__main__':
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--tools',type=Path,required=True)
    parser.add_argument('--image',required=True,help='Build examples/multinode/cpu-contract.Dockerfile first')
    args=parser.parse_args()
    print(json.dumps(validate(args.output,args.tools,args.image),indent=2))
