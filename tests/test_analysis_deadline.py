"""Real subprocess/HTTP deadlines, recovery, persistence ownership and shutdown."""
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from xlayer_telemetry.analysis.deadline import IsolatedAnalyzer
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, load_config, run_once
from tests._process_helpers import child_processes, process_exists, signal_process


@pytest.fixture
def backend():
    state = {'slow': True, 'connections': 0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            state['connections'] += 1
            self.send_response(200)
            self.end_headers()
            try:
                if state['slow']:
                    # Socket timeout alone does not stop a trickling response.
                    for _ in range(150):
                        self.wfile.write(b' ')
                        self.wfile.flush()
                        time.sleep(.04)
                else:
                    query = parse_qs(urlsplit(self.path).query)
                    values = [[float(query.get('start',[0])[0]),'50'],
                              [float(query.get('end',[1])[0]),'50']]
                    self.wfile.write(json.dumps({'status':'success','data':{'resultType':'matrix',
                        'result':[{'metric':{'instance':'n'},'values':values}]}}).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield state, f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_deadline_interrupts_trickling_http_then_retries_same_step(tmp_path, backend):
    state, url = backend
    now = time.time()
    current = {'schema_version':1,'record_type':'verl_step_observation','record_id':'one',
               'run_id':'r','node':'n','worker_id':'driver','step':1,'observed_at':now,
               'step_duration_seconds':10,'boundary_scope':'rl_step',
               'analysis_window':{'start':now-10,'end':now,'accuracy':'approximate'}}
    history = tmp_path/'history.jsonl'
    history.write_text(json.dumps(current)+'\n')
    config = {'prometheus':{'url':url,'timeout_seconds':5},'retry_interval_seconds':.01,
              'retry_seconds':20,'clock':{'enabled':False}}
    output = tmp_path/'reports'
    engine = DiagnosticEngine(config)
    with IsolatedAnalyzer(config,seconds=1.5) as analyzer:
        started = time.monotonic()
        assert run_once(engine,history,output,periodic_when_idle=False,analyzer=analyzer) == 1
        assert time.monotonic()-started < 3.5  # Includes spawn, planning and cleanup.
        report = json.loads((output/'latest.json').read_text())
        assert report['missing_sources'] == ['analysis:deadline_exceeded']
        assert report['analysis_status'] == 'provisional'
        assert report['candidates'] == [] and report['evidence'] == {}
        assert not (output/'investigation').exists()
        assert analyzer._process is None
        state['slow'] = False
        assert run_once(engine,history,output,periodic_when_idle=False,analyzer=analyzer) == 1
        pid = analyzer._process.pid
        report = json.loads((output/'latest.json').read_text())
        assert report['analysis_execution']['status'] == 'completed'
        assert report['revision'] == 2 and report['analysis_status'] == 'final'
        assert (output/'investigation/one.jsonl').exists()
        assert run_once(engine,history,output,periodic_when_idle=False,analyzer=analyzer) == 0
        assert analyzer._process.pid == pid
    assert not any(process.pid == pid for process in multiprocessing.active_children())


@pytest.mark.skipif(not hasattr(os,'mkfifo'),reason='requires POSIX FIFO')
def test_deadline_also_bounds_blocked_history_reads(tmp_path):
    fifo = tmp_path/'blocked.jsonl'
    os.mkfifo(fifo)
    config = {'prometheus':{'url':'http://127.0.0.1:1'}}
    with IsolatedAnalyzer(config,seconds=.8) as analyzer:
        started = time.monotonic()
        report = analyzer.prepare(fifo,tmp_path/'output',periodic_when_idle=False)
        assert time.monotonic()-started < 2
        assert report['verdict'] == 'insufficient_data'
        assert report['missing_sources'] == ['analysis:deadline_exceeded']
        assert not (tmp_path/'output').exists()  # Worker has no report-writing path.
        assert analyzer._process is None


def test_cli_sigterm_stops_its_analysis_worker(tmp_path):
    fifo = tmp_path/'blocked.jsonl'
    os.mkfifo(fifo)
    config = tmp_path/'config.json'
    config.write_text(json.dumps({'schema_version':1,'prometheus':{'url':'http://127.0.0.1:1'},
                                  'analysis_deadline_seconds':30}))
    process = subprocess.Popen([sys.executable,'-m','xlayer_telemetry.analysis.diagnostics',
        '--config',str(config),'--history',str(fifo),'--output',str(tmp_path/'output')],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    children = []
    try:
        end = time.monotonic()+5
        while time.monotonic()<end:
            children = child_processes(process.pid)
            # Spawn uses a resource tracker and the analysis worker.
            if len(children) >= 2 and any(
                Path(f'/proc/{child.pid}/wchan').read_text() == 'wait_for_partner'
                for child in children
            ):
                break
            time.sleep(.02)
        assert len(children) >= 2
        assert any(Path(f'/proc/{child.pid}/wchan').read_text() == 'wait_for_partner'
                   for child in children), 'actual analyzer did not reach the injected FIFO read'
        process.send_signal(signal.SIGTERM)
        _, stderr = process.communicate(timeout=3)
        assert process.returncode == 143, stderr.decode()
        end = time.monotonic()+2
        while time.monotonic()<end and any(process_exists(child) for child in children):
            time.sleep(.02)
        assert all(not process_exists(child) for child in children)
        assert unrelated.poll() is None
    finally:
        for child in children:
            signal_process(child, signal.SIGKILL)
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=3)
        unrelated.terminate()
        unrelated.wait(timeout=3)


@pytest.mark.parametrize('value',[-1,float('nan'),True])
def test_deadline_config_rejects_invalid_values(tmp_path,value):
    path = tmp_path/'config.json'
    path.write_text(json.dumps({'schema_version':1,'prometheus':{'url':'http://unused'},
                               'analysis_deadline_seconds':value}))
    with pytest.raises(ValueError,match='analysis_deadline_seconds'):
        load_config(path)
