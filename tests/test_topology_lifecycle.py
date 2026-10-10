"""Real publisher and launcher transitions must not leave stale owned topology."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).parents[1]


def test_topology_enable_disable_restart_removes_only_owned_textfiles(tmp_path):
    exporter = tmp_path/'tools/node_exporter-1.9.1.linux-amd64/node_exporter'
    exporter.parent.mkdir(parents=True)
    exporter.write_text('#!/bin/sh\nexec sleep 60\n'); exporter.chmod(0o755)
    topology = tmp_path/'topology'; topology.mkdir()
    (topology/'compute-topology.json').write_text(json.dumps({'components': [{'id': 'node-a', 'role': 'gpu-node'}], 'edges': []}))
    output = tmp_path/'state'; textfiles = output/'textfile'; textfiles.mkdir(parents=True)
    marker = textfiles/'user.prom'; marker.write_text('user_metric 1\n')
    env = {**os.environ, 'TOOLS_DIR': str(tmp_path/'tools'), 'OUTPUT_DIR': str(output),
        'NODE_ADDR': '127.0.0.1', 'NODE_NAME': 'node-a', 'ENABLE_GPU_METRICS': '0', 'PYTHON': sys.executable,
        'TELEMETRY_METRICS_DIR': '', 'TELEMETRY_RUNS_ROOT': '', 'LOKI_PUSH_URL': '', 'TELEMETRY_LOG_ROOTS': '', 'TOPOLOGY_INTERVAL': '.1'}
    def launch(source):
        return subprocess.Popen(['bash', str(ROOT/'scripts/run_telemetry.sh'), 'node'], cwd=ROOT,
            env={**env, 'TOPOLOGY_DIR': source}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def wait_for(process, predicate):
        end = time.monotonic()+5
        while not predicate():
            assert process.poll() is None
            assert time.monotonic() < end
            try: process.wait(timeout=.05)
            except subprocess.TimeoutExpired: pass
    process = launch(str(topology))
    try:
        wait_for(process, lambda: (textfiles/'topology.prom').exists())
    finally:
        process.terminate(); process.wait(timeout=8)
    assert not (textfiles/'topology.prom').exists()
    assert marker.read_text() == 'user_metric 1\n'
    (textfiles/'topology.prom').write_text('stale_configured_topology 1\n')
    process = launch('')
    try:
        wait_for(process, lambda: (textfiles/'collector.prom').exists())
        assert not (textfiles/'topology.prom').exists()
        assert marker.read_text() == 'user_metric 1\n'
    finally:
        process.terminate(); process.wait(timeout=8)
