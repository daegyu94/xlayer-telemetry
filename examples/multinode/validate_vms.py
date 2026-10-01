"""Validate collection and correlation in two disposable KVM guests.

Uses an operator-supplied Ubuntu cloud image, CPU-only fixture workloads, and
real Node Exporter/Prometheus. No host clock or existing service is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import shlex
import socket
import subprocess
import tarfile
import time

from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine
from xlayer_telemetry.prometheus import PrometheusClient

ROOT = Path(__file__).resolve().parents[2]


def run(command, **kwargs):
    return subprocess.run(command, check=True, capture_output=True, timeout=60, **kwargs)


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_for(check, timeout=150):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            last = error
        time.sleep(.5)
    raise RuntimeError(f'validation readiness timeout: {last}')


def payload(exporter):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for path in (ROOT / 'xlayer_telemetry').rglob('*.py'):
            archive.add(path, arcname=str(path.relative_to(ROOT)))
        archive.add(ROOT / 'scripts/run_telemetry.sh', arcname='scripts/run_telemetry.sh')
        archive.add(exporter, arcname='tools/node_exporter-1.9.1.linux-amd64/node_exporter')
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True, help='Trusted amd64 Ubuntu cloud qcow2 image')
    parser.add_argument('--image-sha256', required=True, help='Expected image digest from the publisher')
    parser.add_argument('--node-exporter', type=Path, required=True)
    parser.add_argument('--prometheus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New directory for disposable guest state')
    args = parser.parse_args()
    with args.image.open('rb') as image:
        digest = hashlib.sha256()
        for chunk in iter(lambda: image.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != args.image_sha256:
        parser.error('image SHA256 mismatch')
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    output = args.output.resolve()
    key = output / 'ssh-key'
    run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)])
    public = key.with_suffix('.pub').read_text().strip()
    processes, handles, guests = [], [], []
    checks = {}
    try:
        for name in ('vm-rollout', 'vm-storage'):
            directory = output / name
            directory.mkdir()
            disk = directory / 'disk.qcow2'
            run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(args.image.resolve()), str(disk), '6G'])
            (directory / 'user-data').write_text('#cloud-config\n' + json.dumps({
                'users': [{'name': 'xlayer', 'shell': '/bin/bash', 'sudo': 'ALL=(ALL) NOPASSWD:ALL',
                           'ssh_authorized_keys': [public]}], 'ssh_pwauth': False,
                'package_update': False, 'package_upgrade': False}))
            (directory / 'meta-data').write_text(json.dumps({'instance-id': name, 'local-hostname': name}))
            seed = directory / 'seed.img'
            run(['cloud-localds', str(seed), str(directory / 'user-data'), str(directory / 'meta-data')])
            ssh_port, metric_port = port(), port()
            log = (directory / 'qemu.log').open('w'); handles.append(log)
            process = subprocess.Popen(['qemu-system-x86_64', '-accel', 'kvm', '-cpu', 'host',
                '-m', '1024', '-smp', '1', '-display', 'none', '-monitor', 'none',
                '-serial', f'file:{directory}/serial.log',
                '-drive', f'file={disk},if=virtio,format=qcow2',
                '-drive', f'file={seed},if=virtio,format=raw',
                '-netdev', f'user,id=net,hostfwd=tcp:127.0.0.1:{ssh_port}-:22,hostfwd=tcp:127.0.0.1:{metric_port}-:19100',
                '-device', 'virtio-net-pci,netdev=net'], stdout=log, stderr=log)
            processes.append(process)
            guests.append({'name': name, 'ssh_port': ssh_port, 'metric_port': metric_port, 'process': process})

        def ssh(guest, command, data=None):
            if guest['process'].poll() is not None:
                raise RuntimeError(f"{guest['name']} exited; inspect qemu.log")
            return run(['ssh', '-i', str(key), '-p', str(guest['ssh_port']), '-o', 'BatchMode=yes',
                        '-o', 'ConnectTimeout=2', '-o', 'StrictHostKeyChecking=accept-new',
                        '-o', f'UserKnownHostsFile={output}/known_hosts',
                        'xlayer@127.0.0.1', command], input=data).stdout

        bundle = payload(args.node_exporter)
        for guest in guests:
            wait_for(lambda: ssh(guest, 'printf ready') == b'ready')
            ssh(guest, 'tar -xz -C /home/xlayer', bundle)
            guest['kernel'] = ssh(guest, 'uname -r').decode().strip()
            guest['boot_id'] = ssh(guest, 'cat /proc/sys/kernel/random/boot_id').decode().strip()
            ssh(guest, 'sudo timedatectl set-ntp false')
        assert guests[0]['boot_id'] != guests[1]['boot_id']
        checks['independent_guest_kernels'] = True
        parent = None
        for index, guest in enumerate(guests):
            script = '''from pathlib import Path
import json
from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.metrics import Metric, MetricEmitter
context = CorrelationContext('vm-validation','agent',ROLE,'0',NODE)
events = EventRecorder(Path('run/telemetry-events'), context)
with events.span('tool.call' if ROLE == 'rollout' else 'storage.read',phase='environment',
                 trace_id=TRACE, parent_span_id=PARENT) as span:
    Path('sample.bin').write_bytes(b'x' * 1048576)
    Path('sample.bin').read_bytes()
MetricEmitter(Path('run/telemetry-metrics'),run_id=context.run_id,producer='agent',
              role=context.role,worker_id='0',node=NODE).emit(step=1,samples=[Metric('validation_work',1)])
print(json.dumps({'trace_id':span.trace_id,'span_id':span.span_id}))
'''
            prefix = f"NODE={guest['name']!r}\nROLE={('rollout' if index == 0 else 'storage')!r}\nTRACE={parent['trace_id'] if parent else None!r}\nPARENT={parent['span_id'] if parent else None!r}\n"
            identity = json.loads(ssh(guest, 'python3 -', (prefix + script).encode()))
            if parent:
                assert identity['trace_id'] == parent['trace_id']
            else:
                parent = identity
            ssh(guest, 'nohup env ENABLE_GPU_METRICS=0 NODE_ADDR=0.0.0.0 NODE_NAME=' + guest['name'] +
                ' TOOLS_DIR=/home/xlayer/tools OUTPUT_DIR=/home/xlayer/state'
                ' TELEMETRY_METRICS_DIR=/home/xlayer/run/telemetry-metrics'
                ' bash scripts/run_telemetry.sh node > collector.log 2>&1 < /dev/null &')
        records = [json.loads(line) for guest in guests for line in
                   ssh(guest, 'cat run/telemetry-events/*.jsonl').decode().splitlines()]
        child = next(row for row in records if row['node'] == 'vm-storage')
        assert child['parent_span_id'] == parent['span_id']
        checks['cross_guest_trace_parent'] = True
        (output / 'events.json').write_text(json.dumps(records, indent=2))
        metric_port = port()
        config = output / 'prometheus.json'
        config.write_text(json.dumps({'global': {'scrape_interval':'1s'}, 'scrape_configs':[
            {'job_name':'telemetry', 'static_configs':[
                {'targets':[f"127.0.0.1:{guest['metric_port']}"],
                 'labels':{'instance':guest['name'],'node':guest['name'],'cluster':'vm-validation'}} for guest in guests]}]}))
        log = (output / 'prometheus.log').open('w'); handles.append(log)
        processes.append(subprocess.Popen([str(args.prometheus.resolve()), f'--config.file={config}',
            f'--storage.tsdb.path={output}/tsdb', f'--web.listen-address=127.0.0.1:{metric_port}'],stdout=log,stderr=log))
        base = f'http://127.0.0.1:{metric_port}'
        client = PrometheusClient(base)
        wait_for(lambda: len(client.query_range_detail('validation_work', time.time()-2, time.time(), 1)['series']) == 2)
        checks['real_collectors_and_prometheus'] = True
        engine = DiagnosticEngine({'prometheus':{'url':base}, 'cluster':'vm-validation',
            'node':'vm-rollout','compute_node':'vm-rollout','rollout_node':'vm-rollout',
            'storage_node':'vm-storage','clock':{'require_sync':False,'max_skew_seconds':3}})
        def analyze():
            end = time.time()
            return engine.analyze({'record_id':'vm-step','run_id':'vm-validation','node':'vm-rollout',
                'analysis_window':{'start':end-2,'end':end,'accuracy':'exact'}}, [])
        aligned = wait_for(lambda: (r if (r := analyze())['clock_quality']['status'] == 'aligned' else None))
        checks['initial_clock_alignment'] = aligned['clock_quality']
        ssh(guests[1], "sudo date -s '+30 seconds'")
        shifted = wait_for(lambda: (r if (r := analyze())['clock_quality']['status'] == 'unsafe' else None), 20)
        assert not shifted['candidates'] and shifted['verdict'] == 'insufficient_data'
        checks['guest_clock_skew_withholds_diagnosis'] = shifted['clock_quality']
        ssh(guests[1], 'sudo date -s ' + shlex.quote('@' + str(time.time())))
        wait_for(lambda: analyze()['clock_quality']['status'] == 'aligned', 20)
        checks['clock_recovery'] = True
        guests[1]['process'].terminate(); guests[1]['process'].wait(timeout=10)
        wait_for(lambda: (client.query_range('up{instance="vm-storage"}',time.time(),time.time()+.01,1) or {}).get('min') == 0, 25)
        checks['guest_loss_detected'] = True
        report = {'status':'passed', 'image_sha256':digest.hexdigest(),
            'guests':[{k:v for k,v in guest.items() if k in {'name','kernel','boot_id'}} for guest in guests],
            'checks':checks,'limitations':['Two VMs share one physical host.', 'CPU fixture workload; no VERL training, GPU passthrough, RDMA or 3FS.',
            'Trace context transported via SSH for this fixture.', 'Clock alignment is scrape-relative; NTP synchronization not asserted.']}
        (output / 'validation.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({'status':'passed','report':str(output/'validation.json'),'checks':list(checks)}))
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
        for handle in handles: handle.close()


if __name__ == '__main__':
    main()
