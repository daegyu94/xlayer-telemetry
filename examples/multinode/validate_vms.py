"""Validate collection and correlation in two disposable KVM guests.

Uses an operator-supplied Ubuntu cloud image, CPU-only fixture workloads, and
real Node Exporter/Prometheus. No host clock or existing service is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tarfile
import time
from urllib.parse import urlencode
from urllib.request import urlopen

from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine
from xlayer_telemetry.prometheus import PrometheusClient

ROOT = Path(__file__).resolve().parents[2]


def run(command, **kwargs):
    kwargs.setdefault('timeout', 60)
    return subprocess.run(command, check=True, capture_output=True, **kwargs)


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


def payload(exporter, cli_tools=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for path in (ROOT / 'xlayer_telemetry').rglob('*.py'):
            archive.add(path, arcname=str(path.relative_to(ROOT)))
        archive.add(ROOT / 'scripts/run_telemetry.sh', arcname='scripts/run_telemetry.sh')
        archive.add(exporter, arcname='tools/node_exporter-1.9.1.linux-amd64/node_exporter')
        if cli_tools:
            archive.add(ROOT / 'scripts/verl_local.sh', arcname='scripts/verl_local.sh')
            archive.add((cli_tools / 'alloy-linux-amd64').resolve(), arcname='tools/alloy-linux-amd64', recursive=False)
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True, help='Trusted amd64 Ubuntu cloud qcow2 image')
    parser.add_argument('--image-sha256', required=True, help='Expected image digest from the publisher')
    parser.add_argument('--node-exporter', type=Path, required=True)
    parser.add_argument('--prometheus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New directory for disposable guest state')
    parser.add_argument('--cli-tools-dir', type=Path, help='Also validate xltel host server, guest node lifecycle and Loki; full tool directory')
    args = parser.parse_args()
    with args.image.open('rb') as image:
        digest = hashlib.sha256()
        for chunk in iter(lambda: image.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != args.image_sha256:
        parser.error('image SHA256 mismatch')
    if args.cli_tools_dir:
        required = [args.node_exporter, args.prometheus, args.cli_tools_dir/'alloy-linux-amd64',
                    args.cli_tools_dir/'loki-linux-amd64', args.cli_tools_dir/'grafana-v12.1.0/bin/grafana']
        if any(not path.is_file() or not os.access(path,os.X_OK) for path in required):
            parser.error('CLI validation requires executable Node Exporter, Prometheus, Grafana, Loki and Alloy.')
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    output = args.output.resolve()
    key = output / 'ssh-key'
    run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)])
    public = key.with_suffix('.pub').read_text().strip()
    processes, handles, guests = [], [], []
    checks = {}
    cli_config = None
    def host_cli(*argv):
        result = run([sys.executable, '-m', 'xlayer_telemetry.cli', '--config', str(cli_config), *argv], cwd=ROOT, timeout=120)
        return result
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
            ssh_port, metric_port = port(), 19100 if args.cli_tools_dir else port()
            metric_addr = f'127.0.0.{len(guests)+2}' if args.cli_tools_dir else '127.0.0.1'
            with socket.socket() as probe:
                probe.bind((metric_addr, metric_port))
            log = (directory / 'qemu.log').open('w'); handles.append(log)
            process = subprocess.Popen(['qemu-system-x86_64', '-accel', 'kvm', '-cpu', 'host',
                '-m', '1024', '-smp', '1', '-display', 'none', '-monitor', 'none',
                '-serial', f'file:{directory}/serial.log',
                '-drive', f'file={disk},if=virtio,format=qcow2',
                '-drive', f'file={seed},if=virtio,format=raw',
                '-netdev', f'user,id=net,hostfwd=tcp:127.0.0.1:{ssh_port}-:22,hostfwd=tcp:{metric_addr}:{metric_port}-:19100',
                '-device', 'virtio-net-pci,netdev=net'], stdout=log, stderr=log)
            processes.append(process)
            guests.append({'name': name, 'ssh_port': ssh_port, 'metric_port': metric_port, 'metric_addr':metric_addr, 'process': process})

        def ssh(guest, command, data=None):
            if guest['process'].poll() is not None:
                raise RuntimeError(f"{guest['name']} exited; inspect qemu.log")
            return run(['ssh', '-i', str(key), '-p', str(guest['ssh_port']), '-o', 'BatchMode=yes',
                        '-o', 'ConnectTimeout=2', '-o', 'StrictHostKeyChecking=accept-new',
                        '-o', f'UserKnownHostsFile={output}/known_hosts',
                        'xlayer@127.0.0.1', command], input=data).stdout

        bundle = payload(args.node_exporter, args.cli_tools_dir)
        for guest in guests:
            wait_for(lambda: ssh(guest, 'printf ready') == b'ready')
            ssh(guest, 'tar -xz -C /home/xlayer', bundle)
            guest['kernel'] = ssh(guest, 'uname -r').decode().strip()
            guest['os'] = ssh(guest, '. /etc/os-release; printf "%s" "$PRETTY_NAME"').decode().strip()
            guest['boot_id'] = ssh(guest, 'cat /proc/sys/kernel/random/boot_id').decode().strip()
            ssh(guest, 'sudo timedatectl set-ntp false')
        assert guests[0]['boot_id'] != guests[1]['boot_id']
        checks['independent_guest_kernels'] = True
        print('VMs ready: separate guest kernels and clocks', flush=True)
        if args.cli_tools_dir:
            ports = {name: port() for name in ('PROMETHEUS', 'GRAFANA', 'LOKI')}
            cli_config = output / 'host.toml'
            values = {'TELEMETRY_HOME':str(output / 'host'), 'TOOLS_DIR':str(args.cli_tools_dir.resolve()),
                      'CLUSTER_NAME':'vm-validation', 'ENABLE_GPU_METRICS':False, 'ENABLE_LOGS':True,
                      'TELEMETRY_TARGETS':','.join(g['name']+'='+g['metric_addr'] for g in guests)}
            values.update({name+'_PORT':value for name, value in ports.items()})
            cli_config.write_text('[telemetry]\n' + ''.join(f'{key}={json.dumps(value)}\n' for key,value in values.items()))
            # Source-mode CLI uses exactly the public parser/config/lifecycle implementation.
            for guest in guests:
                settings = {'TELEMETRY_HOME':'/home/xlayer/telemetry', 'TOOLS_DIR':'/home/xlayer/tools',
                            'NODE_NAME':guest['name'], 'NODE_ADDR':'0.0.0.0', 'CLUSTER_NAME':'vm-validation',
                            'ENABLE_GPU_METRICS':False, 'ENABLE_LOGS':True,
                            'RUN_ROOT':'/home/xlayer/run', 'TELEMETRY_METRICS_DIR':'/home/xlayer/run/telemetry-metrics'}
                settings.update({name+'_URL':f'http://10.0.2.2:{value}' for name,value in ports.items()})
                text = '[telemetry]\n' + ''.join(f'{key}={json.dumps(value)}\n' for key,value in settings.items())
                ssh(guest, 'cat > cli.toml', text.encode())
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
            if args.cli_tools_dir:
                ssh(guest, 'python3 -m xlayer_telemetry.cli --config cli.toml up --role node')
                ssh(guest, 'python3 -m xlayer_telemetry.cli --config cli.toml up --role node')
            else:
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
        if args.cli_tools_dir:
            host_cli('up', '--role', 'server')
            host_cli('up', '--role', 'server')
            base = f'http://127.0.0.1:{ports["PROMETHEUS"]}'
            checks['cli_role_start_idempotent'] = True
        else:
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
        print('Collected application metrics from both guests', flush=True)
        if args.cli_tools_dir:
            def guest_status(guest):
                result = ssh(guest, 'python3 -m xlayer_telemetry.cli --config cli.toml status --role node --json')
                return json.loads(result)
            for guest in guests:
                wait_for(lambda: guest_status(guest)['status'] == 'healthy', 45)
            assert json.loads(host_cli('status','--role','server','--json').stdout)['status'] == 'healthy'
            checks['cli_host_and_guest_health'] = True
            # Both real Alloy agents ship event JSONL from separate guest filesystems.
            def logs():
                query = urlencode({'query':'{cluster="vm-validation",signal="xlayer_event"}', 'limit':100})
                with urlopen(f'http://127.0.0.1:{ports["LOKI"]}/loki/api/v1/query_range?{query}',timeout=5) as response:
                    return json.load(response)['data']['result']
            def both_logs():
                streams = logs()
                return streams if {s['stream'].get('node') for s in streams} == {'vm-rollout','vm-storage'} else None
            logged = wait_for(both_logs, 45)
            checks['cross_guest_loki_events'] = sorted({s['stream']['node'] for s in logged})
            grafana_base = f'http://127.0.0.1:{ports["GRAFANA"]}'
            metric_query = urlencode({'query':'validation_work{run_id="vm-validation"}'})
            with urlopen(grafana_base+'/api/datasources/proxy/uid/telemetry-prometheus/api/v1/query?'+metric_query,timeout=5) as response:
                result = json.load(response)['data']['result']
            assert {row['metric'].get('node') for row in result} == {'vm-rollout','vm-storage'}
            log_query = urlencode({'query':'{cluster="vm-validation",signal="xlayer_event"} | json | trace_id="'+parent['trace_id']+'"','limit':100})
            with urlopen(grafana_base+'/api/datasources/proxy/uid/telemetry-loki/loki/api/v1/query_range?'+log_query,timeout=5) as response:
                result = json.load(response)['data']['result']
            rows = [json.loads(value[1]) for stream in result for value in stream['values']]
            assert {row['node'] for row in rows} == {'vm-rollout','vm-storage'}
            assert any(row['parent_span_id'] == parent['span_id'] for row in rows if row['node'] == 'vm-storage')
            checks['grafana_datasource_metric_and_trace_log_queries'] = True
            before = ssh(guests[1], 'cat telemetry/state/verl-local/node.pid').decode()
            ssh(guests[1], 'python3 -m xlayer_telemetry.cli --config cli.toml down --role node')
            stopped = json.loads(ssh(guests[1], 'python3 -m xlayer_telemetry.cli --config cli.toml status --role node --json; result=$?; test "$result" = 1'))
            assert stopped['services']['node']['process'] == 'stopped' and stopped['status'] == 'degraded'
            checks['cli_stopped_collector_not_reported_healthy'] = True
            ssh(guests[1], 'python3 -m xlayer_telemetry.cli --config cli.toml restart --role node')
            after = ssh(guests[1], 'cat telemetry/state/verl-local/node.pid').decode()
            assert before != after
            wait_for(lambda: guest_status(guests[1])['status'] == 'healthy',45)
            checks['cli_collector_restart'] = True
            print('CLI health, Loki event delivery and collector restart passed', flush=True)
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
        print('Clock skew rejection and recovery passed', flush=True)
        guests[1]['process'].terminate(); guests[1]['process'].wait(timeout=10)
        wait_for(lambda: (client.query_range('up{instance="vm-storage"}',time.time(),time.time()+.01,1) or {}).get('min') == 0, 45)
        checks['guest_loss_detected'] = True
        print('VM loss detected by Prometheus', flush=True)
        if args.cli_tools_dir:
            result = subprocess.run([sys.executable,'-m','xlayer_telemetry.cli','--config',str(cli_config),
                                     'status','--role','server','--json'],capture_output=True,timeout=15,cwd=ROOT)
            assert result.returncode == 1 and json.loads(result.stdout)['status'] == 'degraded'
            checks['cli_server_reports_vm_loss'] = True
            ssh(guests[0], 'python3 -m xlayer_telemetry.cli --config cli.toml down --role node')
            ssh(guests[0], 'python3 -m xlayer_telemetry.cli --config cli.toml down --role node')
            host_cli('down','--role','server')
            host_cli('down','--role','server')
            checks['cli_role_stop_idempotent'] = True
        report = {'status':'passed', 'image_sha256':digest.hexdigest(),
            'guests':[{k:v for k,v in guest.items() if k in {'name','kernel','boot_id','os'}} for guest in guests],
            'checks':checks,'limitations':['Two VMs share one physical host.', 'CPU fixture workload; no VERL training, GPU passthrough, RDMA or 3FS.',
            'Trace context transported via SSH for this fixture.', 'Clock alignment is scrape-relative; NTP synchronization not asserted.'],
            'cli_mode':bool(args.cli_tools_dir), 'cli_entrypoint':'python -m xlayer_telemetry.cli' if args.cli_tools_dir else None,
            'resources':{'physical_hosts':1,'per_guest_ram_mib':1024,'per_guest_vcpus':1,'per_guest_overlay_virtual_gib':6}}
        (output / 'validation.json').write_text(json.dumps(report,indent=2)+'\n')
    except Exception as error:
        (output / 'failure.json').write_text(json.dumps({'status':'failed','error':type(error).__name__,
            'checks_completed':checks, 'stderr':getattr(error,'stderr',b'').decode(errors='replace') if
            isinstance(getattr(error,'stderr',None),bytes) else str(getattr(error,'stderr',''))},indent=2)+'\n')
        raise
    finally:
        cleanup_error = None
        if cli_config and cli_config.exists():
            try:
                result = subprocess.run([sys.executable,'-m','xlayer_telemetry.cli','--config',str(cli_config),
                                         'down','--role','server'],capture_output=True,timeout=20,cwd=ROOT)
                if result.returncode:
                    cleanup_error = 'Host CLI cleanup failed; inspect the validation state directory.'
            except (OSError,subprocess.TimeoutExpired):
                cleanup_error = 'Host CLI cleanup did not complete; inspect the validation state directory.'
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
        for handle in handles: handle.close()
        if cleanup_error:
            (output / 'validation.json').write_text(json.dumps({'status':'failed','error':cleanup_error})+'\n')
            raise RuntimeError(cleanup_error)
    print(json.dumps({'status':'passed','report':str(output/'validation.json'),'checks':list(checks)}))


if __name__ == '__main__':
    main()
