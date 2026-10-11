import json
from xlayer_telemetry.time_alignment import CalibrationCache
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine,run_once,_prepare_batch
from tests.test_time_alignment import calibration


class Empty:
    def query_range_detail(self,*args):return {'aggregate':None,'series':[]}


def setup(tmp_path,settle=0):
    raw=[220.];mono=[50.]
    path=tmp_path/'clock.json'
    payload=calibration();payload.update(local_anchor=220,monotonic_anchor=50,offset_seconds=-120,valid_from=200,valid_until=220.5)
    path.write_text(json.dumps(payload))
    cache=CalibrationCache(path,node='gpu-a',wall_clock=lambda:raw[0],monotonic=lambda:mono[0],boot_id=lambda:'boot')
    window=cache.project(218,220)
    record={'schema_version':1,'record_type':'verl_step_observation','record_id':'id','run_id':'r','node':'gpu-a',
        'worker_id':'driver','boundary_scope':'rl_step','observed_at':220,'step_duration_seconds':2,'analysis_window':window}
    history=tmp_path/'history.jsonl';history.write_text(json.dumps(record)+'\n')
    engine=DiagnosticEngine({'run_id':'r','node':'gpu-a','prometheus':{'url':'unused'},'retry_seconds':60,'retry_interval_seconds':10,
        'threefs':{'settle_seconds':settle,'url':'http://unused'} if settle else {}},prometheus=Empty(),threefs=object() if settle else None,
        clock=lambda:raw[0],elapsed_clock=lambda:mono[0],time_calibration=cache)
    return engine,history,tmp_path/'diagnostics',raw,mono,path,payload


def renew(path,payload,raw,mono):
    payload.update(local_anchor=raw[0],monotonic_anchor=mono[0],valid_from=raw[0]-1,valid_until=raw[0]+60)
    path.write_text(json.dumps(payload))


def test_retry_does_not_expire_on_calibration_expiry_or_recovery(tmp_path):
    engine,history,output,raw,mono,path,payload=setup(tmp_path)
    assert engine.clock()==100
    assert run_once(engine,history,output,periodic_when_idle=False)==1
    raw[0]=221;mono[0]=51
    assert engine.clock()==221
    assert run_once(engine,history,output,periodic_when_idle=False)==0
    raw[0]=230;mono[0]=60
    assert run_once(engine,history,output,periodic_when_idle=False)==1
    assert json.loads((output/'latest.json').read_text())['analysis_status']=='provisional'
    raw[0]=231;mono[0]=61;renew(path,payload,raw,mono)
    assert engine.clock()==111
    assert run_once(engine,history,output,periodic_when_idle=False)==0
    raw[0]=280;mono[0]=110;renew(path,payload,raw,mono)
    assert run_once(engine,history,output,periodic_when_idle=False)==1
    assert json.loads((output/'latest.json').read_text())['analysis_status']=='final'


def test_settling_is_elapsed_time_across_expiry_and_recovery(tmp_path):
    engine,history,output,raw,mono,path,payload=setup(tmp_path,settle=30)
    assert not _prepare_batch(engine,history,output,periodic_when_idle=False)[0]['pending']
    raw[0]=221;mono[0]=51
    assert not _prepare_batch(engine,history,output,periodic_when_idle=False)[0]['pending']
    raw[0]=235;mono[0]=65;renew(path,payload,raw,mono)
    assert not _prepare_batch(engine,history,output,periodic_when_idle=False)[0]['pending']
    raw[0]=250;mono[0]=80
    assert len(_prepare_batch(engine,history,output,periodic_when_idle=False)[0]['pending'])==1


import pytest


@pytest.mark.parametrize('mapping', ['expired', 'missing', 'different-session', 'valid'])
def test_fresh_finalizer_reuses_elapsed_wait_without_claiming_clock_alignment(tmp_path, mapping):
    from tests.test_diagnostics import FakeThreeFS
    engine, history, output, raw, mono, path, payload = setup(tmp_path, settle=30)
    engine.threefs = FakeThreeFS([], [])
    # Establish a real provisional result after the first process settled.
    assert run_once(engine, history, output, periodic_when_idle=False) == 0
    raw[0] = 250; mono[0] = 80
    renew(path, payload, raw, mono)
    assert run_once(engine, history, output, periodic_when_idle=False) == 1
    previous = json.loads((output / 'latest.json').read_text())
    assert previous['analysis_status'] == 'provisional'
    raw[0] = 281; mono[0] = 111  # The wrapper has waited another settle interval.
    if mapping == 'expired':
        payload['valid_until'] = 280
    elif mapping == 'different-session':
        payload['reference_session'] = 'new-session'
    path.write_text(json.dumps(payload))
    fresh = DiagnosticEngine(engine.config, prometheus=Empty(), threefs=FakeThreeFS([], []),
        clock=lambda: raw[0], elapsed_clock=lambda: mono[0],
        time_calibration=None if mapping == 'missing' else CalibrationCache(path,
            node='gpu-a', wall_clock=lambda: raw[0], monotonic=lambda: mono[0], boot_id=lambda: 'boot'))
    # Production engines use the shared boot-specific monotonic identity.
    fresh.elapsed_clock_id = engine.elapsed_clock_id
    assert run_once(fresh, history, output, periodic_when_idle=False, finalize_pending=True) == 1
    final = json.loads((output / 'latest.json').read_text())
    assert final['revision'] == previous['revision'] + 1
    assert final['analysis_status'] == 'final'
    assert final['missing_sources'], 'Final is the last attempt, not proof of complete telemetry'
    rows = [json.loads(line) for line in (output / 'investigation/id.jsonl').read_text().splitlines()]
    assert rows and all(row['generated_at'] == final['generated_at'] and row['record_id'] == 'id' for row in rows)


def test_finalizer_does_not_reuse_elapsed_proof_from_another_boot(tmp_path, monkeypatch):
    engine, history, output, raw, mono, path, payload = setup(tmp_path, settle=30)
    from tests.test_diagnostics import FakeThreeFS
    engine.threefs = FakeThreeFS([], [])
    assert run_once(engine, history, output, periodic_when_idle=False) == 0
    raw[0] = 250; mono[0] = 80
    assert run_once(engine, history, output, periodic_when_idle=False) == 1
    engine._settle_deadlines.clear()
    engine.elapsed_clock_id = 'another-boot'
    raw[0] = 281; mono[0] = 111
    engine.threefs = FakeThreeFS([], [])
    waits = []
    def wait(seconds):
        waits.append(seconds)
        mono[0] += seconds
    monkeypatch.setattr('xlayer_telemetry.analysis.diagnostics.time.sleep', wait)
    assert run_once(engine, history, output, periodic_when_idle=False, finalize_pending=True) == 1
    assert waits == [30], 'Another boot must start a fresh bounded elapsed wait'


@pytest.mark.parametrize('mapping', ['expired', 'missing', 'different-session', 'valid'])
def test_new_once_cli_writes_final_revision_and_projection(tmp_path, mapping):
    import os
    import subprocess
    import sys
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from xlayer_telemetry.step_history import StepHistoryWriter
    from xlayer_telemetry.time_alignment import boot_id
    calls = []
    class Backend(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append('prometheus')
            body = b'{"status":"success","data":{"resultType":"matrix","result":[]}}'
            self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            calls.append('threefs')
            self.send_response(200); self.send_header('Content-Length', '0'); self.end_headers()
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1',0), Backend)
    thread = threading.Thread(target=server.serve_forever); thread.start()
    try:
        now = time.time(); mono = time.monotonic()
        payload = calibration(reference_session='session')
        payload.update(boot_id=boot_id(), local_anchor=now, monotonic_anchor=mono,
                       valid_from=now-20, valid_until=now+60, offset_seconds=0)
        path = tmp_path/'clock.json'; path.write_text(json.dumps(payload))
        cache = CalibrationCache(path,node='gpu-a')
        history = tmp_path/'steps.jsonl'
        row = StepHistoryWriter(history, run_id='r', node='gpu-a', worker_id='driver',
            clock=lambda: now-2, time_calibration=cache).append({'step':1,'data':{'timing_s/step':1}})
        assert row['analysis_window']['time_alignment']['status'] == 'aligned'
        url = f'http://127.0.0.1:{server.server_port}'
        config = {'schema_version':1,'run_id':'r','node':'gpu-a', 'analysis_deadline_seconds':5,
                  'prometheus':{'url':url}, 'threefs':{'url':url,'settle_seconds':.1},
                  'retry_seconds':60,'retry_interval_seconds':30}
        cfg = tmp_path/'config.json'; cfg.write_text(json.dumps(config))
        output = tmp_path/'diagnostics'
        env = os.environ | {'TELEMETRY_TIME_CALIBRATION_FILE':str(path)}
        argv = [sys.executable,'-m','xlayer_telemetry.analysis.diagnostics','--config',str(cfg),
                '--history',str(history),'--output',str(output),'--once','--pending-only']
        first = subprocess.run(argv, env=env, capture_output=True,text=True,timeout=10)
        assert first.returncode == 0, first.stderr
        previous = json.loads((output/'latest.json').read_text())
        assert previous['analysis_status'] == 'provisional'
        if mapping == 'expired': payload['valid_until'] = time.time()-.01
        if mapping == 'different-session': payload['reference_session'] = 'new-session'
        if mapping == 'missing': path.unlink()
        else: path.write_text(json.dumps(payload))
        calls.clear()
        finalizer = subprocess.run([*argv,'--finalize-pending'],env=env,capture_output=True,text=True,timeout=10)
        assert finalizer.returncode == 0, finalizer.stderr
        final = json.loads((output/'latest.json').read_text())
        assert final['revision'] == previous['revision']+1
        assert final['analysis_status'] == 'final' and final['missing_sources']
        assert 'prometheus' in calls and 'threefs' in calls
        projection = output/'investigation'/f"{row['record_id']}.jsonl"
        assert projection.is_file()
        assert all(json.loads(line)['generated_at']==final['generated_at'] for line in projection.read_text().splitlines())
    finally:
        server.shutdown();server.server_close();thread.join(2)
