"""Private, bounded one-request transport for HTTP health and Ollama clients.

A subprocess keeps DNS, HTTP headers and trickling response bodies cancellable
without requiring multiprocessing's safe-main convention from library callers.
Only the parent publishes results. URL and payload travel over stdin; argv
contains only the deadline and owner PID needed to guard a blocked stdin read.
"""
from __future__ import annotations

from http.client import HTTPException
import json
import math
import os
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_REQUEST_BYTES = 256 * 1024
_MAX_STATUS_BYTES = 8192
_UNREAPED: list[subprocess.Popen] = []
_UNREAPED_LOCK = threading.Lock()
_STATE_PID = os.getpid()


class _ResponseTooLarge(RuntimeError):
    """A generated, non-sensitive size error safe to return to the parent."""


def _check_complete(response) -> None:
    # HTTPResponse.read(amount) can silently return a partial Content-Length
    # body at EOF. A syntactically valid JSON prefix is not a complete reply.
    if getattr(response, "length", None):
        raise ConnectionError("Incomplete HTTP response")


class _BoundedRedirectHandler(HTTPRedirectHandler):
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


def request_bytes(url: str, payload: dict | None, timeout: float, *,
                  max_response_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """Bound DNS, headers, redirects and the complete body by one deadline."""
    global _STATE_PID, _UNREAPED, _UNREAPED_LOCK
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise TimeoutError("HTTP transport deadline exceeded")
    if type(max_response_bytes) is not int or not 0 < max_response_bytes <= MAX_RESPONSE_BYTES:
        raise ValueError("Invalid HTTP response byte limit")
    deadline = time.monotonic() + timeout
    if _STATE_PID != os.getpid():
        # An embedding application may fork while another thread holds the
        # lock. The child owns neither that lock nor the parent's workers.
        _STATE_PID, _UNREAPED, _UNREAPED_LOCK = os.getpid(), [], threading.Lock()
    with _UNREAPED_LOCK:
        _UNREAPED[:] = [process for process in _UNREAPED if process.poll() is None]
        if _UNREAPED:
            raise RuntimeError("Previous HTTP transport worker has not exited; no replacement started")
    request = {"url": url, "payload": payload}
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
        if time.monotonic() >= deadline or process.returncode == 124:
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
        return body
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
        http_request = Request(request["url"],
                               data=json.dumps(payload, allow_nan=False).encode() if payload is not None else None,
                               headers={"Content-Type": "application/json"} if payload is not None else {},
                               method="POST" if payload is not None else "GET")
        with build_opener(_BoundedRedirectHandler(max_response_bytes)).open(http_request, timeout=remaining) as response:
            body = response.read(max_response_bytes + 1)
            if len(body) > max_response_bytes:
                raise _ResponseTooLarge("HTTP response exceeds byte limit")
            _check_complete(response)
        sys.stdout.buffer.write(b'{"ok":true}\n' + body)
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
