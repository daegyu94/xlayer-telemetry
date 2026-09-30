"""Validate run discovery and source timestamps against a real local Prometheus.

Uses two logical nodes on one physical host and SDK fixture values. This is not
VERL training, a storage bottleneck experiment or physical multi-node validation.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, PrometheusClient, write_report
from xlayer_telemetry.metrics import Metric, MetricEmitter
from xlayer_telemetry.metrics.prometheus import format_gauges
from xlayer_telemetry.metrics.textfile import collect_snapshots, build_metrics
from xlayer_telemetry.telemetry_health import finish


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_until(check, seconds=20):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            value=check()
            if value: return value
        except (OSError, ValueError, RuntimeError): pass
        threading.Event().wait(.2)
    raise RuntimeError('validation readiness timeout')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prometheus',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True,help='New output directory')
    parser.add_argument('--ollama',action='store_true',help='Explicitly run the optional local model after collecting evidence')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    runs=args.output/'runs'
    def emit(run,node,value):
        MetricEmitter(runs/run/'telemetry-metrics',run_id=run,producer='app',role='trainer',worker_id='0',node=node).emit(
            step=1,samples=[Metric('training_loss',value)])
    emit('a','node-a',1)
    emit('b','node-b',2)
    servers=[]; threads=[]; process=None
    checks={}
    try:
        def handler(node):
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    snapshots=collect_snapshots([], [runs], node=node, max_age_seconds=30)
                    body=format_gauges(build_metrics(snapshots)).encode()
                    self.send_response(200); self.send_header("Content-Type", "text/plain; version=0.0.4"); self.end_headers(); self.wfile.write(body)
                def log_message(self,*_args): pass
            return Handler
        for node in ('node-a','node-b'):
            server=ThreadingHTTPServer(('127.0.0.1',0),handler(node)); servers.append(server)
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start(); threads.append(thread)
        targets=[f'127.0.0.1:{server.server_port}' for server in servers]
        config=args.output/'prometheus.yml'
        config.write_text('global:\n  scrape_interval: 1s\nscrape_configs:\n  - job_name: application\n    static_configs:\n      - targets: '+json.dumps(targets)+'\n')
        port=free_port(); base=f'http://127.0.0.1:{port}'
        with (args.output/'prometheus.log').open('w') as log:
            process=subprocess.Popen([str(args.prometheus.resolve()),f'--config.file={config}',
                                      f'--storage.tsdb.path={args.output}/tsdb',f'--web.listen-address=127.0.0.1:{port}'],stdout=log,stderr=log)
            client=PrometheusClient(base)
            def count():
                detail=client.query_range_detail('training_loss',time.time()-5,time.time(),1)
                return detail if len(detail['series'])==2 else None
            detail=wait_until(count)
            checks['two_nodes_same_worker_isolated']=all(item['labels']['worker_id']=='0' for item in detail['series'])
            emit('c','node-a',3)
            wait_until(lambda: len(client.query_range_detail('training_loss',time.time(),time.time()+.01,1)['series'])==3)
            checks['new_run_without_collector_restart']=True
            # Real source timestamps distinguish fresh backend evaluations from old snapshots.
            start=time.time()-5; end=time.time()
            current=dict(record_id='selected-step',run_id='a',node='node-a',worker_id='0',boundary_scope='rl_step',
                         observed_at=end,step=1,step_duration_seconds=5,analysis_window=dict(start=start,end=end,accuracy='exact'))
            report=DiagnosticEngine({'prometheus':{'url':base,'queries':{'training_loss':'training_loss{run_id="a"}'}},
                                     'sampling':{'check_source_freshness':True}}).analyze(current,[])
            sample=report['sampling_quality']['training_loss']['current']
            assert sample['freshness']=='observed' and sample['observed_source_samples']>=1, sample
            checks['real_source_timestamps']=sample
            report.update(analysis_status='final',revision=1,data_origin='synthetic')
            write_report(runs/'a/diagnostics',report)
            finish(runs/'c',0,'ok','disabled')
            with urlopen(f'http://127.0.0.1:{servers[0].server_port}/metrics') as response:
                assert b'run_id="c"' not in response.read()
            checks['completed_run_removed_from_export']=True
            stale=collect_snapshots([], [runs], now=time.time()+60,max_age_seconds=30)
            assert not stale
            checks['snapshot_expiry']=True
            if args.ollama:
                result=subprocess.run([sys.executable,'-m','xlayer_telemetry.analysis.llm_diagnosis',
                                       '--run-root',str(runs/'a'),'--record-id','selected-step'],timeout=650)
                checks['ollama_exit_code']=result.returncode
                assert result.returncode==0
                checks['llm_projection']=bool(list((runs/'a/diagnostics/investigation').glob('llm-*.jsonl')))
            summary={'status':'passed','checks':checks,'limitations':['SDK fixture values, not VERL training.','Logical nodes share one host.','No causal storage or GPU bottleneck asserted.']}
            (args.output/'validation.json').write_text(json.dumps(summary,indent=2)+'\n')
            print(json.dumps(summary,indent=2))
    finally:
        if process is not None:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        for server in servers: server.shutdown(); server.server_close()
        for thread in threads: thread.join()


if __name__=='__main__': main()
