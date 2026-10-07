"""Bounded opt-in hook for the next window; delivery is not capture proof."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import signal
import subprocess
import threading
import time
from typing import Callable, Mapping, Sequence

from ..measurements import finite_number


@dataclass(frozen=True)
class CapturePolicy:
    cooldown_seconds: float = 60.0
    max_attempts: int = 4
    window_seconds: float = 5.0
    timeout_seconds: float = 10.0
    max_capture_bytes: int = 16 * 1024 * 1024

    def __post_init__(self):
        for name in ("cooldown_seconds", "window_seconds", "timeout_seconds"):
            value = getattr(self, name)
            if finite_number(value) is None or not 0 < value <= 3600:
                raise ValueError(f"invalid {name}")
        if self.timeout_seconds > 30 or self.window_seconds > self.timeout_seconds:
            raise ValueError("capture window must fit hook deadline (at most 30 seconds)")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 128:
            raise ValueError("invalid attempt budget")
        if type(self.max_capture_bytes) is not int or not 1024 <= self.max_capture_bytes <= 1024 * 1024 * 1024:
            raise ValueError("invalid capture byte budget")


class TriggeredProfiler:
    """One controller per run: serialized hook, global cooldown and budget.

    Fixed argv and bounded JSON stdin; discarded output and a deadline. Only
    the newly created child session may be killed. Do not log configured argv
    or exception messages: these may contain credentials. Trusted hooks must
    enforce their own capture file size limit and not detach child processes.
    """

    def __init__(self, argv: Sequence[str] | None = None, *, policy: CapturePolicy | None = None,
                 clock: Callable[[], float] = time.monotonic):
        if argv is not None and (isinstance(argv, (str, bytes)) or not 1 <= len(argv) <= 32
                                 or any(not isinstance(arg, str) or not arg or len(arg) > 4096 for arg in argv)):
            raise ValueError("hook requires bounded argv")
        self.argv = tuple(argv) if argv is not None else None
        self.policy = policy or CapturePolicy()
        self.clock = clock
        self.attempts = 0
        self._last_attempt: float | None = None
        self._seen: set[tuple] = set()
        self._lock = threading.Lock()

    def request(self, signature: Mapping, comparison: Mapping, *, anomaly: bool = False) -> dict:
        if self.argv is None:
            return {"state": "disabled"}
        if comparison.get("slow") is not True and anomaly is not True:
            return {"state": "not_triggered"}
        boundary = signature["boundary"]
        if boundary.get("accuracy") in {"unknown", "clock_discontinuity"}:
            return {"state": "missing_evidence", "reason": "boundary_time_unknown"}
        if any(signature["quality"].get(key) for key in ("events_truncated", "observations_truncated", "dropped_groups")):
            return {"state": "missing_evidence", "reason": "signature_truncated"}
        key = (*sorted(boundary["context"].items()), boundary["scope"], boundary["step"], boundary["sequence"], boundary.get("rollout_id"))
        if not self._lock.acquire(blocking=False):
            return {"state": "busy"}
        try:
            now = self.clock()
            if key in self._seen:
                return {"state": "duplicate"}
            if self.attempts >= self.policy.max_attempts:
                return {"state": "budget_exhausted", "attempts": self.attempts}
            if self._last_attempt is not None and now-self._last_attempt < self.policy.cooldown_seconds:
                return {"state": "cooldown"}
            payload = {"schema_version": 1, "record_type": "profiling_request", "context": boundary["context"],
                       "trigger": {"scope": boundary["scope"], "step": boundary["step"], "sequence": boundary["sequence"],
                                   "rollout_id": boundary.get("rollout_id"), "accuracy": boundary["accuracy"],
                                   "duration_ratio": comparison.get("duration_ratio"), "anomaly": anomaly is True},
                       "target": "next_cooperative_window", "window_seconds": self.policy.window_seconds,
                       "max_capture_bytes": self.policy.max_capture_bytes}
            encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
            if len(encoded) > 8192:
                return {"state": "missing_evidence", "reason": "request_too_large"}
            self.attempts += 1
            self._seen.add(key)
            self._last_attempt = now
            return self._invoke(encoded)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            return {"state": "hook_failed", "error_type": type(exc).__name__}
        finally:
            self._lock.release()

    def _invoke(self, payload: bytes) -> dict:
        with subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, start_new_session=True) as process:
            try:
                process.communicate(payload, timeout=self.policy.timeout_seconds)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.communicate(timeout=1)
                return {"state": "hook_timeout", "attempts": self.attempts}
            return {"state": "hook_delivered" if process.returncode == 0 else "hook_failed",
                    "exit_code": process.returncode, "attempts": self.attempts,
                    "capture_verified": False}
