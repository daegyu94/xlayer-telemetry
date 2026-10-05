"""Private, bounded one-request transport for direct HTTP clients.

A subprocess keeps DNS, HTTP headers and trickling response bodies cancellable
without requiring multiprocessing's safe-main convention from library callers.
Only the parent publishes results. URL and payload travel over stdin; argv
contains only the deadline and owner PID needed to guard a blocked stdin read.
"""
from __future__ import annotations

import base64
from http.client import HTTPException
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ._http_redirects import _CredentialSafeRedirectHandler


MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_REQUEST_BYTES = 256 * 1024
_MAX_STATUS_BYTES = 8192
_UNREAPED: list[subprocess.Popen] = []
_UNREAPED_LOCK = threading.Lock()
_STATE_PID = os.getpid()
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class _ResponseTooLarge(RuntimeError):
    """A generated, non-sensitive size error safe to return to the parent."""


def _check_complete(response) -> None:
    # HTTPResponse.read(amount) can silently return a partial Content-Length
    # body at EOF. A syntactically valid JSON prefix is not a complete reply.
    if getattr(response, "length", None):
        raise ConnectionError("Incomplete HTTP response")


class _BoundedRedirectHandler(_CredentialSafeRedirectHandler):
    def __init__(self, max_response_bytes: int):
        self.max_response_bytes = max_response_bytes
        super().__init__()

    def http_error_302(self, req, fp, code, msg, headers):
        # urllib otherwise drains redirects with an unbounded fp.read().
        # Discard small chunks before delegation, so recursive redirect frames
        # retain neither a large body nor unread data for that default drain.
        remaining = self.max_response_bytes + 1
        try:
            while remaining:
                count = len(fp.read(min(65536, remaining)))
                if not count:
                    break
                remaining -= count
            if not remaining:
                raise _ResponseTooLarge("HTTP redirect response exceeds byte limit")
            _check_complete(fp)
        except BaseException:
            fp.close()
            raise
        return super().http_error_302(req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class _NoRedirectHandler(HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        fp.close()
        raise ValueError("Clock endpoint redirects are not supported")

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _stop(process: subprocess.Popen) -> bool:
    """Bound cleanup separately from the request deadline."""
    for action in (process.terminate, process.kill):
        if process.poll() is not None:
            return True
        try:
            action()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=.2)
            return True
        except subprocess.TimeoutExpired:
            pass
    return process.poll() is not None


def request_json(url: str, payload: dict | None, timeout: float):
    """Keep the Ollama JSON wrapper and include parsing in its deadline."""
    started = time.monotonic()
    body = request_bytes(url, payload, timeout)
    result = json.loads(body)
    if time.monotonic() - started >= timeout:
        raise TimeoutError("HTTP transport deadline exceeded")
    return result


def _validate_headers(headers: dict[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    if not isinstance(headers, dict) or any(
        not isinstance(name, str) or not _HEADER_NAME.fullmatch(name)
        or not isinstance(value, str)
        or any((ord(char) < 32 and char != "\t") or 127 <= ord(char) < 160
               or ord(char) > 255 for char in value)
        for name, value in headers.items()
    ):
        raise ValueError("Invalid HTTP request headers")
    return headers


def request_bytes(url: str, payload: dict | None, timeout: float, *,
                  max_response_bytes: int = MAX_RESPONSE_BYTES,
                  data: bytes | None = None, headers: dict[str, str] | None = None) -> bytes:
    """Bound DNS, headers, redirects and the complete body by one deadline."""
    return _request_exchange(url, payload, timeout, max_response_bytes=max_response_bytes,
                             data=data, headers=headers)[0]


def request_clock_bytes(url: str, timeout: float) -> tuple[bytes, dict]:
    """Return real request-boundary clocks; reject all redirects before following."""
    body, status = _request_exchange(url, None, timeout, max_response_bytes=4096, measure_clock=True)
    return body, status["timing"]


def _request_exchange(url: str, payload: dict | None, timeout: float, *,
                      max_response_bytes: int = MAX_RESPONSE_BYTES, data: bytes | None = None,
                      headers: dict[str, str] | None = None, measure_clock: bool = False) -> tuple[bytes, dict]:
    global _STATE_PID, _UNREAPED, _UNREAPED_LOCK
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise TimeoutError("HTTP transport deadline exceeded")
    if type(max_response_bytes) is not int or not 0 < max_response_bytes <= MAX_RESPONSE_BYTES:
        raise ValueError("Invalid HTTP response byte limit")
    started = time.monotonic()
    deadline = started + timeout
    if _STATE_PID != os.getpid():
        # An embedding application may fork while another thread holds the
        # lock. The child owns neither that lock nor the parent's workers.
        _STATE_PID, _UNREAPED, _UNREAPED_LOCK = os.getpid(), [], threading.Lock()
    with _UNREAPED_LOCK:
        _UNREAPED[:] = [process for process in _UNREAPED if process.poll() is None]
        if _UNREAPED:
            raise RuntimeError("Previous HTTP transport worker has not exited; no replacement started")
    if data is not None and (not isinstance(data, bytes) or payload is not None):
        raise ValueError("HTTP request needs either a JSON payload or raw bytes")
    if data is not None and len(data) > _MAX_REQUEST_BYTES:
        raise ValueError("HTTP transport request exceeds 256 KiB limit")
    checked_headers = _validate_headers(headers)
    request = {"url": url, "payload": payload}
    if measure_clock:
        request["measure_clock"] = True
    if data is not None:
        request["data"] = base64.b64encode(data).decode("ascii")
    if checked_headers:
        request["headers"] = checked_headers
    if max_response_bytes != MAX_RESPONSE_BYTES:
        request["max_response_bytes"] = max_response_bytes
    command = json.dumps(request, allow_nan=False).encode()
    if len(command) > _MAX_REQUEST_BYTES:
        raise ValueError("HTTP transport request exceeds 256 KiB limit")
    process = subprocess.Popen([sys.executable, "-m", "xlayer_telemetry._http_transport",
                                str(deadline), str(os.getpid())],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("HTTP transport deadline exceeded")
        try:
            output, _ = process.communicate(command, timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("HTTP transport deadline exceeded") from error
        completed = time.monotonic()
        if completed >= deadline or process.returncode == 124:
            raise TimeoutError("HTTP transport deadline exceeded")
        if process.returncode != 0:
            raise RuntimeError("HTTP transport worker exited before returning a response")
        status, separator, body = output.partition(b"\n")
        if not separator or len(status) > _MAX_STATUS_BYTES or len(body) > max_response_bytes:
            raise RuntimeError("Invalid or oversized HTTP transport response")
        status = json.loads(status)
        if not isinstance(status, dict) or type(status.get("ok")) is not bool:
            raise RuntimeError("Invalid HTTP transport status")
        if not status["ok"]:
            category = status.get("type")
            if category == "HTTPError" and type(status.get("code")) is int:
                # Keep the status code without retaining a private URL, response
                # headers, reason phrase or partial backend body in the error.
                raise HTTPError("", status["code"], "HTTP request failed", {}, None)
            error_type = {"TimeoutError": TimeoutError, "OSError": OSError,
                          "ConnectionError": ConnectionError, "URLError": URLError,
                          "ResponseTooLarge": _ResponseTooLarge,
                          "ValueError": ValueError, "RuntimeError": RuntimeError}.get(category, RuntimeError)
            raise error_type(status.get("message", "HTTP transport failed"))
        if measure_clock:
            timing = status.get("timing")
            if (not isinstance(timing, dict) or set(timing) != {"t1", "m1", "t4", "m4"}
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in timing.values())
                    or not started <= timing["m1"] <= timing["m4"] <= completed
                    or not 0 <= timing["t4"] - timing["t1"] <= timeout + .05
                    or abs(timing["t4"] - timing["t1"] - (timing["m4"] - timing["m1"])) > .05):
                raise ValueError("Invalid HTTP clock timing")
        return body, status
    finally:
        if not _stop(process):
            with _UNREAPED_LOCK:
                _UNREAPED.append(process)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()


def _main() -> None:
    done = threading.Event()
    try:
        if len(sys.argv) != 3:
            raise ValueError("HTTP transport needs a deadline and owner PID")
        deadline, parent_pid = float(sys.argv[1]), int(sys.argv[2])
        if not math.isfinite(deadline) or parent_pid <= 0:
            raise ValueError("Invalid HTTP transport deadline or owner PID")

        def guard():
            # A killed parent cannot run finally. Do not leave its HTTP worker
            # alive; the child's own deadline also bounds unsupported platforms.
            while not done.wait(.05):
                if time.monotonic() >= deadline:
                    os._exit(124)
                if os.name == "posix" and os.getppid() != parent_pid:
                    os._exit(125)

        threading.Thread(target=guard, daemon=True, name="xlayer-http-transport-guard").start()
        # Start the guard before reading: a concurrent fork can inherit the
        # write descriptor and prevent EOF after the owning parent is killed.
        command = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
        if len(command) > _MAX_REQUEST_BYTES:
            raise ValueError("HTTP transport request exceeds 256 KiB limit")
        request = json.loads(command)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("HTTP transport deadline exceeded")
        max_response_bytes = request.get("max_response_bytes", MAX_RESPONSE_BYTES)
        if type(max_response_bytes) is not int or not 0 < max_response_bytes <= MAX_RESPONSE_BYTES:
            raise ValueError("Invalid HTTP response byte limit")
        payload = request["payload"]
        data = request.get("data")
        if data is not None and (not isinstance(data, str) or payload is not None):
            raise ValueError("Invalid HTTP request body")
        data = base64.b64decode(data, validate=True) if data is not None else None
        headers = _validate_headers(request.get("headers"))
        if payload is not None:
            data = json.dumps(payload, allow_nan=False).encode()
            headers = {"Content-Type": "application/json", **headers}
        http_request = Request(request["url"], data=data, headers=headers,
                               method="POST" if data is not None else "GET")
        measure_clock = request.get("measure_clock", False)
        if type(measure_clock) is not bool:
            raise ValueError("Invalid HTTP clock measurement mode")
        opener = build_opener(_NoRedirectHandler() if measure_clock else _BoundedRedirectHandler(max_response_bytes))
        timing = {}
        if measure_clock:
            timing["t1"], timing["m1"] = time.time(), time.monotonic()
        with opener.open(http_request, timeout=remaining) as response:
            body = response.read(max_response_bytes + 1)
            if measure_clock:
                timing["t4"], timing["m4"] = time.time(), time.monotonic()
            if len(body) > max_response_bytes:
                raise _ResponseTooLarge("HTTP response exceeds byte limit")
            _check_complete(response)
        status = {"ok": True, "timing": timing} if measure_clock else {"ok": True}
        sys.stdout.buffer.write(json.dumps(status, separators=(",", ":")).encode() + b"\n" + body)
        sys.stdout.buffer.flush()
    except (OSError, HTTPException, RuntimeError, ValueError, KeyError, TypeError) as error:
        category = ("ResponseTooLarge" if isinstance(error, _ResponseTooLarge) else
                    "TimeoutError" if isinstance(error, TimeoutError) else
                    "HTTPError" if isinstance(error, HTTPError) else
                    "URLError" if isinstance(error, URLError) else
                    "ConnectionError" if isinstance(error, (HTTPException, ConnectionError)) else
                    "OSError" if isinstance(error, OSError) else
                    "ValueError" if isinstance(error, ValueError) else "RuntimeError")
        message = ("HTTP transport deadline exceeded" if category == "TimeoutError" else
                   str(error) if isinstance(error, _ResponseTooLarge) else "HTTP transport failed")
        status = {"ok": False, "type": category, "message": message}
        if isinstance(error, HTTPError):
            status["code"] = error.code
            error.close()
        sys.stdout.buffer.write(json.dumps(status).encode() + b"\n")
        sys.stdout.buffer.flush()
    finally:
        done.set()


if __name__ == "__main__":
    _main()
