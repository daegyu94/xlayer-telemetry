"""Independent processes share SDK directories and analyze concurrently on CPU."""
import json
import multiprocessing
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.fileio import json_objects
from xlayer_telemetry.metrics import Metric, MetricEmitter
from xlayer_telemetry.metrics.textfile import _iter_snapshots, build_metrics
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, write_report


def _producer(root, run, ready, release, start=0, partial=False):
    root=Path(root)
    emitter=MetricEmitter(root/'snapshots',run_id=run,node='node',producer='verl',role='trainer',worker_id='driver')
    recorder=EventRecorder(root/'events',CorrelationContext(run_id=run,node='node',producer='verl',role='trainer',worker_id='driver'))
    emitter.emit(step=start,samples=[Metric('training_loss',start)])
    recorder.event('step.sample',phase='training',step=start)
    if partial:
        with recorder.path.open('ab') as stream:
            stream.write(b'{"interrupted":'); stream.flush()
    ready.set()
    if not release.wait(10): raise TimeoutError('producer release')
    for step in range(start+1,start+10):
        emitter.emit(step=step,samples=[Metric('training_loss',step)])
        recorder.event('step.sample',phase='training',step=step)


def _analyzer(root, run, url, start):
    before={'record_id':run+'-before','run_id':run,'node':'node','worker_id':'driver','step':127,
        'observed_at':start-10,'step_duration_seconds':2,'analysis_window':{'start':start-12,'end':start-10,'accuracy':'approximate'}}
    current={**before,'record_id':run+'-now','step':128,'observed_at':start+4,
        'analysis_window':{'start':start,'end':start+4,'accuracy':'approximate'}}
    report=DiagnosticEngine({'prometheus':{'url':url},'sampling':{'check_source_freshness':True}}).analyze(current,[before])
    write_report(Path(root)/run/'diagnostics',report)


def test_concurrent_sdk_producers_survive_one_partial_writer_restart(tmp_path):
    ctx=multiprocessing.get_context('spawn')
    ready=[ctx.Event() for _ in range(3)]; releases=[ctx.Event() for _ in range(3)]
    runs=['job-a','job-b','job-c']
    processes=[ctx.Process(target=_producer,args=(str(tmp_path),run,ready[i],releases[i],0,i==1)) for i,run in enumerate(runs)]
    try:
        for process in processes: process.start()
        assert all(event.wait(10) for event in ready)
        assert all(process.is_alive() for process in processes)
        samples=build_metrics(_iter_snapshots(tmp_path/'snapshots'))
        assert {s.labels['run_id'] for s in samples if s.name=='training_loss'} == set(runs)
        releases[0].set(); releases[2].set()
        processes[1].terminate(); processes[1].join(5)
        # A killed waiter can leave its multiprocessing Condition unrecoverable;
        # restart with new coordination objects, not the interrupted Event.
        restart_ready, restart_release = ctx.Event(), ctx.Event()
        restart_release.set()
        restart=ctx.Process(target=_producer,args=(str(tmp_path),'job-b',restart_ready,restart_release,10,False))
        restart.start(); processes.append(restart)
        for process in (processes[0],processes[2],restart):
            process.join(10); assert process.exitcode==0
        samples=build_metrics(_iter_snapshots(tmp_path/'snapshots'))
        assert {s.labels['run_id']:s.value for s in samples if s.name=='training_loss'} == {'job-a':9,'job-b':19,'job-c':9}
        records=[r for path in (tmp_path/'events').glob('*.jsonl') for r in json_objects(path)]
        assert {run:sum(r['run_id']==run for r in records) for run in runs} == {'job-a':10,'job-b':11,'job-c':10}
    finally:
        for process in processes:
            if process.pid is None: continue
            if process.is_alive(): process.terminate()
            process.join(5)


def test_three_analyzers_overlap_http_queries_and_keep_run_baselines(tmp_path):
    first=threading.Barrier(3); seen=set(); lock=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            args=parse_qs(urlparse(self.path).query); start=float(args['start'][0]); end=float(args['end'][0]); query=args['query'][0]
            with lock:
                initial=start in {1000,1100,1200} and start not in seen
                if initial: seen.add(start)
            if initial: first.wait(timeout=10)
            result=[]
            if query.startswith('timestamp('): values=[[start+1,str(start+1)],[end-1,str(end-1)]]
            elif 'num_requests_waiting' in query: values=[[start+1,'12'],[end-1,'12']]
            elif 'kv_cache_usage' in query: values=[[start+1,'.99'],[end-1,'.99']]
            elif 'num_preemptions' in query: values=[[start+1,'0'],[end-1,'3']]
            else: values=[]
            if values: result=[{'metric':{'node':'node','instance':'shared-engine'},'values':values}]
            body=json.dumps({'status':'success','data':{'resultType':'matrix','result':result}}).encode()
            self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    ctx=multiprocessing.get_context('spawn')
    processes=[ctx.Process(target=_analyzer,args=(str(tmp_path),'job-'+str(i),f'http://127.0.0.1:{server.server_port}',1000+100*i)) for i in range(3)]
    try:
        for process in processes: process.start()
        for process in processes: process.join(20); assert process.exitcode==0
        assert seen == {1000,1100,1200}
        for i in range(3):
            run='job-'+str(i); report=json.loads((tmp_path/run/'diagnostics/latest.json').read_text())
            assert report['run_id']==run and report['comparison']['baseline_record_id']==run+'-before'
            candidate=next(c for c in report['candidates'] if c['id']=='kv_cache_pressure')
            assert candidate['run_relation']=='shared_unverified' and candidate['resource_attribution']=='not_established'
    finally:
        for process in processes:
            if process.is_alive(): process.terminate()
            process.join(5)
        server.shutdown(); server.server_close(); thread.join(5)


def test_raw_loggers_through_three_wrappers_survive_one_writer_restart_and_backend_failure(tmp_path):
    """Model names are metadata; CPU processes emit scalars, not model weights."""
    import os
    import signal
    import subprocess
    import sys
    from xlayer_telemetry.operations import runs as saved_runs

    root = Path(__file__).parents[1]
    workload = tmp_path / 'fake_workload.py'
    workload.write_text('''import json, os, subprocess, sys, time
from pathlib import Path
root=Path(sys.argv[1]); mode=sys.argv[2]; fault=os.environ['WRITER_FAULT']=='1'
logger=Path(os.environ['VERL_FILE_LOGGER_PATH'])
def record(step):
 return {'step':step,'data':{'perf/time_per_step':.02*step*(2 if fault else 1),
         'timing_s/gen':.01*step,'perf/total_num_tokens':128,'policy_version':7}}
def append(step):
 with logger.open('ab') as stream:
  stream.write((json.dumps(record(step))+'\\n').encode());stream.flush();os.fsync(stream.fileno())
if mode=='supervisor':
 child=subprocess.Popen([sys.executable,__file__,str(root),'writer'])
 (root/'writer.pid').write_text(str(child.pid)); code=child.wait()
 if fault and code!=0:
  code=subprocess.call([sys.executable,__file__,str(root),'restart'])
 sys.exit(code)
if mode=='writer':
 append(1)
 if fault:
  with logger.open('ab') as stream:stream.write(b'{"step":2,"data":');stream.flush()
 (root/'ready').touch()
 while not (root/'release').exists():time.sleep(.02)
 if fault:time.sleep(60)
else:
 # Repair only this producer's incomplete raw tail, then replay its last
 # complete record. The actual bridge must deduplicate that input.
 data=logger.read_bytes();logger.write_bytes(data[:data.rfind(b'\\n')+1]);append(1)
for step in (2,3):
 time.sleep(.4);append(step)
''')
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path.split('/api/',1)[0])
            body=json.dumps({'status':'success','data':{'resultType':'matrix','result':[]}}).encode()
            self.send_response(503 if self.path.startswith('/job-b/') else 200)
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    backend=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=backend.serve_forever,daemon=True);thread.start()
    processes=[]; logs=[]
    artifact_root=tmp_path/'runs'; controls=tmp_path/'controls'
    def wait_for(predicate):
        deadline=time.monotonic()+15
        while not predicate():
            if any(process.poll() not in (None,0) for process in processes):
                raise AssertionError('Wrapper failure: ' + '\n'.join(path.read_text()[-2000:] for path in controls.glob('*/wrapper.log')))
            if time.monotonic()>=deadline:raise AssertionError('Timed out waiting for an artifact condition')
            threading.Event().wait(.03)
    try:
        for index, run in enumerate(('job-a','job-b','job-c')):
            control=controls/run;control.mkdir(parents=True)
            config=tmp_path/(run+'.json')
            config.write_text(json.dumps({'schema_version':1,'cluster':'cpu-fixture','node':'shared-node',
                'clock':{'enabled':False}, 'baseline':{'match_fields':['perf/total_num_tokens']},
                'prometheus':{'url':f'http://127.0.0.1:{backend.server_port}/{run}'}}))
            log=(control/'wrapper.log').open('w');logs.append(log)
            argv=['bash',str(root/'scripts/run_verl_with_telemetry.sh'),'--output',str(artifact_root/run),
                '--run-id',run,'--node','shared-node','--execution-mode','async' if index==2 else 'sync',
                '--diagnostics-config',str(config),'--diagnostics-interval','0.1']
            for field in ('cluster=cpu-fixture','observer_node=shared-node','model_identifier=CPU metadata only',
                          'data_origin=synthetic','workload_fingerprint=raw-scalars-v1'):
                argv+=['--set',field]
            argv+=['--',sys.executable,str(workload),str(control),'supervisor']
            processes.append(subprocess.Popen(argv,cwd=root,start_new_session=True,stdout=log,stderr=log,
                env=os.environ|{'TELEMETRY_PYTHON':sys.executable,'WRITER_FAULT':str(int(index==1))}))
        wait_for(lambda:all((controls/run/'ready').exists() for run in ('job-a','job-b','job-c')))
        wait_for(lambda:len(saved_runs.catalog(artifact_root)['runs'])==3 and
                 all(row['steps']==1 for row in saved_runs.catalog(artifact_root)['runs']))
        assert all(process.poll() is None for process in processes)
        os.kill(int((controls/'job-b/writer.pid').read_text()),signal.SIGTERM)
        for run in ('job-a','job-c'):(controls/run/'release').touch()
        for process in processes:
            process.wait(timeout=20)
            assert process.returncode==0
        result=saved_runs.catalog(artifact_root)
        assert len(result['runs'])==3
        ids=set()
        for row in result['runs']:
            assert row['steps']==3 and row['data_origin']=='synthetic'
            assert row['status']=='reported_completed' and row['workload_exit_code']==0
            run=row['run_id'];directory=artifact_root/run
            history=[json.loads(line) for line in (directory/'telemetry-events/verl-steps.jsonl').read_text().splitlines()]
            own={entry['record_id'] for entry in history}
            assert len(own)==3 and not ids.intersection(own);ids.update(own)
            assert {entry['run_id'] for entry in history}=={run}
            assert {entry['boundary_scope'] for entry in history}==({'trainer_update'} if run=='job-c' else {'rl_step'})
            snapshot=json.loads(next((directory/'telemetry-metrics').glob('verl-trainer-driver*.json')).read_text())
            assert snapshot['run_id']==run and snapshot['step']==3
            report=json.loads((directory/'diagnostics/latest.json').read_text())
            assert report['run_id']==run
            assert report['step']==3
            assert report['comparison']['baseline_record_id'] in {entry['record_id'] for entry in history if entry['step']<3}
            assert all(candidate.get('resource_attribution')!='established' for candidate in report['candidates'])
            if run=='job-b':assert any(item.startswith('prometheus:') and 'Error' in item for item in report['missing_sources'])
        assert set(seen)=={'/job-a','/job-b','/job-c'}
    finally:
        for process in processes:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
        for log in logs:log.close()
        backend.shutdown();backend.server_close();thread.join(timeout=5)
