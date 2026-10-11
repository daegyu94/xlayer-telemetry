"""A worker-lifetime recorder for repeatedly invoked external tool adapters.

Create one handle per producer at module scope. Environment configuration is
read on first use in each PID; a changed context waits for the old writer to
drain before replacement. Async writers use the SDK's existing bounded
process-exit drain; close() can end a worker's instrumentation earlier.
"""
import os
import threading
import weakref

from .events import EventRecorder

_HANDLES = weakref.WeakSet()
_CONTEXT_KEYS = ('TELEMETRY_EVENTS_DIR', 'TELEMETRY_RUN_ID', 'TELEMETRY_NODE',
                 'RANK', 'LOCAL_RANK', 'CUDA_VISIBLE_DEVICES',
                 'TELEMETRY_TIME_CALIBRATION_FILE', 'TELEMETRY_ASYNC_IO',
                 'TELEMETRY_IO_QUEUE_CAPACITY', 'TELEMETRY_IO_QUEUE_BYTES',
                 'TELEMETRY_IO_FLUSH_TIMEOUT')


class WorkerEventRecorder:
    def __init__(self, *, producer, role):
        self.producer, self.role = producer, role
        self._reset()
        _HANDLES.add(self)

    def _reset(self):
        self._lock = threading.Lock()
        self._recorder = None
        self._closed = False
        self._context = None

    def get(self):
        with self._lock:
            if self._closed:
                return None
            context = tuple(os.environ.get(key) for key in _CONTEXT_KEYS)
            if self._recorder is not None and context != self._context:
                # A reused worker must not label a new job with the old Run.
                # Do not create more writers while the old I/O is still stuck.
                if not self._recorder.close(0):
                    return None
                self._recorder = None
            if self._recorder is None:
                self._context = context
                self._recorder = EventRecorder.from_env(
                    producer=self.producer, role=self.role, worker_id=str(os.getpid()))
            return self._recorder

    def close(self, timeout=None):
        with self._lock:
            self._closed = True
            recorder = self._recorder
        return recorder.close(timeout) if recorder is not None else True


def _after_fork():
    # Reset before the child's threads can touch an inherited locked handle.
    # Never flush a parent's queue from the child or reuse its worker identity.
    for handle in list(_HANDLES):
        handle._reset()


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_fork)
