"""Public CLI and executable documentation seams reported in release review."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from tests.test_cli import invoke

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize('logger', ["['console','file']", '["console","file"]', '[console,file]', "[ 'console', 'file' ]"])
@pytest.mark.parametrize('exit_code', [0, 7])
def test_public_run_keeps_valid_logger_argv_and_exit_status(tmp_path, logger, exit_code):
    config = tmp_path/'config'
    config.write_text(f'TELEMETRY_HOME={str(tmp_path / "state")!r}\nRUN_ROOT={str(tmp_path / "run")!r}\n')
    captured = tmp_path/'argv.json'
    workload = tmp_path/'workload.py'
    workload.write_text('import json,sys\nfrom pathlib import Path\n'+
        f'Path({str(captured)!r}).write_text(json.dumps(sys.argv[1:]))\nraise SystemExit({exit_code})\n')
    args = ['-m', 'verl.trainer.main_ppo', 'trainer.logger='+logger,
            'trainer.project_name=unchanged', 'trainer.experiment_name=unchanged', 'argument with spaces']
    result = invoke(config,'run','--output',str(tmp_path/'run'),'--',sys.executable,str(workload),*args)
    assert result.returncode == exit_code, result.stderr
    assert json.loads(captured.read_text()) == args
    health = json.loads((tmp_path/'run/telemetry-health.json').read_text())
    assert health['workload']['exit_code'] == exit_code


@pytest.mark.parametrize('address,status,exit_code', [('10.0.0.20','matched',0),('10.0.0.10','mismatch',1)])
def test_public_live_validation_checks_actual_target_address(tmp_path, address, status, exit_code):
    class Prometheus(BaseHTTPRequestHandler):
        def do_GET(self):
            data = {'status':'success','data':{'activeTargets':[{
                'labels':{'job':'telemetry','cluster':'poc','nodename':'gpu-a'}, 'health':'up',
                'scrapeUrl':f'http://{address}:19100/metrics'}]}}
            body = json.dumps(data if self.path.startswith('/api/v1/targets') else {'database':'ok'}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Prometheus)
    thread=threading.Thread(target=server.serve_forever);thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_port}'
        config=tmp_path/'config'
        config.write_text(f'TELEMETRY_HOME={str(tmp_path)!r}\nCLUSTER_NAME=poc\nNODE_NAME=gpu-a\nNODE_ADDR=10.0.0.20\nTELEMETRY_TARGETS=gpu-a=10.0.0.20\nPROMETHEUS_URL={url}\nGRAFANA_URL={url}\nENABLE_LOGS=0\n')
        result=invoke(config,'cluster','validate','--live','--json')
        report=json.loads(result.stdout)
        assert report['observations']['collector_targets'][0]['configuration_status']==status
        assert result.returncode==exit_code, report
        assert report['status']==('valid' if status=='matched' else 'needs_attention')
        if status=='mismatch':
            assert any(i['code']=='collector_configuration_mismatch' and i['severity']=='warning' for i in report['issues'])
    finally:
        server.shutdown();server.server_close();thread.join(2)


def test_rendered_topology_publisher_validates_the_declared_inventory(tmp_path):
    source=tmp_path/'inventory.json'
    source.write_text(json.dumps({'schema_version':1,'cluster':{'name':'poc','monitoring_node':'monitoring-0'},
        'nodes':[{'name':'monitoring-0','address':'127.0.0.1','roles':['monitoring']},
                 {'name':'gpu-a','address':'10.0.0.20','roles':['rollout'],'gpus':[0]}]}))
    bundle=tmp_path/'bundle'
    generated=invoke(tmp_path/'unused','cluster','render','--inventory',str(source),'--output',str(bundle),'--json')
    assert generated.returncode==0,generated.stderr
    # Follow the documented publisher recipe, with only a host-local topology path.
    publisher=bundle/'nodes/monitoring-0.toml'
    with publisher.open('a') as file:
        file.write('\nTOPOLOGY_DIR = '+json.dumps(str(bundle/'topology'))+'\nTELEMETRY_HOME = '+json.dumps(str(tmp_path/'state'))+'\n')
    result=invoke(publisher,'config','validate')
    assert result.returncode==0,result.stderr
    doctor=invoke(publisher,'doctor','--role','node','--json')
    assert json.loads(doctor.stdout)['cluster_configuration']['status']=='valid'
    path=bundle/'topology/compute-topology.json'
    document=json.loads(path.read_text())
    document['components'].append({'id':'unregistered-device','role':'gpu','resource_node':'ghost','gpu':'0'})
    path.write_text(json.dumps(document))
    invalid=invoke(publisher,'cluster','validate','--json')
    assert invalid.returncode==2,invalid.stdout
    assert 'resource_node_unregistered' in {r['code'] for r in json.loads(invalid.stdout)['issues']}
    assert invoke(publisher,'config','validate').returncode==2


@pytest.mark.parametrize('enabled', [True, False])
def test_documented_policy_fragment_records_worker_node(tmp_path, monkeypatch, enabled):
    section=(ROOT/'docs/ui-telemetry-coverage.md').read_text().split('## Explicit Policy Version in Events')[1].split('\n## ')[0]
    fragment=re.search(r'```python\n(.*?)```',section,re.S).group(1)
    preparation=re.search(r'```bash\n(.*?)```',section,re.S).group(1)
    node=subprocess.check_output(['bash','-c',preparation+'\nprintf "%s" "$TELEMETRY_NODE"'],text=True)
    assert node=='rollout-node-0'
    monkeypatch.setenv('TELEMETRY_NODE',node)
    monkeypatch.setenv('TELEMETRY_ASYNC_IO','0')
    for key,value in [('TELEMETRY_EVENTS_DIR',str(tmp_path)),('TELEMETRY_RUN_ID','policy-demo')]:
        if enabled: monkeypatch.setenv(key,value)
        else: monkeypatch.delenv(key,raising=False)
    calls=[]
    namespace={'applied_version':12,'training_step':4,'batch':['prompt'],
               'generate_sequences':lambda batch:calls.append(batch) or 'answer'}
    exec(compile(fragment,'docs/ui-telemetry-coverage.md','exec'),namespace)
    if enabled:
        namespace['events'].close()
        rows=[json.loads(line) for path in tmp_path.glob('*.jsonl') for line in path.read_text().splitlines()]
        assert {r['name'] for r in rows}=={'weights.applied','rollout.generate'}
        assert all(r['node']=='rollout-node-0' and r['worker_id']=='rollout-0' and r['policy_version']==12 for r in rows)
        assert calls==[['prompt']]
    else:
        assert namespace['events'] is None
    assert calls==[['prompt']] and namespace['result']=='answer', 'Optional telemetry must not suppress rollout execution'


@pytest.mark.parametrize('logger', ["['console','profile']", '[console,myfile]', '["console","file-backed"]'])
def test_public_run_still_rejects_logger_without_exact_file(tmp_path, logger):
    config=tmp_path/'config';config.write_text(f'TELEMETRY_HOME={str(tmp_path)!r}\n')
    marker=tmp_path/'launched'
    workload=tmp_path/'workload.py';workload.write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
    result=invoke(config,'run','--',sys.executable,str(workload),'-m','verl.trainer.main_ppo','trainer.logger='+logger)
    assert result.returncode==2 and 'must include file' in result.stderr
    assert not marker.exists()
