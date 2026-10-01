"""Slow storage, saturation, mutable input, concurrency, failures and fork."""
import json
import os
from pathlib import Path
import select
import signal
import threading
import time

import pytest

from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.io_writer import BoundedWriter
from xlayer_telemetry.metrics.emitter import Metric, MetricEmitter


CONTEXT = CorrelationContext(run_id='r',producer='agent',role='rollout',worker_id='0',node='n')


def test_event_queue_never_waits_for_slow_storage_and_close_is_bounded(tmp_path,monkeypatch):
    entered, release = threading.Event(), threading.Event()
    persisted = []
    def blocked(self,line):
        entered.set()
        release.wait(5)
        persisted.append(json.loads(line))
    monkeypatch.setattr(EventRecorder,'_persist',blocked)
    recorder = EventRecorder(tmp_path,CONTEXT,async_io=True,queue_capacity=2,flush_timeout=.02)
    try:
        recorder.event('tool.result',phase='environment',attributes={'number':0})
        assert entered.wait(1)
        started = time.monotonic()
        for number in range(1,5):
            recorder.event('tool.result',phase='environment',attributes={'number':number})
        assert time.monotonic()-started < .5
        assert recorder.io_status()['queued'] == 2
        assert recorder.io_status()['dropped'] == 2
        assert not recorder.close()
        assert recorder.io_status()['dropped'] == 4
        assert recorder.io_status()['flush_timeouts'] == 1
    finally:
        release.set()
        assert recorder.flush(1)
    assert [record['attributes']['number'] for record in persisted] == [0]


def test_events_are_serialized_before_enqueue_and_flush_preserves_spans(tmp_path):
    recorder = EventRecorder(tmp_path,CONTEXT,async_io=True,queue_capacity=300)
    details = {'nested':{'value':1}}
    recorder.event('tool.result',phase='environment',attributes=details)
    details['nested']['value'] = 9
    def record(index):
        with recorder.span('tool.call',phase='environment',attributes={'tool':str(index)}):
            pass
    workers = [threading.Thread(target=lambda n=n:[record(n*20+i) for i in range(20)]) for n in range(5)]
    for worker in workers: worker.start()
    for worker in workers: worker.join()
    assert recorder.close(2)
    rows = [json.loads(line) for line in recorder.path.read_text().splitlines()]
    assert rows[0]['attributes']['nested']['value'] == 1
    assert len(rows) == 101 and len({r['span_id'] for r in rows[1:]}) == 100
    assert recorder.io_status()['written'] == 101
    assert recorder.io_status()['dropped'] == 0


def test_async_metrics_coalesce_queued_snapshots_to_latest_step(tmp_path,monkeypatch):
    import xlayer_telemetry.metrics.emitter as module
    entered, release = threading.Event(), threading.Event()
    original = module.atomic_write_text
    calls = []
    def blocked(path,text):
        calls.append(text)
        if len(calls) == 1:
            entered.set()
            release.wait(5)
        original(path,text)
    monkeypatch.setattr(module,'atomic_write_text',blocked)
    emitter = MetricEmitter(tmp_path,run_id='r',producer='app',role='trainer',worker_id='0',node='n',
                            async_io=True,queue_capacity=1)
    try:
        destination = emitter.emit(step=0,samples=[Metric('duration',0)])
        assert entered.wait(1)
        for step in range(1,101):
            emitter.emit(step=step,samples=[Metric('duration',step)])
        assert emitter.io_status()['queued'] == 1
        assert emitter.io_status()['dropped'] == 99
        release.set()
        assert emitter.close(2)
        assert json.loads(destination.read_text())['step'] == 100
        assert emitter.io_status()['written'] == 2
    finally:
        release.set()
        emitter.close(1)


def test_background_disk_failure_is_isolated_and_counted(tmp_path,monkeypatch):
    def failed(self,line):
        raise OSError('No space left on device')
    monkeypatch.setattr(EventRecorder,'_persist',failed)
    recorder = EventRecorder(tmp_path,CONTEXT,async_io=True)
    recorder.event('tool.result',phase='environment')
    assert recorder.flush(1)
    assert recorder.disabled
    assert recorder.io_status()['write_errors'] == 1
    assert recorder.io_status()['dropped'] == 1
    recorder.event('tool.result',phase='environment')  # Workload continues.
    recorder.close()


def test_queue_byte_limit_bounds_large_serialized_records():
    entered, release = threading.Event(), threading.Event()
    values = []
    def write(value):
        entered.set()
        release.wait(3)
        values.append(value)
    writer = BoundedWriter(write,lambda _:None,capacity=100,max_bytes=5)
    try:
        assert writer.submit('1234')
        assert entered.wait(1)
        assert writer.submit('5678') and writer.submit('9')
        assert not writer.submit('ab') and not writer.submit('123456')
        assert writer.status()['queued_bytes'] == 5
        assert writer.status()['dropped'] == 2
        release.set()
        assert writer.close(1)
        assert values == ['1234','5678','9']
        assert writer.status()['queued_bytes'] == 0
    finally:
        release.set()
        writer.close(1)


@pytest.mark.parametrize('capacity,timeout',[(0,1),(True,1),(1,-1),(1,float('nan'))])
def test_async_queue_settings_are_validated(capacity,timeout):
    with pytest.raises(ValueError):
        BoundedWriter(lambda _:None,lambda _:None,capacity=capacity,flush_timeout=timeout)


def test_from_env_opt_in_preserves_default_sync_mode(tmp_path,monkeypatch):
    monkeypatch.setenv('TELEMETRY_EVENTS_DIR',str(tmp_path))
    monkeypatch.setenv('TELEMETRY_METRICS_DIR',str(tmp_path))
    monkeypatch.setenv('TELEMETRY_RUN_ID','r')
    monkeypatch.delenv('TELEMETRY_ASYNC_IO',raising=False)
    assert EventRecorder.from_env(producer='app',role='worker').io_status()['mode'] == 'synchronous'
    monkeypatch.setenv('TELEMETRY_ASYNC_IO','1')
    event = EventRecorder.from_env(producer='app',role='worker')
    metric = MetricEmitter.from_env(producer='app',role='worker')
    assert event.io_status()['mode'] == metric.io_status()['mode'] == 'asynchronous'
    event.close()
    metric.close()
    monkeypatch.setenv('TELEMETRY_IO_QUEUE_CAPACITY','0')
    assert EventRecorder.from_env(producer='app',role='worker') is None


@pytest.mark.skipif(not hasattr(os,'fork'),reason='requires POSIX fork')
@pytest.mark.filterwarnings('ignore:This process.*multi-threaded.*:DeprecationWarning')
def test_fork_discards_inherited_queue_and_resets_held_writer_lock(tmp_path,monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = EventRecorder._persist
    owner_pid = os.getpid()
    def blocked(self,line):
        if os.getpid() == owner_pid:
            entered.set()
            release.wait(5)
        original(self,line)
    monkeypatch.setattr(EventRecorder,'_persist',blocked)
    recorder = EventRecorder(tmp_path,CONTEXT,async_io=True)
    recorder.event('parent.event',phase='environment')
    assert entered.wait(1)
    read_fd, write_fd = os.pipe()
    # Deliberately fork with the parent writer's condition locked.
    with recorder._writer._condition:
        pid = os.fork()
        if pid == 0:
            try:
                os.close(read_fd)
                recorder.event('child.event',phase='environment')
                drained = recorder.close(1)
                os.write(write_fd,json.dumps({'drained':drained,**recorder.io_status()}).encode())
                os._exit(0)
            except BaseException:
                os._exit(1)
    os.close(write_fd)
    try:
        ready,_,_ = select.select([read_fd],[],[],3)
        assert ready, 'child SDK writer inherited a locked condition'
        result = json.loads(os.read(read_fd,4096))
        assert result['drained'] and result['fork_discarded'] == 1
        _,status = os.waitpid(pid,0)
        pid = None
        assert status == 0
    finally:
        if pid is not None:
            os.kill(pid,signal.SIGKILL)
            os.waitpid(pid,0)
        os.close(read_fd)
        release.set()
        recorder.close(2)
    rows = [json.loads(line)['name'] for line in recorder.path.read_text().splitlines()]
    assert sorted(rows) == ['child.event','parent.event']
