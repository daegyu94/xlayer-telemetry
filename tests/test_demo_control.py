import pytest
from xlayer_telemetry.demos.control import ScenarioControl


def test_reset_revokes_backend_and_observations_before_next_process_starts():
    control=ScenarioControl()
    first=control.command('replay','normal',0)
    assert first['generation']==1 and first['status']=='starting'
    control.publish(1,backend={'prometheus':'http://127.0.0.1:1','loki':'http://127.0.0.1:2'},
        catalog={'observations':[{'scenario':'normal','context':{'run_id':'g1-r'}}]},state='ready')
    assert control.snapshot()['observations'][0]['context']['demo_generation']=='1'
    control.command('reset',None,1)
    assert control.snapshot()['observations']==[]
    with pytest.raises(RuntimeError):control.target('prometheus')
    assert not control.publish(1,catalog={'observations':[{'old':'record'}]},state='ready')
    assert control.snapshot()['status']=='resetting'


def test_commands_are_allowlisted_and_optimistic_generation_prevents_double_replay():
    control=ScenarioControl()
    with pytest.raises(ValueError):control.command('replay','shell-command',0)
    assert control.generation==0
    control.command('replay','slow-rollout',0)
    with pytest.raises(RuntimeError):control.command('replay','normal',0)
    assert control.selected=='slow-rollout'


def test_stop_preserves_summary_while_reset_clears_it():
    control=ScenarioControl();control.command('start','normal',0)
    control.publish(1,catalog={'observations':[{'scenario':'normal','context':{'run_id':'g1-r'}}]},validation={'query':{'state':'passed'}},state='ready')
    control.command('stop',None,1)
    assert control.state=='stopping' and control.catalog
    assert control.validation['query']['state']=='passed' and control.backend is None
    control.command('reset',None,2)
    assert not control.catalog and not control.validation


def test_proxy_discards_a_response_that_completed_after_reset():
    import json
    import threading
    import urllib.request
    import urllib.error
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from concurrent.futures import ThreadPoolExecutor
    from xlayer_telemetry.demos.control import control_server
    entered=threading.Event();release=threading.Event()
    class Backend(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            entered.set();release.wait(5);self.send_response(200);self.end_headers();self.wfile.write(b'{"old_value":42}')
    backend=ThreadingHTTPServer(('127.0.0.1',0),Backend)
    control=ScenarioControl();control.command('start','normal',0)
    control.publish(1,backend={'prometheus':f'http://127.0.0.1:{backend.server_port}'},state='ready')
    proxy=control_server(control)
    threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (backend,proxy)]
    for thread in threads:thread.start()
    def fetch():
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{proxy.server_port}/prom/api/v1/query',timeout=5) as response:return response.status,response.read()
        except urllib.error.HTTPError as response:return response.code,response.read()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(fetch);assert entered.wait(3)
            control.command('reset',None,1);release.set()
            status,body=future.result(timeout=5)
            assert status==503 and b'old_value' not in body
            assert 'Replay changed' in json.loads(body)['message']
    finally:
        release.set()
        for server in (backend,proxy):server.shutdown();server.server_close()
        for thread in threads:thread.join(timeout=2)


def test_failed_stage_is_preserved_and_failed_backend_is_revoked():
    control=ScenarioControl();control.command('start','normal',0)
    control.publish(1,backend={'prometheus':'http://127.0.0.1:1'},state='collecting')
    control.publish(1,state='error',validation={'query':{'state':'failed','checks':[],'message':'Expected signal absent'}})
    assert control.snapshot()['validation']['query']['state']=='failed'
    with pytest.raises(RuntimeError):control.target('prometheus')


@pytest.mark.parametrize("exhaust_retries", [False, True])
def test_coordinator_survives_partial_connection_and_replay_isolation(tmp_path,monkeypatch,exhaust_retries):
    import importlib,sys,json,threading,time
    from pathlib import Path
    from argparse import Namespace
    from xlayer_telemetry.fileio import atomic_write_text
    scripts=Path(__file__).parents[1]/'grafana/xlayer-app/scripts'
    monkeypatch.syspath_prepend(str(scripts))
    lab=importlib.import_module('scenario_lab')
    control=ScenarioControl();stopping=threading.Event();created=threading.Event();read=threading.Event()
    original=Path.read_text
    def inspect_read(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        if path.name=='connection.json':read.set()
        return result
    monkeypatch.setattr(Path,'read_text',inspect_read)
    class Child:
        def __init__(self,command,**kwargs):
            self.state=Path(command[command.index('--output')+1]);self.state.mkdir(parents=True)
            (self.state/'connection.json').write_text('{"prometheus":')
            self.stopped=False;created.set()
        def poll(self):return 0 if self.stopped else None
    monkeypatch.setattr(lab.subprocess,'Popen',Child)
    monkeypatch.setattr(lab,'stop_owned',lambda child:setattr(child,'stopped',True))
    failures=[]
    def run():
        try:lab.coordinate(control,Namespace(output=tmp_path,tools=tmp_path),stopping)
        except Exception as error:failures.append(type(error).__name__)
    thread=threading.Thread(target=run);thread.start()
    try:
        for generation in (1,2):
            created.clear();read.clear()
            control.command('replay','normal',generation-1)
            assert created.wait(2) and read.wait(2)
            thread.join(.1)
            assert thread.is_alive() and not failures,failures
            with pytest.raises(RuntimeError):control.target('prometheus')
            if exhaust_retries and generation==1:
                deadline=time.monotonic()+4
                while time.monotonic()<deadline and control.snapshot()['status']!='error':
                    stopping.wait(.02)
                assert control.snapshot()['status']=='error'
                assert thread.is_alive() and not failures
                with pytest.raises(RuntimeError):control.target('prometheus')
                continue  # Only a fresh Replay may replace the failed generation.
            endpoint=f'http://127.0.0.1:{1000+generation}'
            atomic_write_text(tmp_path/'replays'/f'g{generation}'/'connection.json',json.dumps({'prometheus':endpoint,'loki':endpoint}))
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                with control.lock:
                    if control.backend:break
                stopping.wait(.01)
            assert control.target('prometheus')==(generation,endpoint)
    finally:
        stopping.set();control.changed.set();thread.join(3)
    assert not thread.is_alive()
