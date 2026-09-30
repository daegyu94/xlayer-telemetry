"""Exercise real Prometheus/Node Exporter on one host with logical node labels.

GPU values come from nvidia-smi. Injected clock offsets are explicitly synthetic;
this is not a physical multi-node or distributed VERL training benchmark.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import threading
import time
from urllib.request import urlopen

from xlayer_telemetry.analysis.clock_quality import assess_clocks
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, PrometheusClient
from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.collectors.gpu_sampler import snapshot
from xlayer_telemetry.metrics import Metric, MetricEmitter
from xlayer_telemetry.metrics.prometheus import GaugeSample, write_gauges
from xlayer_telemetry.metrics.textfile import _iter_snapshots, build_metrics


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def request(url):
    with urlopen(url, timeout=3) as response:
        return json.load(response)


def wait_for(check, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = check()
            if result: return result
        except (OSError, ValueError):
            pass
        threading.Event().wait(.25)
    raise RuntimeError('validation readiness timeout')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-exporter', type=Path, required=True)
    parser.add_argument('--prometheus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New directory for isolated state and report')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    processes, handles, servers, threads = [], [], [], []
    skew = {'storage-a': 0.0}
    nodes = ['gpu-a', 'gpu-b', 'storage-a']
    cluster = 'multinode-validation'
    checks = {}
    try:
        gpu_sample = snapshot()
        if len(gpu_sample['gpus']) < 2:
            raise RuntimeError('this hardware validation requires at least two GPUs')
        targets = []
        shared = args.output / 'shared-metrics'
        events = args.output / 'telemetry-events'
        trace = None
        for index, node in enumerate(nodes):
            directory = args.output / node
            textfiles = directory / 'textfile'
            textfiles.mkdir(parents=True)
            context = CorrelationContext('local-validation', 'agent', 'rollout' if index < 2 else 'storage', '0', node)
            recorder = EventRecorder(events, context)
            with recorder.span('tool.call' if index < 2 else 'storage.read', phase='rollout' if index < 2 else 'storage',
                               trace_id=trace.trace_id if trace else None,
                               parent_span_id=trace.span_id if trace else None) as identity:
                if trace is None: trace = identity
                # Bounded real file work; no injected I/O pressure or bottleneck.
                start = time.monotonic()
                (directory / 'sample.bin').write_bytes(b'x' * 1024 * 1024)
                (directory / 'sample.bin').read_bytes()
                duration = time.monotonic() - start
            MetricEmitter(shared, run_id='local-validation', producer='agent', role=context.role,
                          worker_id='0', node=node).emit(step=1, samples=[Metric('validation_file_duration_seconds', duration)])
            selected = [item for item in _iter_snapshots(shared) if item['node'] == node]
            write_gauges(textfiles, 'application.prom', build_metrics(selected))
            if index < 2:
                gpu = gpu_sample['gpus'][index]
                write_gauges(textfiles, 'gpu.prom', [GaugeSample('telemetry_gpu_utilization_percent', 'nvidia-smi utilization.',
                    gpu['utilization.gpu'], {'gpu': str(int(gpu['index'])), 'gpu_uuid': gpu['uuid']})])
            port = free_port()
            log = (directory / 'node-exporter.log').open('w'); handles.append(log)
            processes.append(subprocess.Popen([str(args.node_exporter.resolve()), f'--web.listen-address=127.0.0.1:{port}',
                                               f'--collector.textfile.directory={textfiles}'], stdout=log, stderr=subprocess.STDOUT))
            def handler(node=node, port=port):
                class Proxy(BaseHTTPRequestHandler):
                    def do_GET(self):
                        with urlopen(f'http://127.0.0.1:{port}/metrics', timeout=3) as response:
                            lines = response.read().decode().splitlines()
                        body = '\n'.join('node_time_seconds ' + str(float(line.split()[1]) + skew.get(node, 0))
                                         if line.startswith('node_time_seconds ') else line for line in lines).encode() + b'\n'
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/plain; version=0.0.4')
                        self.end_headers(); self.wfile.write(body)
                    def log_message(self, *args): pass
                return Proxy
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler())
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            servers.append(server); threads.append(thread)
            targets.append((node, server.server_port))
        prom_port = free_port()
        config = args.output / 'prometheus.yml'
        config.write_text('global:\n  scrape_interval: 1s\nscrape_configs:\n  - job_name: telemetry\n    static_configs:\n' + ''.join(
            f'      - targets: ["127.0.0.1:{port}"]\n        labels:\n          cluster: {cluster}\n          instance: {node}\n          nodename: {node}\n'
            for node, port in targets))
        log = (args.output / 'prometheus.log').open('w'); handles.append(log)
        processes.append(subprocess.Popen([str(args.prometheus.resolve()), f'--config.file={config}',
                                           f'--storage.tsdb.path={args.output / "tsdb"}',
                                           f'--web.listen-address=127.0.0.1:{prom_port}'], stdout=log, stderr=subprocess.STDOUT))
        base = f'http://127.0.0.1:{prom_port}'
        client = PrometheusClient(base)
        wait_for(lambda: sum(item['health'] == 'up' for item in request(base + '/api/v1/targets')['data']['activeTargets']) == 3)
        begin = time.time()
        threading.Event().wait(3)
        end = time.time()
        def clocks(start, end, require_sync=False):
            return assess_clocks(client.query_range, cluster=cluster, nodes=nodes, start=start, end=end,
                                 require_sync=require_sync, max_sample_age_seconds=5)
        aligned = clocks(begin, end)
        assert aligned['status'] == 'aligned', aligned
        checks['scrape_relative_alignment_without_ntp_requirement'] = aligned
        checks['strict_sync_check'] = clocks(begin, end, True)
        gpu = client.query_range_detail(f'telemetry_gpu_utilization_percent{{cluster="{cluster}"}}', begin, end, 1)['series']
        assert len(gpu) == 2 and len({item['labels']['gpu_uuid'] for item in gpu}) == 2
        checks['real_gpu_series'] = gpu
        application = client.query_range_detail(f'validation_file_duration_seconds{{cluster="{cluster}"}}', begin, end, 1)['series']
        assert len(application) == 3
        assert all(item['labels']['node'] == item['labels']['instance'] for item in application)
        checks['node_local_worker_snapshots'] = [item['labels'] for item in application]
        assert client.query_range(f'node_disk_io_time_seconds_total{{cluster="{cluster}",instance="storage-a"}}', begin, end, 1)
        checks['storage_host_metrics'] = True
        span_records = [json.loads(line) for path in events.glob('*.jsonl') for line in path.read_text().splitlines()]
        assert len(span_records) == 3 and len({record['trace_id'] for record in span_records}) == 1
        assert sum(record.get('parent_span_id') == trace.span_id for record in span_records) == 2
        checks['cross_node_trace_parent_links'] = True
        interval = {'schema_version': 1, 'record_type': 'verl_step_observation', 'record_id': 'local',
            'run_id': 'local-validation', 'node': 'gpu-a', 'step': 1, 'worker_id': '0', 'boundary_scope': 'validation_interval',
            'observed_at': end, 'analysis_window': {'start': begin, 'end': end, 'accuracy': 'validation_interval'}}
        (events / 'verl-steps.jsonl').write_text(json.dumps(interval) + '\n')
        diagnostic_config = {'prometheus': {'url': base, 'queries': {
                                'disk_read_bytes_per_second': 'rate(node_disk_read_bytes_total{cluster="{cluster}",job="telemetry",instance="{storage_node}"}[1m])',
                             }}, 'cluster': cluster,
                             'compute_node': 'gpu-b', 'rollout_node': 'gpu-b',
                             'storage_node': 'storage-a', 'clock': {'require_sync': False}}
        engine = DiagnosticEngine(diagnostic_config, prometheus=client)
        diagnosis = engine.analyze(interval, [])
        assert diagnosis['clock_quality']['status'] == 'aligned'
        assert set(diagnosis['clock_quality']['nodes']) == set(nodes)
        assert {'gpu_utilization_percent', 'host_memory_available_ratio', 'disk_read_bytes_per_second'} <= set(diagnosis['evidence'])
        checks['diagnosis_three_node_query'] = True
        skew['storage-a'] = 12
        shifted_start = time.time() + 1
        threading.Event().wait(4)
        unsafe = clocks(shifted_start, time.time())
        assert unsafe['status'] == 'unsafe'
        assert 'scrape_relative_clock_offset' in unsafe['nodes']['storage-a']['issues']
        checks['synthetic_plus_12s_clock_skew'] = unsafe
        interval['analysis_window'] = {'start': shifted_start, 'end': time.time()}
        diagnosed = engine.analyze(interval, [])
        assert diagnosed['verdict'] == 'insufficient_data' and not diagnosed['candidates'] and not diagnosed['findings']
        checks['real_backend_diagnosis_withheld_for_skew'] = True
        # Remove one target source; old lookback data must not hide staleness.
        servers[-1].shutdown(); servers[-1].server_close()
        missing_start = time.time()
        threading.Event().wait(7)
        stale = clocks(missing_start, time.time())
        assert stale['status'] != 'aligned'
        checks['stopped_storage_source'] = stale
        report = {'status': 'passed', 'physical_hosts': 1, 'logical_nodes': nodes,
                  'limitations': ['No distributed VERL training or independent remote clocks tested.',
                                  'Clock skew is injected into an HTTP evidence proxy; system clocks are unchanged.',
                                  'Node Exporter host counters share one physical machine.',
                                  'No local SSD or 3FS bottleneck is asserted.'], 'checks': checks}
        (args.output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'status': 'passed', 'report': str(args.output / 'validation.json'), 'checks': list(checks)}, indent=2))
    finally:
        for server in servers: server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=3)
        for process in reversed(processes):
            if process.poll() is None: process.terminate()
        for process in processes:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        for handle in handles: handle.close()


if __name__ == '__main__': main()
