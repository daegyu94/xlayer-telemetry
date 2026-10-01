"""Optional bounded background writes; no telemetry backend or disk spool."""
from __future__ import annotations

import atexit
from collections import deque
import math
import os
import threading
import time
import weakref


_LIVE = weakref.WeakSet()


def _shutdown():
    # Bound the whole SDK shutdown, not timeout multiplied by recorder count.
    end = time.monotonic() + 1
    for writer in list(_LIVE):
        writer.close(timeout=max(0, min(writer.flush_timeout, end-time.monotonic())))


atexit.register(_shutdown)


def settings_from_env() -> dict:
    enabled = os.environ.get("TELEMETRY_ASYNC_IO", "0")
    if enabled not in {"0", "1"}:
        raise ValueError("TELEMETRY_ASYNC_IO must be 0 or 1")
    if enabled == "0":
        return {}
    return {"async_io": True,
            "queue_capacity": int(os.environ.get("TELEMETRY_IO_QUEUE_CAPACITY", "256")),
            "max_queue_bytes": int(os.environ.get("TELEMETRY_IO_QUEUE_BYTES", str(4 * 1024 * 1024))),
            "flush_timeout": float(os.environ.get("TELEMETRY_IO_FLUSH_TIMEOUT", "1"))}


class BoundedWriter:
    """FIFO events or coalesced snapshots with explicit drop and error counts.

    The callback may block in filesystem I/O, but submit/flush/close do not wait
    indefinitely for it. A daemon thread may finish its in-flight write after
    close times out. Callers serialize records before submitting them.
    """

    def __init__(self, write, on_error, *, capacity=256, max_bytes=4 * 1024 * 1024,
                 flush_timeout=1, latest_only=False):
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("queue_capacity must be a positive integer")
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_queue_bytes must be a positive integer")
        if type(flush_timeout) not in (int, float) or not math.isfinite(flush_timeout) or flush_timeout < 0:
            raise ValueError("flush_timeout must be finite and nonnegative")
        self.write, self.on_error = write, on_error
        self.capacity, self.flush_timeout, self.latest_only = capacity, flush_timeout, latest_only
        self.max_bytes = max_bytes
        self._pid = os.getpid()
        self._condition = threading.Condition()
        self._queue = deque()
        self._queued_bytes = 0
        self._thread = None
        self._inflight = False
        self._closed = self._failed = False
        self._counts = dict(accepted=0, written=0, dropped=0, write_errors=0, flush_timeouts=0, fork_discarded=0)
        _LIVE.add(self)

    def _check_pid(self):
        if self._pid == os.getpid():
            return
        # A fork inherits data and locks, not the writer thread. Never acquire
        # an inherited lock or replay a parent's buffered events in the child.
        discarded = len(self._queue) + int(self._inflight)
        self._pid = os.getpid()
        self._condition = threading.Condition()
        self._queue = deque()
        self._queued_bytes = 0
        self._thread = None
        self._inflight = False
        self._counts = dict(accepted=0, written=0, dropped=0, write_errors=0, flush_timeouts=0, fork_discarded=discarded)

    def submit(self, item) -> bool:
        self._check_pid()
        # SDK JSON uses ensure_ascii=True: serialized characters equal bytes.
        size = len(item[1]) if isinstance(item, tuple) else len(item)
        with self._condition:
            if self._closed or self._failed or size > self.max_bytes:
                self._counts["dropped"] += 1
                return False
            if self.latest_only and self._queue:
                self._counts["dropped"] += len(self._queue)
                self._queue.clear()  # Never replace the write already in flight.
                self._queued_bytes = 0
            if len(self._queue) >= self.capacity or self._queued_bytes + size > self.max_bytes:
                self._counts["dropped"] += 1
                return False
            self._queue.append((item, size))
            self._queued_bytes += size
            self._counts["accepted"] += 1
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True, name="xlayer-sdk-writer")
                try:
                    self._thread.start()
                except (RuntimeError, OSError) as error:
                    self._failed = True
                    self._counts["write_errors"] += 1
                    self._counts["dropped"] += len(self._queue)
                    self._queue.clear()
                    self._queued_bytes = 0
                    self._thread = None
                    self.on_error(error)
                    return False
            self._condition.notify_all()
            return True

    def _run(self):
        while True:
            with self._condition:
                if not self._queue and not self._closed:
                    self._condition.wait(timeout=1)
                if not self._queue:
                    self._thread = None  # Restart lazily after an idle interval.
                    self._condition.notify_all()
                    return
                item, size = self._queue.popleft()
                self._queued_bytes -= size
                self._inflight = True
            try:
                self.write(item)
            except Exception as error:
                with self._condition:
                    self._failed = True
                    self._counts["write_errors"] += 1
                    self._counts["dropped"] += len(self._queue) + 1
                    self._queue.clear()
                    self._queued_bytes = 0
                self.on_error(error)
            else:
                with self._condition:
                    self._counts["written"] += 1
            finally:
                with self._condition:
                    self._inflight = False
                    self._condition.notify_all()
            if self._failed:
                with self._condition:
                    self._thread = None
                return

    def flush(self, timeout=None) -> bool:
        self._check_pid()
        timeout = self.flush_timeout if timeout is None else timeout
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("flush timeout must be finite and nonnegative")
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._queue or self._inflight:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    self._counts["flush_timeouts"] += 1
                    return False
                self._condition.wait(timeout=remaining)
            return True

    def close(self, timeout=None) -> bool:
        self._check_pid()
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        drained = self.flush(timeout)
        with self._condition:
            self._counts["dropped"] += len(self._queue)
            self._queue.clear()
            self._queued_bytes = 0
            self._condition.notify_all()
        return drained

    def status(self) -> dict:
        self._check_pid()
        with self._condition:
            return {"mode": "asynchronous", **self._counts,
                    "queued": len(self._queue), "inflight": self._inflight,
                    "queued_bytes": self._queued_bytes,
                    "closed": self._closed, "failed": self._failed}
