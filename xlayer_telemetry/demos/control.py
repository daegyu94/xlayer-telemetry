"""Demo-only serial scenario coordinator and generation-safe datasource proxy.

A new replay gets fresh backend stores and Run identities. The single Grafana
and its datasource UIDs stay stable. This module is never loaded by the SDK.
"""
from __future__ import annotations

import json
from pathlib import Path
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .journeys import SCENARIOS, SCENARIO_IDS, BOUNDARY_TESTS


class ScenarioControl:
    def __init__(self):
        self.lock=threading.RLock()
        self.changed=threading.Event()
        self.generation=0
        self.selected=None
        self.state='idle'
        self.action='reset'
        self.validation={}
        self.backend=None
        self.catalog=None
        self.error=None

    def command(self,action,scenario,expected):
        with self.lock:
            if type(expected) is not int or expected!=self.generation:
                raise RuntimeError('Scenario changed; refresh before replaying')
            if action not in {'start','replay','stop','reset'} or action in {'start','replay'} and scenario not in SCENARIO_IDS:
                raise ValueError('Unknown synthetic scenario command')
            self.generation+=1
            self.action=action
            if action in {'start','replay'}:self.selected=scenario
            elif action=='reset':self.selected=None
            self.state='starting' if action in {'start','replay'} else 'stopping' if action=='stop' else 'resetting'
            self.backend=None;self.error=None
            if action!='stop':self.catalog=None;self.validation={}
            self.changed.set()
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            catalog=self.catalog or {}
            rows=[]
            for observation in catalog.get('observations',[]):
                rows.append({**observation,'context':{**observation['context'],'demo_generation':str(self.generation)}})
            return {'schema_version':1,'data_origin':'synthetic','generation':self.generation,
                    'selected':self.selected or '', 'action':self.action,'validation':self.validation, 'cycle':catalog.get('cycle',0),'status':self.state,
                    'scenarios':list(SCENARIOS),'observations':rows,'boundary_tests':BOUNDARY_TESTS,
                    'error':self.error,'limitations':'Isolated synthetic backend per replay; one Grafana. No physical GPU, multi-node, RDMA or 3FS validation.'}

    def publish(self,generation,*,backend=None,catalog=None,state=None,error=None,validation=None):
        with self.lock:
            if generation!=self.generation:return False
            if backend is not None:self.backend=backend
            if catalog is not None:self.catalog=catalog
            if state is not None:self.state=state
            if state in {'error','stopped','idle'}:self.backend=None
            if validation is not None:self.validation={**validation,**({'ui':self.validation['ui']} if 'ui' in self.validation else {})}
            self.error=error
            return True

    def target(self,source):
        with self.lock:
            if not self.backend or source not in {'prometheus','loki'}:
                raise RuntimeError('Synthetic backend is preparing; no previous replay is served')
            return self.generation,self.backend[source]


def control_server(control:ScenarioControl,port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,body,content_type='application/json'):
            self.send_response(status);self.send_header('Content-Type',content_type)
            self.send_header('Cache-Control','no-store');self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
        def payload(self):
            size=int(self.headers.get('Content-Length','0'))
            if not 0<=size<=1024:raise ValueError('Command body exceeds demo limit')
            value=json.loads(self.rfile.read(size))
            if not isinstance(value,dict):raise ValueError('Invalid command')
            return value
        def route(self):
            self.connection.settimeout(25)
            path=self.path.split('?',1)[0]
            if path=='/control/status' and self.command=='GET':
                self.send(200,json.dumps(control.snapshot()).encode());return
            if path=='/control/ui-result' and self.command=='POST':
                try:
                    value=self.payload()
                    with control.lock:
                        if value.get('generation')!=control.generation:raise RuntimeError('Replay changed before UI result')
                        checks=value.get('checks',[])
                        if value.get('state') not in {'passed','failed'} or not isinstance(checks,list) or len(checks)>12 or any(not isinstance(c,str) or len(c)>120 for c in checks):raise ValueError('Invalid browser result')
                        control.validation['ui']={'state':value['state'],'checks':checks,'message':'Reported by actual browser regression; not hardware validation'}
                    self.send(200,b'{}')
                except RuntimeError as error:self.send(409,json.dumps({'message':str(error)}).encode())
                except (ValueError,TypeError) as error:self.send(400,json.dumps({'message':str(error)}).encode())
                return
            if path in {'/control/start','/control/replay','/control/stop','/control/reset'} and self.command=='POST':
                try:
                    value=self.payload();result=control.command(path.rsplit('/',1)[1],value.get('scenario'),value.get('generation'))
                    self.send(202,json.dumps(result).encode())
                except RuntimeError as error:self.send(409,json.dumps({'message':str(error)}).encode())
                except (ValueError,TypeError) as error:self.send(400,json.dumps({'message':str(error)}).encode())
                return
            source,prefix=('prometheus','/prom') if path.startswith('/prom/') else ('loki','/logs')
            if not path.startswith(prefix+'/') or self.command not in {'GET','POST'}:
                self.send(404,b'{}');return
            try:
                generation,target=control.target(source)
                size=int(self.headers.get('Content-Length','0'))
                if not 0<=size<=1048576:raise ValueError('Query body exceeds demo limit')
                body=self.rfile.read(size) if size else None
                request=urllib.request.Request(target+self.path[len(prefix):],data=body,method=self.command,
                    headers={'Content-Type':self.headers.get('Content-Type','application/json')})
                try:
                    with urllib.request.urlopen(request,timeout=20) as response:
                        result=response.read(16777217);status=response.status;content_type=response.headers.get('Content-Type','application/json')
                except urllib.error.HTTPError as response:
                    result=response.read(1048576);status=response.code;content_type='application/json'
                if len(result)>16777216:raise ValueError('Query response exceeds demo limit')
                # Never publish an in-flight response from the reset generation.
                with control.lock:
                    if generation!=control.generation:raise RuntimeError('Replay changed while query was running')
                    self.send(status,result,content_type)
            except (OSError,ValueError,RuntimeError) as error:
                self.send(503,json.dumps({'message':str(error)}).encode())
        do_GET=route
        do_POST=route
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    return server
