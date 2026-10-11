"""One owned Grafana, all scenarios selected/started/reset through its Explorer UI."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import urllib.request
from ci_demo import ROOT,SCRIPTS,stop_owned,bounded_tail
from live_demo import wait_for_service
sys.path.insert(0,str(ROOT))
from xlayer_telemetry.demos.journeys import SCENARIO_IDS


def run(args):
    args.output.mkdir(parents=True,exist_ok=False)
    if args.state.exists():raise ValueError('--state must be a new disposable directory')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    process=None;validator=None;report={'scenarios':args.scenarios,'status':'starting','hardware':'pending'}
    try:
        with (args.output/'scenario-lab.log').open('w') as log:
            process=subprocess.Popen([sys.executable,str(SCRIPTS/'scenario_lab.py'),'--tools',str(args.tools.resolve()),
                '--output',str(args.state.resolve()),'--grafana-port',str(port)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            origin=f'http://127.0.0.1:{port}'
            wait_for_service(origin+'/api/plugin-proxy/xlayer-telemetry-app/demo-control/status',process,threading.Event(),
                lambda url:urllib.request.urlopen(url,timeout=3).read(),timeout=120)
            report['grafana']=origin
            validator=subprocess.Popen([sys.executable,str(SCRIPTS/'lab_validate.py'),'--url',origin,'--output',str(args.output/'browser'),
                '--scenarios',*args.scenarios,*(['--browser',args.browser] if args.browser else [])],cwd=ROOT,start_new_session=True)
            code=validator.wait(timeout=350*len(args.scenarios)+90)
            if code:raise RuntimeError(f'Scenario Explorer journey exited {code}; inspect browser/validation.json')
            report['status']='passed'
    except Exception as error:
        report.update(status='failed',error=str(error)[:2000]);raise
    finally:
        if validator is not None:stop_owned(validator,whole_group=True)
        if process is not None:stop_owned(process)
        report['owned_processes_stopped']=True
        if (args.state/'grafana.log').exists():(args.output/'grafana-tail.log').write_text(bounded_tail(args.state/'grafana.log'))
        (args.output/'validation.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools',required=True,type=Path);parser.add_argument('--state',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path);parser.add_argument('--browser')
    parser.add_argument('--scenarios',nargs='+',choices=SCENARIO_IDS,default=list(SCENARIO_IDS))
    run(parser.parse_args())
