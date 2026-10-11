"""Exercise registered public tools under blocked persistence and concurrency."""
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from xlayer_telemetry.events import EventRecorder

ROOT = Path(__file__).parents[1]


def load_tools(tmp_path, monkeypatch):
    registry = {}
    api = ModuleType('verl.tools.function_tool')
    def register(name):
        def wrap(fn):
            registry[name] = SimpleNamespace(fn=fn)
            return fn
        return wrap
    api.function_tool = register
    api.FUNCTION_TOOL_REGISTRY = registry
    monkeypatch.setitem(sys.modules, api.__name__, api)
    source = tmp_path / 'original.py'
    source.write_text('''from verl.tools.function_tool import function_tool
@function_tool('read_source')
def read_source(path):
 if path == 'fail': raise ValueError('workload failure')
 return 'source:'+path
@function_tool('test_patch')
def test_patch(patch): return patch
def compute_score(*args): return 1
''')
    monkeypatch.setenv('VERL_LAB_TOOLS_PATH', str(source))
    monkeypatch.setenv('TELEMETRY_EVENTS_DIR', str(tmp_path / 'events'))
    monkeypatch.setenv('TELEMETRY_RUN_ID', 'tool-run')
    monkeypatch.setenv('TELEMETRY_NODE', 'worker-node')
    spec = importlib.util.spec_from_file_location('review_swe_tools', ROOT / 'examples/sandbox/verl_lab_swebench_tools.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, registry


def test_registered_tools_share_bounded_writer_during_persist_stall(tmp_path, monkeypatch):
    monkeypatch.setenv('TELEMETRY_ASYNC_IO', '1')
    monkeypatch.setenv('TELEMETRY_IO_QUEUE_CAPACITY', '2')
    module, registry = load_tools(tmp_path, monkeypatch)
    entered = threading.Event(); release = threading.Event()
    writers = set(); recorders = []
    original_init = EventRecorder.__init__
    def initialize(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        recorders.append(self)
    original_write = EventRecorder._persist
    def blocked(self, *args, **kwargs):
        writers.add(threading.get_ident()); entered.set()
        assert release.wait(8), 'test persistence not released'
        return original_write(self, *args, **kwargs)
    monkeypatch.setattr(EventRecorder, '__init__', initialize)
    monkeypatch.setattr(EventRecorder, '_persist', blocked)
    try:
        fn = registry['read_source'].fn
        with ThreadPoolExecutor(max_workers=8) as pool:
            answers = list(pool.map(fn, [str(i) for i in range(24)]))
        assert answers == ['source:'+str(i) for i in range(24)]
        assert entered.wait(1)
        assert len(recorders) == 1, 'each invocation allocated an independent async queue'
        assert len(writers) == 1
        status = recorders[0].io_status()
        assert status['queued'] <= 2 and status['dropped'] >= 21
        import pytest
        with pytest.raises(ValueError, match='workload failure'): fn('fail')
        assert json.loads(fn())['error'] == 'invalid_tool_arguments'
    finally:
        release.set()
        for recorder in recorders: assert recorder.close(2)


def test_worker_context_change_does_not_relabel_or_multiply_blocked_writers(tmp_path, monkeypatch):
    from xlayer_telemetry.worker_events import WorkerEventRecorder
    monkeypatch.setenv('TELEMETRY_EVENTS_DIR', str(tmp_path))
    monkeypatch.setenv('TELEMETRY_RUN_ID', 'first')
    monkeypatch.setenv('TELEMETRY_ASYNC_IO', '1')
    entered = threading.Event(); release = threading.Event()
    original = EventRecorder._persist
    def blocked(self, line):
        entered.set(); assert release.wait(5)
        original(self, line)
    monkeypatch.setattr(EventRecorder, '_persist', blocked)
    handle = WorkerEventRecorder(producer='agent', role='rollout')
    first = handle.get()
    try:
        first.event('test.first', phase='rollout')
        assert entered.wait(1)
        monkeypatch.setenv('TELEMETRY_RUN_ID', 'second')
        for _ in range(20): assert handle.get() is None
        release.set(); assert first.flush(1)
        second = handle.get()
        assert second.context.run_id == 'second' and second is not first
        second.event('test.second', phase='rollout')
        assert handle.close(1)
        assert handle.get() is None
        rows = [json.loads(line) for path in tmp_path.glob('*.jsonl') for line in path.read_text().splitlines()]
        assert {(r['run_id'], r['name']) for r in rows} == {('first','test.first'), ('second','test.second')}
    finally:
        release.set(); handle.close(1)


def test_registered_tool_after_fork_uses_child_identity_and_one_writer(tmp_path):
    import os
    import signal
    import subprocess
    import pytest
    if not hasattr(os, 'fork'): pytest.skip('POSIX fork required')
    script = r'''
import json,os,sys,threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from pytest import MonkeyPatch
from tests.test_worker_tool_recorders import load_tools
m=MonkeyPatch();m.setenv('TELEMETRY_ASYNC_IO','1')
module,registry=load_tools(Path(sys.argv[1]),m)
fn=registry['read_source'].fn
assert fn('parent')=='source:parent'
assert module._AGENT_EVENTS.get().flush(1)
locked=threading.Event();release=threading.Event()
def hold():
 with module._AGENT_EVENTS._lock:locked.set();release.wait(5)
t=threading.Thread(target=hold);t.start();assert locked.wait(1)
pid=os.fork()
if pid==0:
 try:
  with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(fn,['a','b']))==['source:a','source:b']
  recorder=module._AGENT_EVENTS.get()
  assert recorder.context.worker_id==str(os.getpid())
  assert module._AGENT_EVENTS.close(1)
  assert recorder.io_status()['written']==2
  os._exit(0)
 except BaseException:
  import traceback;traceback.print_exc();os._exit(1)
release.set();t.join(1)
_,status=os.waitpid(pid,0);assert status==0
assert module._AGENT_EVENTS.close(1)
rows=[json.loads(line) for p in (Path(sys.argv[1])/'events').glob('*.jsonl') for line in p.read_text().splitlines()]
assert len(rows)==3
assert sorted(r['worker_id'] for r in rows)==sorted([str(os.getpid()),str(pid),str(pid)])
'''
    process = subprocess.Popen([sys.executable, '-c', script, str(tmp_path)], cwd=ROOT,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stdout + stderr
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
