"""Isolate rule analysis so its parent can enforce a wall-clock deadline."""
from __future__ import annotations

from datetime import datetime, timezone
import multiprocessing
from pathlib import Path
import signal
import threading
import time

from ..measurements import finite_number


def _worker(connection, config):
    # The controller handles interruption and owns all report persistence.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    from .diagnostics import DiagnosticEngine, load_history, _prepare_batch
    engine = DiagnosticEngine(config)
    try:
        while True:
            request = connection.recv()
            try:
                current, history_path = request.get("current"), request["history_path"]
                if request["command"] == "prepare":
                    plan, _ = _prepare_batch(engine, Path(history_path), Path(request["output"]), **request["options"])
                    connection.send((True, plan))
                    continue
                history = load_history(Path(history_path), cache=engine.jsonl_cache)
                if current is not None:
                    observed = finite_number(current.get("observed_at"))
                    history = [item for item in history if observed is not None
                               and finite_number(item.get("observed_at")) is not None
                               and item["observed_at"] < observed]
                report = engine.analyze(current, history)
                if engine.jsonl_cache is not None:
                    report["jsonl_cache"] = engine.jsonl_cache.stats()
                connection.send((True, report))
            except Exception as error:
                connection.send((False, type(error).__name__))
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()


def failure_report(config, current, status, seconds, elapsed):
    """Incomplete analysis is missing evidence, never a bottleneck candidate."""
    current = current or {}
    window = current.get("analysis_window") or {}
    return {
        "schema_version": 1, "diagnosis_schema_version": 1,
        "record_type": "bottleneck_diagnosis", "diagnosis_method": "rule",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": current.get("run_id", config.get("run_id")),
        "node": current.get("node", config.get("node")),
        "execution_mode": current.get("execution_mode", config.get("execution_mode", "sync")),
        "data_origin": "observed", "trigger": "step_observed" if current else "periodic",
        "trigger_record_id": current.get("record_id"), "step": current.get("step"),
        "boundary_scope": current.get("boundary_scope"), "analysis_window": window,
        "verdict": "insufficient_data", "findings": [], "evidence": {}, "candidates": [],
        "missing_sources": ["analysis:" + status],
        "symptom": {"step": current.get("step"), "step_duration_seconds": current.get("step_duration_seconds"),
                    "slow_stages": [], "boundary_scope": current.get("boundary_scope")},
        "comparison": {"current_interval": window, "baseline_interval": None,
                       "baseline_record_id": None, "selection": "unavailable", "signals": []},
        "analysis_execution": {"status": status, "isolated": True,
                               "deadline_seconds": seconds, "elapsed_seconds": elapsed},
        "limitations": ["Analysis did not complete. No partial result was persisted or used as a candidate."],
    }


class IsolatedAnalyzer:
    """Reuse one spawn worker; terminate and replace it after a stalled analysis.

    The deadline includes worker initialization, cache reads, analysis and IPC.
    Cleanup has a separate bounded grace. Kernel uninterruptible I/O cannot be
    guaranteed to exit; an unreaped worker prevents further worker creation.
    """

    def __init__(self, config, *, seconds: float = 60, target=_worker):
        if finite_number(seconds) is None or seconds <= 0:
            raise ValueError("analysis deadline must be finite and positive")
        self.config, self.seconds, self.target = dict(config), seconds, target
        self._context = multiprocessing.get_context("spawn")
        self._process = self._connection = None
        self._closed = False
        self._unreaped = False

    def _start(self):
        parent, child = self._context.Pipe()
        process = self._context.Process(target=self.target, args=(child, self.config), daemon=True)
        try:
            process.start()
        except BaseException:
            parent.close()
            child.close()
            raise
        child.close()
        self._process, self._connection = process, parent

    def _stop(self):
        if self._process is not None:
            process = self._process
            if process.is_alive():
                process.terminate()
            process.join(timeout=.2)
            if process.is_alive():
                process.kill()
                process.join(timeout=.2)
            if process.is_alive():
                return False  # Do not accumulate replacements for a D-state worker.
            process.close()
            self._process = None
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        return True

    def _request(self, request):
        if self._closed:
            raise RuntimeError("analysis controller is closed")
        started = time.monotonic()
        current = request.get("current")
        if self._unreaped:
            if not self._stop():
                return failure_report(self.config, current, "worker_unreaped", self.seconds, time.monotonic()-started)
            self._unreaped = False
        if self._process is not None and not self._process.is_alive():
            self._stop()
        if self._process is None:
            try:
                self._start()
            except (OSError, RuntimeError):
                return failure_report(self.config, current, "worker_start_failed", self.seconds, time.monotonic()-started)
        # send/recv run in a daemon thread: even a partially delivered large IPC
        # frame cannot trap the controller in Connection.recv beyond its deadline.
        done, response = threading.Event(), []
        connection = self._connection
        def exchange():
            try:
                connection.send(request)
                response.append(connection.recv())
            except (OSError, EOFError, TypeError, ValueError):
                response.append((False, "worker_connection_failed"))
            finally:
                done.set()
        thread = threading.Thread(target=exchange, daemon=True, name="xlayer-analysis-result")
        try:
            thread.start()
        except (OSError, RuntimeError):
            self._unreaped = not self._stop()
            return failure_report(self.config, current, "worker_communication_failed", self.seconds, time.monotonic()-started)
        if (not done.wait(max(0, self.seconds-(time.monotonic()-started)))
                or time.monotonic()-started >= self.seconds):
            reaped = self._stop()
            thread.join(timeout=.1)
            if not reaped:
                self._unreaped = True
            return failure_report(self.config, current, "deadline_exceeded", self.seconds, time.monotonic()-started)
        ok, result = response[0]
        elapsed = time.monotonic()-started
        if not ok:
            self._unreaped = not self._stop()
            report = failure_report(self.config, current, "worker_failed", self.seconds, elapsed)
            report["analysis_execution"]["error_type"] = result
            return report
        result["analysis_execution"] = {"status": "completed", "isolated": True,
                                         "deadline_seconds": self.seconds, "elapsed_seconds": elapsed}
        return result

    def prepare(self, history_path, output, **options):
        return self._request({"command": "prepare", "history_path": str(history_path),
                              "output": str(output), "options": options})

    def analyze(self, current, history_path: Path):
        return self._request({"command": "analyze", "current": current, "history_path": str(history_path)})

    def close(self):
        self._closed = True
        self._stop()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
