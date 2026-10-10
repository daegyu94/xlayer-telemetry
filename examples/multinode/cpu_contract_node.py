"""Owned CPU container: actual xltel node launcher, SDK and raw exporter fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.metrics.emitter import Metric, MetricEmitter
from xlayer_telemetry.operations.config import load_config

config_path = Path(sys.argv[1])
config, _ = load_config(config_path)
node = config['NODE_NAME']
root = Path(config['TELEMETRY_HOME'])
root.mkdir(parents=True, exist_ok=True)
(root/'runs').mkdir(exist_ok=True)
mode_path = root/'fault.json'


class Native(BaseHTTPRequestHandler):
    def do_GET(self):
        try: mode = json.loads(mode_path.read_text())
        except (OSError, ValueError): mode = {}
        if mode.get('delay'): time.sleep(3)
        if mode.get('error'):
            self.send_error(503); return
        waiting = 0 if node == 'node-a' else 18
        kv = .3 if node == 'node-a' else .95
        text = '# TYPE vllm:num_requests_waiting gauge\n'
        if not mode.get('missing'):
            text += f'vllm:num_requests_waiting{{engine="0",model_name="synthetic"}} {waiting}\n'
        text += f'# TYPE vllm:kv_cache_usage_perc gauge\nvllm:kv_cache_usage_perc{{engine="0",model_name="synthetic"}} {kv}\n'
        if node == 'node-a':
            counter = 0 if mode.get('counter_reset') else int(time.monotonic()) % 1000
            text += f'# TYPE vllm:num_preemptions_total counter\nvllm:num_preemptions_total{{engine="0",model_name="synthetic"}} {counter}\n'
        if mode.get('sample_limit'):
            text += ''.join(f'fixture_extra{{index="{i}"}} {i}\n' for i in range(200))
        body = text.encode()
        self.send_response(200); self.send_header('Content-Type', 'text/plain; version=0.0.4')
        self.send_header('Content-Length', str(len(body))); self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass

    def log_message(self, *args): pass


server = ThreadingHTTPServer(('0.0.0.0',8000), Native)
threading.Thread(target=server.serve_forever, daemon=True).start()
subprocess.run([sys.executable,'-m','xlayer_telemetry.cli','--config',str(config_path),'up','--role','node'],check=True)
directory = root/'runs/shared-run/telemetry-metrics'
emitter = MetricEmitter(directory, run_id='shared-run', producer='verl', role='trainer', worker_id='0', node=node, gpu='0')
events = EventRecorder(root/'runs/shared-run/telemetry-events', CorrelationContext('shared-run','sdk','rollout','0',node,gpu='0'))
try:
    while True:
        emitter.emit(step=1, samples=[Metric('training_step_duration_seconds',10 if node=='node-a' else 20)])
        events.event('fixture.observation', phase='rollout', step=1, attributes={'data_origin':'synthetic','device':'nvme0n1'})
        time.sleep(.5)
finally:
    server.shutdown()
    subprocess.run([sys.executable,'-m','xlayer_telemetry.cli','--config',str(config_path),'down','--role','node'],timeout=15)
