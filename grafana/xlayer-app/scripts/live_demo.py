#!/usr/bin/env python3
"""Isolated, loopback-only Scenes PoC. Requires existing Grafana/Prometheus/Loki binaries.

Uses the repository's live exporter and diagnosis generator. SIGINT/SIGTERM
stops only processes launched by this script. No existing config/container is touched.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from xlayer_telemetry.demos.live import Demo, prometheus_config
from xlayer_telemetry.demos.diagnosis import generate
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, _investigation_rows, write_report
from xlayer_telemetry.demos.scenario import make_scenario
from xlayer_telemetry.demos.multi_job import diagnosis_config, make_schedule, record_job
from xlayer_telemetry.fileio import atomic_write_text, append_jsonl
from xlayer_telemetry.analysis.deadline import IsolatedAnalyzer
from xlayer_telemetry.events import CorrelationContext


def collect_and_analyze_job(directory, job, prom):
    streams=[]
    run_id=job['scenario']['run_id']; node=job['scenario']['node']
    steps=record_job(directory,job)
    config=diagnosis_config(job,prom,events_dir=directory/'telemetry-events')
    with IsolatedAnalyzer(config, seconds=60) as analyzer:
        report=analyzer.analyze(steps[-1],directory/'telemetry-events/verl-steps.jsonl')
    report['data_origin']='synthetic'
    report['run_model']={'identifier':job['model'],'source':'run_manifest','weights_loaded':False}
    report['limitations'].append('Concurrent synthetic jobs share this node; endpoint configuration is not operation attribution. No model weights are loaded.')
    write_report(directory/'diagnostics',report)
    files=[('verl_step',directory/'telemetry-events/verl-steps.jsonl')]+[
        ('xlayer_event',p) for p in (directory/'telemetry-events').glob('*.jsonl') if p.name!='verl-steps.jsonl']
    for telemetry_signal,path in files:
        values=[]
        for i,line in enumerate(path.read_text().splitlines()):
            row=json.loads(line); stamp=row.get('event_time_unix_nano',int(row.get('observed_at',steps[-1]['observed_at'])*1e9))
            values.append([str(int(stamp)+i),line])
        streams.append({'stream':{'signal':telemetry_signal,'cluster':'scenes-demo','node':node,'data_origin':'synthetic','run_id':run_id},'values':values})
    rows=_investigation_rows(report)
    streams.append({'stream':{'signal':'xlayer_diagnosis','cluster':'scenes-demo','node':node,'data_origin':'synthetic','run_id':run_id},
        'values':[[str(int(row['window_end_ms'])*1000000-len(rows)+i),json.dumps(row)] for i,row in enumerate(rows)]})
    streams.append({'stream':{'cluster':'scenes-demo','node':node,'data_origin':'synthetic','run_id':run_id},
        'values':[[str(int(steps[-1]['observed_at']*1e9)),json.dumps({'run_id':run_id,'log_file':'agent.log','_entry':(directory/'logs/agent.log').read_text()})]]})
    return streams, report


def validate_demo_app(package, dist):
    """Validate build/package before creating demo state or starting services."""
    if package:
        from xlayer_telemetry.operations.app import _unpack, checksum
        with tempfile.TemporaryDirectory(prefix='xlayer-demo-app-check-') as stage:
            _unpack(package, Path(stage), checksum(package), allow_unsigned=True)
        return
    required = ('module.js', 'plugin.json', 'img/logo.svg')
    if any(not (dist / name).is_file() or not (dist / name).stat().st_size for name in required):
        raise ValueError('App bundle missing or incomplete: run npm ci && npm run build in grafana/xlayer-app, or provide --app-package ZIP with its SHA256 sidecar.')
    metadata = json.loads((dist / 'plugin.json').read_text())
    if not isinstance(metadata, dict) or metadata.get('id') != 'xlayer-telemetry-app' or metadata.get('type') != 'app':
        raise ValueError('App bundle has an unexpected plugin identity; rebuild the checkout.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New, disposable state directory')
    parser.add_argument('--grafana-port', type=int, default=23400)
    parser.add_argument('--scenario', choices=('storage-regression', 'normal', 'alternating'), default='alternating',
                        help='Explicit synthetic comparison: real scrapes precede completed SDK spans/report')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--multi-job', action='store_true', help='Three concurrent synthetic jobs; actual query/diagnosis with shared and missing evidence')
    modes.add_argument('--multi-worker', action='store_true',
                        help='Opt-in controlled rollout spans on four workers with one explicit synthetic outlier')
    parser.add_argument('--storage-series',action='store_true',help='Explicit synthetic 3FS collection points, sparse report coverage and no clock attribution')
    parser.add_argument('--app-package',type=Path,help='Install a checksum-verified local package into this isolated demo')
    args = parser.parse_args()
    if args.multi_job and args.storage_series:
        parser.error('--multi-job uses actual diagnosis queries and does not inject --storage-series fixtures')
    args.output = args.output.resolve()
    args.tools = args.tools.resolve()
    if not 1 <= args.grafana_port <= 65535:
        parser.error('--grafana-port must be 1..65535')
    with socket.socket() as probe:
        try: probe.bind(('127.0.0.1', args.grafana_port))
        except OSError: parser.error('Grafana port already in use; choose another --grafana-port')
    try:
        validate_demo_app(args.app_package, ROOT / 'grafana/xlayer-app/dist')
    except (OSError, ValueError) as error:
        parser.error(str(error))
    args.output.mkdir(parents=True, exist_ok=False)
    processes = []
    stopping = threading.Event()
    def stop(*_): stopping.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def port():
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]
    def request(url, body=None):
        req = urllib.request.Request(url, json.dumps(body).encode() if body else None,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=5) as response: return response.read()
    def ready(url, owner=None):
        for _ in range(100):
            if owner is not None and owner.poll() is not None:
                raise RuntimeError('Owned service exited before readiness: '+url)
            try: return request(url)
            except Exception:
                if stopping.wait(.2): raise RuntimeError('Interrupted')
        raise RuntimeError('Service not ready: '+url)
    def launch(argv, name, env=None):
        log = (args.output / (name+'.log')).open('w')
        process = subprocess.Popen(argv, stdout=log, stderr=log, env=env)
        processes.append((process, log))
        return process
    try:
        ep, pp, lp, grpc = port(), port(), port(), port()
        scenario_state = args.output / 'scenario-state.json'
        demo = Demo(ROOT / 'examples/live-demo', **({'multi_job_state': scenario_state} if args.multi_job else {'scenario_state': scenario_state}))
        if args.multi_job:
            atomic_write_text(scenario_state, json.dumps(make_schedule(start=time.time()+120,
                node=demo.gpu['gpu_nodes'][0], replica_nodes=demo.gpu['gpu_nodes'][1:3])))
        pc = args.output / 'prometheus.yaml'
        pc.write_text(prometheus_config(demo, f'127.0.0.1:{ep}', 'scenes-demo'))
        launch([sys.executable, '-m', 'xlayer_telemetry.demos.live', '--listen', f'127.0.0.1:{ep}', '--multi-job-state' if args.multi_job else '--scenario-state', str(scenario_state)], 'exporter')
        prom_process = launch([str(args.tools / 'prometheus-3.5.0.linux-amd64/prometheus'), f'--config.file={pc}',
                f'--storage.tsdb.path={args.output}/prometheus', f'--web.listen-address=127.0.0.1:{pp}'], 'prometheus')
        lc = args.output / 'loki.yaml'
        lc.write_text(f'''auth_enabled: false
server:
  http_listen_address: 127.0.0.1
  http_listen_port: {lp}
  grpc_listen_address: 127.0.0.1
  grpc_listen_port: {grpc}
common:
  path_prefix: {args.output}/loki
  instance_addr: 127.0.0.1
  ring:
    kvstore:
      store: inmemory
  replication_factor: 1
  storage:
    filesystem:
      chunks_directory: {args.output}/chunks
      rules_directory: {args.output}/rules
schema_config:
  configs:
    - from: 2024-01-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
analytics:
  reporting_enabled: false
''')
        loki_process = launch([str(args.tools / 'loki-linux-amd64'), f'-config.file={lc}'], 'loki')
        prom, loki = f'http://127.0.0.1:{pp}', f'http://127.0.0.1:{lp}'
        ready(prom+'/-/ready',prom_process); ready(loki+'/ready',loki_process)
        provisioning = args.output / 'provisioning'
        for name in ('dashboards','datasources','plugins'): (provisioning/name).mkdir(parents=True)
        dashboards = args.output/'dashboards'
        subprocess.run([sys.executable, str(ROOT/'scripts/provision_dashboards.py'), '--output', str(dashboards), '--enable-logs'], check=True)
        (provisioning/'datasources/local.yaml').write_text(f'''apiVersion: 1
datasources:
  - name: Scenes demo Prometheus
    uid: telemetry-prometheus
    type: prometheus
    access: proxy
    url: {prom}
    jsonData:
      timeInterval: 2s
  - name: Scenes demo Loki
    uid: telemetry-loki
    type: loki
    access: proxy
    url: {loki}
''')
        (provisioning/'dashboards/local.yaml').write_text(f'apiVersion: 1\nproviders:\n  - name: XLayer Scenes demo\n    type: file\n    options:\n      path: {dashboards}\n')
        (provisioning/'plugins/local.yaml').write_text('apiVersion: 1\napps:\n  - type: xlayer-telemetry-app\n    org_id: 1\n    disabled: false\n')
        if args.app_package:
            from xlayer_telemetry.operations.app import install_package,checksum
            install_package({'SERVER_OUTPUT_DIR':str(args.output),'TOOLS_DIR':str(args.tools),'GRAFANA_URL':'http://127.0.0.1:1'},
                args.app_package,checksum(args.app_package),allow_unsigned=True)
            plugin_dir=args.output/'grafana-plugins'
        else:
            plugin_dir=args.output/'plugins';plugin_dir.mkdir()
            (plugin_dir/'xlayer-telemetry-app').symlink_to(ROOT/'grafana/xlayer-app/dist',target_is_directory=True)
        grafana_home = args.tools/'grafana-v12.1.0'
        env={**os.environ,'GF_PATHS_DATA':str(args.output/'grafana'),'GF_PATHS_LOGS':str(args.output/'grafana-logs'),
             'GF_PATHS_PROVISIONING':str(provisioning),'GF_PATHS_PLUGINS':str(plugin_dir),
             'GF_SERVER_HTTP_ADDR':'127.0.0.1','GF_SERVER_HTTP_PORT':str(args.grafana_port),
             'GF_AUTH_ANONYMOUS_ENABLED':'true','GF_AUTH_ANONYMOUS_ORG_ROLE':'Viewer',
             'GF_USERS_DEFAULT_THEME':'light','GF_USERS_HOME_PAGE':'/a/xlayer-telemetry-app/overview','GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS':'xlayer-telemetry-app',
             'GF_ANALYTICS_REPORTING_ENABLED':'false','GF_ANALYTICS_CHECK_FOR_UPDATES':'false',
             'GF_PLUGINS_PREINSTALL_DISABLED':'true'}
        grafana_process = launch([str(grafana_home/'bin/grafana'),'server',f'--homepath={grafana_home}'], 'grafana',env)
        grafana=f'http://127.0.0.1:{args.grafana_port}'
        ready(grafana+'/api/health',grafana_process);ready(grafana+'/api/dashboards/uid/telemetry-overview',grafana_process)
        (args.output/'connection.json').write_text(json.dumps({'grafana':grafana,'prometheus':prom,'loki':loki,'data_origin':'synthetic'},indent=2))
        first_run = 'demo-qwen' if args.multi_job else 'verl-agent-demo'
        first_engine = '$__all' if args.multi_job else 'synthetic-vllm-0'
        print(f'LIVE DEMO {grafana}/a/xlayer-telemetry-app/overview?var-cluster=scenes-demo&var-run_id={first_run}&var-node=$__all&var-source_node=$__all&var-gpu=0&var-engine={first_engine}',flush=True)
        cycle=0
        while not stopping.is_set():
            if any(process.poll() is not None for process, _ in processes):
                raise RuntimeError('An owned demo service exited; inspect launcher logs')
            if args.multi_job:
                schedule = make_schedule(start=time.time()+2, node=demo.gpu['gpu_nodes'][0], cycle=cycle,
                                         replica_nodes=demo.gpu['gpu_nodes'][1:3])
                atomic_write_text(scenario_state, json.dumps(schedule))
                end = max(job['scenario']['frames'][1]['end'] for job in schedule['jobs'])
                print(f'MULTI-JOB cycle={cycle}: three overlapping jobs; completion in {end-time.time():.0f}s', flush=True)
                fixture=args.output/f'fixture-{cycle}'
                waiting=list(schedule['jobs']); active={}
                with ThreadPoolExecutor(max_workers=3) as analyses:
                    while (waiting or active) and not stopping.is_set():
                        if any(p.poll() is not None for p, _ in processes): raise RuntimeError('Owned demo service exited')
                        for job in waiting[:]:
                            source_end=job['scenario']['frames'][1]['end']
                            if time.time() < source_end + 3: continue
                            future=analyses.submit(collect_and_analyze_job,fixture/job['scenario']['run_id'],job,prom)
                            active[future]=(job,time.time()); waiting.remove(job)
                        for future in list(active):
                            if not future.done(): continue
                            job,submitted=active.pop(future)
                            streams,report=future.result()
                            request(loki+'/loki/api/v1/push',{'streams':streams})
                            publication={'run_id':report['run_id'],'step':report['step'],
                                'source_end':report['analysis_window']['end'],'submitted_at':submitted,'published_at':time.time(),
                                'analysis_status':report.get('analysis_execution',{}).get('status')}
                            append_jsonl(fixture/'publications.jsonl',json.dumps(publication)+'\n')
                            print('PUBLISHED MULTI-JOB '+json.dumps(publication),flush=True)
                        if waiting or active: stopping.wait(.2)
                if stopping.is_set(): break
                from xlayer_telemetry.operations.runs import publish
                publish(fixture, args.output / 'dashboards')
                print(f'COMPLETED MULTI-JOB cycle={cycle} runs=3; actual Prometheus diagnosis; attribution unverified',flush=True)
                cycle+=1
                if stopping.wait(4):break
                continue
            current = ('storage-regression' if cycle % 2 == 0 else 'normal') if args.scenario == 'alternating' else args.scenario
            schedule = make_scenario(start=time.time()+2, run_id='verl-agent-demo', node=demo.gpu['gpu_nodes'][0],
                                     step=127+2*cycle, current=current)
            atomic_write_text(scenario_state, json.dumps(schedule))
            print(f"SCENARIO {current}: baseline Step {schedule['frames'][0]['step']} -> Step {schedule['frames'][1]['step']}; "
                  f"first completed comparison in {schedule['frames'][1]['end']-time.time():.0f}s", flush=True)
            # Let the existing Prometheus scrape process observe actual live phases.
            # No backdated Prometheus samples or pre-completed observations are made.
            while not stopping.is_set() and time.time() < schedule['frames'][1]['end'] + 2:
                if any(process.poll() is not None for process, _ in processes):
                    raise RuntimeError('An owned demo service exited during scenario execution')
                stopping.wait(min(1, max(.01, schedule['frames'][1]['end']+2-time.time())))
            if stopping.is_set():
                break
            directory=args.output/f'fixture-{cycle}'
            report=generate(directory,run_id='verl-agent-demo',node=demo.gpu['gpu_nodes'][0],scenario=schedule,storage_series=args.storage_series,publish_diagnosis=False,
                            rollout_workers=[CorrelationContext(run_id='verl-agent-demo', producer='demo_distributed', role='rollout',
                                worker_id=f'rollout-{index}', node=demo.gpu['gpu_nodes'][index % len(demo.gpu['gpu_nodes'])],
                                gpu=str(index % demo.gpu['gpus_per_node'])) for index in range(4)] if args.multi_worker else None)
            # Read the already-scraped synthetic native endpoints through the
            # real diagnosis/query path. No connector/client numbers are inferred
            # from illustrative 3FS values, and no ClickHouse backend is declared.
            steps=[json.loads(line) for line in (directory/'telemetry-events/verl-steps.jsonl').read_text().splitlines()]
            selected=next(row for row in steps if row.get('record_id')==report['trigger_record_id'])
            node=demo.gpu['gpu_nodes'][0]
            native_report=DiagnosticEngine({'cluster':'scenes-demo','rollout_node':node,
                'clock':{'monitoring_node':node},'sampling':{'check_source_freshness':True},
                'prometheus':{'url':prom,'metric_profiles':['mooncake','mooncake_storage'],'mooncake_master_node':node}}).analyze(selected,steps)
            report['storage_overview']={**native_report['storage_overview'],'data_origin':'synthetic',
                'query_execution':native_report['query_execution']}
            write_report(directory/'diagnostics',report)
            streams=[]
            files=[('verl_step',directory/'telemetry-events/verl-steps.jsonl')]+[('xlayer_event',p) for p in (directory/'telemetry-events').glob('*.jsonl') if p.name!='verl-steps.jsonl']
            for source, path in files:
                if path is None: continue
                by_node={}
                for i,line in enumerate(path.read_text().splitlines()):
                    row=json.loads(line); stamp=row.get('event_time_unix_nano',int(float(row.get('observed_at',time.time()))*1e9))
                    by_node.setdefault(row.get('node', demo.gpu['gpu_nodes'][0]), []).append([str(int(stamp)+i),line])
                for node, values in by_node.items():
                    if values:streams.append({'stream':{'signal':source,'cluster':'scenes-demo','node':node,'data_origin':'synthetic'},'values':values})
            rows=_investigation_rows(report)
            streams.append({'stream':{'signal':'xlayer_diagnosis','cluster':'scenes-demo','node':demo.gpu['gpu_nodes'][0],'data_origin':'synthetic'},'values':[[str(int(row['window_end_ms'])*1_000_000-len(rows)+i),json.dumps(row)] for i,row in enumerate(rows)]})
            streams.append({'stream':{'cluster':'scenes-demo','node':demo.gpu['gpu_nodes'][0],'data_origin':'synthetic'},'values':[[str(int(rows[0]['window_end_ms'])*1_000_000),json.dumps({'run_id':'verl-agent-demo','log_file':'agent.log','_entry':(directory/'logs/agent.log').read_text()})]]})
            request(loki+'/loki/api/v1/push',{'streams':streams})
            print(f"COMPLETED Step {report['step']} ({current}); Prometheus + SDK spans share scenario clocks", flush=True)
            cycle+=1
            if stopping.wait(4):break
    finally:
        for process,log in reversed(processes):
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill();process.wait()
            log.close()

if __name__=='__main__': main()
