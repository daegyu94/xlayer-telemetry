#!/usr/bin/env python3
"""One loopback Grafana; Scenario Explorer owns Run / Reset / Replay.

Only disposable demo backend processes and directories are created. No existing
monitoring config or workload is inspected, reset, or stopped.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import urllib.request

ROOT=Path(__file__).resolve().parents[3]
SCRIPTS=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from xlayer_telemetry.demos.control import ScenarioControl,control_server
from xlayer_telemetry.fileio import atomic_write_text
from xlayer_telemetry.grafana_release import DEFAULT_GRAFANA_VERSION,grafana_home,default_toggles,compatibility_config
from live_demo import validate_demo_app,wait_for_service
from ci_demo import stop_owned


def coordinate(control,args,stopping):
    child=None;log=None;generation=-1;state=None;read_failures=0
    try:
        while not stopping.is_set():
            snapshot=control.snapshot()
            if snapshot['generation']!=generation:
                if child is not None:stop_owned(child)
                if log is not None:log.close()
                child=None;generation=snapshot['generation'];scenario=snapshot['selected'];read_failures=0
                if scenario and snapshot['action'] in {'start','replay'}:
                    state=args.output/'replays'/f'g{generation}'
                    log=(args.output/f'replay-g{generation}.log').open('w')
                    command=[sys.executable,str(SCRIPTS/'live_demo.py'),'--backend-only','--tools',str(args.tools),
                             '--output',str(state),'--scenario',scenario,'--run-prefix',f'g{generation}-',
                             '--cycles','2' if scenario=='partial-telemetry' else '1']
                    if scenario_definition(scenario)['mode']=='single':command.append('--storage-series')
                    if scenario in {'slow-rollout','storage-regression'}:command.append('--multi-worker')
                    child=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    control.publish(generation,state='collecting')
                else:control.publish(generation,state='stopped' if snapshot['action']=='stop' else 'idle')
            if child is not None:
                if child.poll() is not None:
                    validation=state/'validation-state.json'
                    try:terminal=json.loads(validation.read_text()) if validation.exists() else {}
                    except (OSError,ValueError):terminal={}
                    control.publish(generation,state='error',validation=terminal,error='Synthetic backend exited. Inspect the failed stage and owned replay log.');child=None
                else:
                    try:
                        connection=state/'connection.json';catalog=state/'dashboards/xlayer-demo-scenarios.json'
                        validation=state/'validation-state.json'
                        if validation.exists():control.publish(generation,validation=json.loads(validation.read_text()))
                        if connection.exists():
                            endpoints=json.loads(connection.read_text())
                            if not isinstance(endpoints,dict) or any(not isinstance(endpoints.get(key),str) for key in ('prometheus','loki')):raise ValueError('Invalid connection snapshot')
                            control.publish(generation,backend=endpoints)
                        if catalog.exists():
                            data=json.loads(catalog.read_text())['xlayerDemoScenarios']
                            # Partial telemetry needs both fault and recovery pairs.
                            done=data['status']=='ready' and (snapshot['selected']!='partial-telemetry' or data['cycle']>=1)
                            control.publish(generation,catalog=data,state='ready' if done else 'collecting')
                        read_failures=0
                    except (OSError,ValueError,TypeError,KeyError):
                        read_failures+=1
                        if read_failures>=8:
                            stop_owned(child);child=None
                            control.publish(generation,state='error',error='Invalid replay snapshot after bounded retries; Replay starts a new isolated execution.')
            control.changed.wait(.25);control.changed.clear()
    finally:
        if child is not None:stop_owned(child)
        if log is not None:log.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--grafana-port',type=int,default=23400)
    args=parser.parse_args();args.output=args.output.resolve();args.tools=args.tools.resolve()
    if not 1<=args.grafana_port<=65535:parser.error('Invalid Grafana port')
    validate_demo_app(None,ROOT/'grafana/xlayer-app/dist')
    args.output.mkdir(parents=True,exist_ok=False)
    stopping=threading.Event()
    signal.signal(signal.SIGINT,lambda *_:stopping.set());signal.signal(signal.SIGTERM,lambda *_:stopping.set())
    control=ScenarioControl();server=control_server(control)
    proxy=f'http://127.0.0.1:{server.server_port}'
    server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
    coordinator=threading.Thread(target=coordinate,args=(control,args,stopping),daemon=True);coordinator.start()
    process=None;log=None
    try:
        provisioning=args.output/'provisioning'
        for folder in ('dashboards','datasources','plugins'):(provisioning/folder).mkdir(parents=True)
        dashboards=args.output/'dashboards'
        subprocess.run([sys.executable,str(ROOT/'scripts/provision_dashboards.py'),'--output',str(dashboards),'--enable-logs'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
        (provisioning/'dashboards/local.yaml').write_text(f'apiVersion: 1\nproviders:\n  - name: Scenario Lab\n    type: file\n    options:\n      path: {dashboards}\n')
        (provisioning/'plugins/local.yaml').write_text(f'apiVersion: 1\napps:\n  - type: xlayer-telemetry-app\n    org_id: 1\n    disabled: false\n    jsonData:\n      demoControlUrl: {proxy}/control\n')
        (provisioning/'datasources/local.yaml').write_text(f'''apiVersion: 1
datasources:
  - name: Synthetic replay Prometheus
    uid: telemetry-prometheus
    type: prometheus
    access: proxy
    url: {proxy}/prom
    jsonData:
      timeInterval: 2s
  - name: Synthetic replay Loki
    uid: telemetry-loki
    type: loki
    access: proxy
    url: {proxy}/logs
''')
        plugins=args.output/'plugins';plugins.mkdir();(plugins/'xlayer-telemetry-app').symlink_to(ROOT/'grafana/xlayer-app/dist',target_is_directory=True)
        home=grafana_home(args.tools,DEFAULT_GRAFANA_VERSION)
        config=args.output/'grafana.ini';config.write_text(compatibility_config(DEFAULT_GRAFANA_VERSION))
        env={**os.environ,'GF_PATHS_DATA':str(args.output/'grafana'),'GF_PATHS_LOGS':str(args.output/'grafana-logs'),
             'GF_PATHS_PROVISIONING':str(provisioning),'GF_PATHS_PLUGINS':str(plugins),
             'GF_SERVER_HTTP_ADDR':'127.0.0.1','GF_SERVER_HTTP_PORT':str(args.grafana_port),
             'GF_AUTH_ANONYMOUS_ENABLED':'true','GF_AUTH_ANONYMOUS_ORG_ROLE':'Viewer',
             'GF_FEATURE_TOGGLES_ENABLE':default_toggles(DEFAULT_GRAFANA_VERSION),
             'GF_USERS_HOME_PAGE':'/a/xlayer-telemetry-app/demo','GF_USERS_DEFAULT_THEME':'dark',
             'GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS':'xlayer-telemetry-app',
             'GF_ANALYTICS_REPORTING_ENABLED':'false','GF_ANALYTICS_CHECK_FOR_UPDATES':'false','GF_PLUGINS_PREINSTALL_DISABLED':'true'}
        log=(args.output/'grafana.log').open('w')
        process=subprocess.Popen([str(home/'bin/grafana'),'server',f'--homepath={home}',f'--config={config}'],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        grafana=f'http://127.0.0.1:{args.grafana_port}'
        wait_for_service(grafana+'/api/health',process,stopping,lambda url:urllib.request.urlopen(url,timeout=3).read())
        atomic_write_text(args.output/'connection.json',json.dumps({'grafana':grafana,'control':proxy,'data_origin':'synthetic'}))
        print(f'SCENARIO LAB {grafana}/a/xlayer-telemetry-app/demo',flush=True)
        while not stopping.is_set():
            if process.poll() is not None:raise RuntimeError('Owned Grafana exited')
            if not coordinator.is_alive():raise RuntimeError('Scenario coordinator exited')
            stopping.wait(1)
    finally:
        stopping.set();control.changed.set();coordinator.join(timeout=55)
        server.shutdown();server.server_close();server_thread.join(timeout=5)
        if process is not None:stop_owned(process)
        if log is not None:log.close()


if __name__=='__main__':main()
