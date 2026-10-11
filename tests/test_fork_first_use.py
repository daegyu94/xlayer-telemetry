"""SDK fork reset must complete before child threads can share a recorder."""
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest


@pytest.mark.skipif(not hasattr(os,'fork'),reason='POSIX fork required')
def test_locked_parent_condition_and_simultaneous_child_first_calls(tmp_path):
    script = r'''
import json,os,threading,time,sys
from pathlib import Path
from xlayer_telemetry.events import EventRecorder,CorrelationContext
parent=os.getpid();blocked=threading.Event();release=threading.Event();locked=threading.Event()
original=EventRecorder._persist
child_writers=[]
def persist(self,line):
    if os.getpid()==parent:blocked.set();release.wait(5)
    else:child_writers.append(threading.get_ident())
    original(self,line)
EventRecorder._persist=persist
r=EventRecorder(Path(sys.argv[1]),CorrelationContext('r','sdk','worker','0','n'),async_io=True)
r.event('parent.one',phase='test');assert blocked.wait(1)
r.event('parent.two',phase='test')
condition=r._writer._condition
class Proxy:
    entered=None
    def __enter__(self):
        if self.entered is not None:self.entered.set()
        return condition.__enter__()
    def __exit__(self,*args):return condition.__exit__(*args)
    def __getattr__(self,name):return getattr(condition,name)
proxy=Proxy();r._writer._condition=proxy
unlock=threading.Event()
def own_lock():
    with condition:locked.set();unlock.wait(5)
t=threading.Thread(target=own_lock);t.start();assert locked.wait(1)
pid=os.fork()
if pid==0:
    try:
        old_entered=threading.Event();first_reset=threading.Event();proxy.entered=old_entered
        inherited=r._writer._pid!=os.getpid()
        real_condition=threading.Condition
        def paused_condition(*args,**kwargs):
            if threading.current_thread().name=='first':
                first_reset.set();assert old_entered.wait(1)
            return real_condition(*args,**kwargs)
        def first():r.event('child.one',phase='test')
        def second():
            if inherited:assert first_reset.wait(1)
            r.event('child.two',phase='test')
        a=threading.Thread(target=first,name='first');b=threading.Thread(target=second,name='second')
        if inherited:threading.Condition=paused_condition
        a.start();b.start();a.join(2);b.join(2)
        assert not a.is_alive() and not b.is_alive(),'first-use fork deadlock'
        assert r.close(1)
        assert r.io_status()['fork_discarded']==2
        assert len(child_writers)==2 and len(set(child_writers))==1
        os._exit(0)
    except BaseException:
        import traceback;traceback.print_exc();os._exit(1)
_,status=os.waitpid(pid,0)
unlock.set();t.join(1);release.set();assert r.close(1)
assert os.waitstatus_to_exitcode(status)==0
names=[json.loads(line)['name'] for line in r.path.read_text().splitlines()]
assert sorted(names)==['child.one','child.two','parent.one','parent.two'],names
'''
    process=subprocess.Popen([sys.executable,'-c',script,str(tmp_path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    try:
        stdout,stderr=process.communicate(timeout=10)
        assert process.returncode==0,stderr.decode()
    finally:
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGKILL);process.communicate(timeout=3)
