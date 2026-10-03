"""Independent producer processes with different wall clocks, shared reference."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

from xlayer_telemetry.operations.clock import ClockServer
from xlayer_telemetry.analysis.diagnostics import tool_span_window
from xlayer_telemetry.metrics.textfile import build_metrics


WORKER = r'''
import json, sys, time
from pathlib import Path
from xlayer_telemetry.operations.clock import calibrate, save_calibration
from xlayer_telemetry.time_alignment import CalibrationCache
from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.metrics import Metric, MetricEmitter
from xlayer_telemetry.step_history import StepHistoryWriter
root, url, node, skew = Path(sys.argv[1]), sys.argv[2], sys.argv[3], float(sys.argv[4])
root.mkdir()
wall = lambda: time.time()+skew
measured = calibrate(url, node=node, reference_id='monitor', samples=3, ttl=60, wall_clock=wall)
save_calibration(root/'clock.json', measured)
cache = CalibrationCache(root/'clock.json', node=node, wall_clock=wall)
recorder = EventRecorder(root, CorrelationContext('run', 'agent', 'rollout', '0', node),
                         clock_ns=lambda: round(wall()*1e9), monotonic_ns=time.monotonic_ns,
                         time_calibration=cache)
with recorder.span('tool.call', phase='environment', attributes={'tool': 'pytest'},
                   trace_id='shared-trace', parent_span_id='trainer-parent'):
    time.sleep(.02)
step = StepHistoryWriter(root/'steps.jsonl', run_id='run', node=node, worker_id='0',
                         clock=wall, time_calibration=cache).append({'step':1,'data':{'timing_s/step':.02}})
emitter = MetricEmitter(root, run_id='run', producer='verl', role='trainer', worker_id='0',
                        node=node, clock=wall, time_calibration=cache)
snapshot = json.loads(emitter.emit(step=1, samples=[Metric('training_loss', .5)]).read_text())
print(json.dumps({'span':json.loads(recorder.path.read_text()), 'step':step, 'snapshot':snapshot}))
'''


def test_two_process_clocks_share_axis_and_keep_identity(tmp_path):
    server = ClockServer(('127.0.0.1', 0), reference_id='monitor')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    clients = []
    started = time.time()
    try:
        url = f'http://127.0.0.1:{server.server_port}'
        for node, skew in [('gpu-a', 12), ('sandbox-b', -7)]:
            clients.append(subprocess.Popen([sys.executable, '-c', WORKER, str(tmp_path/node), url, node, str(skew)],
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        outputs = []
        for client in clients:
            stdout, stderr = client.communicate(timeout=10)
            assert client.returncode == 0, stderr
            outputs.append(json.loads(stdout))
        finished = time.time()
        for node, result in zip(['gpu-a', 'sandbox-b'], outputs):
            span = result['span']
            assert span['node'] == result['step']['node'] == node
            assert span['trace_id'] == 'shared-trace' and span['parent_span_id'] == 'trainer-parent'
            assert span['boundary_accuracy'] == 'calibrated'
            assert started-.1 <= span['start_time_ms']/1000 <= finished+.1
            assert started-.1 <= result['step']['analysis_window']['end'] <= finished+.1
            metrics = build_metrics([result['snapshot']])
            stamp = next(m for m in metrics if m.name == 'training_sample_timestamp_seconds')
            assert started-.1 <= stamp.value <= finished+.1
            assert not {'reference_id','offset','calibration','trace_id'} & stamp.labels.keys()
            assert tool_span_window(tmp_path/node, 'run', started-.1, finished+.1,
                                    reference_id='monitor') is not None
        raw = [r['span']['start_time_unix_nano']/1e9 for r in outputs]
        assert 18 < abs(raw[0]-raw[1]) < 20
    finally:
        for client in clients:
            if client.poll() is None:
                client.kill()
            client.communicate(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert not thread.is_alive()
