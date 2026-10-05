"""Real Ollama HTTP requests have a total deadline and bounded response memory."""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests._process_helpers import child_processes
from xlayer_telemetry.analysis import llm_diagnosis as llm


def packet():
    return {"schema_version": 1, "record_type": "llm_observation_packet", "data_origin": "synthetic",
            "current_interval": {"start": 10, "end": 20, "accuracy": "exact"},
            "observations": [{"id": "m1", "signal": "step_duration_seconds", "current": 20,
                              "baseline": 10, "observation_scope": "application"}]}


def diagnosis():
    return {"assessment": "insufficient_evidence", "summary": "Step duration만 관측되어 원인은 확인되지 않았습니다.",
            "candidates": [], "limitations": []}


@pytest.fixture
def ollama():
    state = {"slow": None, "oversized": False, "requests": [], "chats": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.reply(None)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            self.reply(payload)

        def reply(self, payload):
            state["requests"].append(self.path)
            stage = "metadata"
            if self.path == "/api/tags":
                result = {"models": [{"name": "test:latest", "digest": "verified-test-digest"}]}
            elif self.path == "/api/version":
                result = {"version": "fixture"}
                if state["oversized"]:
                    result["padding"] = "x" * (8 * 1024 * 1024)
            else:
                state["chats"] += 1
                review = "decision" in payload["format"]["properties"]
                stage = "review" if review else "generation"
                answer = {"decision": "accept", "issues": [], "diagnosis": diagnosis()} if review else diagnosis()
                result = {"model": "test:latest", "done": True, "done_reason": "stop",
                          "message": {"role": "assistant", "content": json.dumps(answer)}}
            self.send_response(200)
            self.end_headers()
            try:
                if state["slow"] == stage:
                    # Every recv progresses, defeating an inactivity timeout.
                    for _ in range(60):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(.04)
                self.wfile.write(json.dumps(result).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@pytest.mark.parametrize("stage", ["metadata", "generation", "review"])
def test_total_deadline_interrupts_trickling_stage_and_allows_clean_retry(ollama, stage):
    state, endpoint = ollama
    state["slow"] = stage
    children_before = {item.pid for item in child_processes(os.getpid())}
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="deadline"):
        llm.diagnose(packet(), endpoint=endpoint, model="test", timeout=.6)
    assert time.monotonic() - started < 1.5
    assert {item.pid for item in child_processes(os.getpid())} <= children_before
    state["slow"] = None
    result = llm.diagnose(packet(), endpoint=endpoint, model="test", timeout=5)
    assert result["diagnosis"] == diagnosis()
    assert result["model_digest"] == "verified-test-digest"
    assert result["runtime_version"] == "fixture"
    assert {item.pid for item in child_processes(os.getpid())} <= children_before


def test_transport_rejects_oversized_response(ollama):
    state, endpoint = ollama
    state["oversized"] = True
    with pytest.raises(RuntimeError, match="response exceeds"):
        llm._get(endpoint + "/api/version", 5)


def test_total_budget_includes_metadata(monkeypatch):
    now = [0.]
    monkeypatch.setattr(llm.time, "monotonic", lambda: now[0])
    def get(*args):
        now[0] += 3
        return {}
    monkeypatch.setattr(llm, "_get", get)
    monkeypatch.setattr(llm, "_post", lambda *args: pytest.fail("metadata consumed the total deadline"))
    with pytest.raises(TimeoutError, match="deadline"):
        llm.diagnose(packet(), timeout=5)


def test_private_transport_supports_unguarded_standalone_caller(tmp_path, ollama):
    _, endpoint = ollama
    script = tmp_path / "unguarded.py"
    script.write_text("import json\nfrom xlayer_telemetry.analysis.llm_diagnosis import _get\n"
                      f"print(json.dumps(_get({endpoint + '/api/version'!r}, 3)))\n")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path,
                            env=os.environ | {"PYTHONPATH": str(root)},
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"version": "fixture"}


def test_cli_timeout_replaces_old_success_with_failure_record(tmp_path, ollama):
    state, endpoint = ollama
    state["slow"] = "review"
    source, output = tmp_path / "input.json", tmp_path / "result.json"
    source.write_text(json.dumps(packet()))
    output.write_text('{"record_type":"llm_diagnosis","stale":true}')
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-m", "xlayer_telemetry.analysis.llm_diagnosis",
                             "--input", str(source), "--output", str(output), "--endpoint", endpoint,
                             "--model", "test", "--timeout", ".6"], capture_output=True, text=True, timeout=4)
    assert result.returncode == 1, result.stdout + result.stderr
    assert time.monotonic() - started < 1.8
    saved = json.loads(output.read_text())
    assert saved["record_type"] == "llm_diagnosis_failure"
    assert "deadline" in saved["reason"]
    assert "stale" not in saved and "diagnosis" not in saved
    assert saved["observation_packet"] == packet()
    assert not list(tmp_path.glob(".result.json.*"))


def test_invalid_json_preserves_value_error_and_reaps_worker(monkeypatch):
    from xlayer_telemetry import _http_transport as transport

    class Process:
        returncode = 0
        stdin = stdout = None
        def communicate(self, *args, **kwargs):
            return b'{"ok":true}\nnot-json', None
        def poll(self):
            return 0
        def terminate(self):
            pytest.fail("completed worker must not be signaled")
        kill = terminate
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *args, **kwargs: Process())
    with pytest.raises(ValueError):
        transport.request_json("http://unused", None, 1)


def test_unreaped_worker_blocks_replacement(monkeypatch):
    from xlayer_telemetry import _http_transport as transport

    class Process:
        def poll(self):
            return None
    monkeypatch.setattr(transport, "_UNREAPED", [Process()])
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not start replacement"))
    with pytest.raises(RuntimeError, match="no replacement"):
        transport.request_json("http://unused", None, 1)


def test_killed_cli_does_not_leave_its_transport_alive(tmp_path, ollama):
    from tests._process_helpers import process_exists

    state, endpoint = ollama
    state["slow"] = "generation"
    source = tmp_path / "input.json"
    source.write_text(json.dumps(packet()))
    process = subprocess.Popen([sys.executable, "-m", "xlayer_telemetry.analysis.llm_diagnosis",
                                "--input", str(source), "--output", str(tmp_path / "result.json"),
                                "--endpoint", endpoint, "--model", "test", "--timeout", "30"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    workers = []
    try:
        end = time.monotonic() + 3
        while time.monotonic() < end and not state["chats"]:
            time.sleep(.01)
        assert state["chats"] == 1
        workers = child_processes(process.pid)
        assert workers
        process.kill()  # Even the parent cannot execute finally after SIGKILL.
        process.communicate(timeout=2)
        end = time.monotonic() + 1
        while time.monotonic() < end and any(process_exists(worker) for worker in workers):
            time.sleep(.01)
        assert all(not process_exists(worker) for worker in workers)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=2)


def test_late_review_cannot_publish_success(monkeypatch):
    now = [0.]
    monkeypatch.setattr(llm.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(llm, "_get", lambda *args: {})
    monkeypatch.setattr(llm, "_post", lambda *args: {
        "model": "test:latest", "done": True, "done_reason": "stop",
        "message": {"role": "assistant", "content": json.dumps(diagnosis())}})
    def review(*args, **kwargs):
        now[0] = 6
        return diagnosis(), {}
    monkeypatch.setattr(llm, "review_diagnosis", review)
    with pytest.raises(TimeoutError, match="deadline"):
        llm.diagnose(packet(), model="test", timeout=5)


def test_transport_after_fork_does_not_use_parent_lock_or_workers(monkeypatch, ollama):
    from xlayer_telemetry import _http_transport as transport

    class ParentLock:
        def __enter__(self):
            pytest.fail("child must not acquire an inherited parent lock")
    monkeypatch.setattr(transport, "_STATE_PID", -1)
    monkeypatch.setattr(transport, "_UNREAPED_LOCK", ParentLock())
    monkeypatch.setattr(transport, "_UNREAPED", [object()])
    _, endpoint = ollama
    assert transport.request_json(endpoint + "/api/version", None, 3) == {"version": "fixture"}
    assert transport._STATE_PID == os.getpid()
    assert transport._UNREAPED == []


def test_worker_deadline_starts_before_stdin_eof():
    deadline = time.monotonic() + .3
    worker = subprocess.Popen([sys.executable, "-m", "xlayer_telemetry._http_transport",
                                str(deadline), str(os.getpid())],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        # An inherited writer can keep this pipe open without sending a byte.
        try:
            worker.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pytest.fail("worker waited for stdin EOF beyond its deadline")
        assert worker.returncode == 124
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.communicate(timeout=2)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_killed_owner_with_fork_inherited_stdin_does_not_leave_worker_alive(tmp_path):
    import signal
    from tests._process_helpers import process_running, read_process, signal_process

    owner_script = tmp_path / "owner.py"
    owner_script.write_text('''import json, os, subprocess, sys, time
worker = subprocess.Popen([sys.executable, "-m", "xlayer_telemetry._http_transport",
                           str(time.monotonic() + 30), str(os.getpid())],
                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
keeper = os.fork()
if keeper == 0:
    time.sleep(30)  # Hold the inherited stdin write descriptor after owner death.
    os._exit(0)
print(json.dumps({"worker": worker.pid, "keeper": keeper}), flush=True)
time.sleep(30)
''')
    owner = subprocess.Popen([sys.executable, str(owner_script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    worker = keeper = None
    try:
        children = json.loads(owner.stdout.readline())
        worker, keeper = (read_process(children[key]) for key in ("worker", "keeper"))
        assert worker is not None and keeper is not None
        owner.kill()
        owner.wait(timeout=2)
        end = time.monotonic() + 1
        while time.monotonic() < end and process_running(worker):
            time.sleep(.01)
        assert not process_running(worker), "worker survived its owner because another process held stdin open"
        assert process_running(keeper), "unrelated descriptor holder must not be terminated"
    finally:
        for child in (worker, keeper):
            if child is not None:
                signal_process(child, signal.SIGKILL)
        if owner.poll() is None:
            owner.kill()
        owner.communicate(timeout=2)


def test_private_worker_argv_contains_only_deadline_and_owner_controls(monkeypatch):
    from xlayer_telemetry import _http_transport as transport

    captured = {}
    class Process:
        returncode = 0
        stdin = stdout = None
        def communicate(self, data, **kwargs):
            captured["stdin"] = json.loads(data)
            return b'{"ok":true}\n{}', None
        def poll(self):
            return 0
        def terminate(self):
            pytest.fail("completed worker must not be signaled")
        kill = terminate
    def popen(argv, **kwargs):
        captured["argv"] = argv
        return Process()
    monkeypatch.setattr(transport.subprocess, "Popen", popen)
    url, payload = "http://example.invalid/private-path", {"prompt": "private-observations"}
    assert transport.request_json(url, payload, 3) == {}
    argv = captured["argv"]
    assert len(argv) == 5
    assert float(argv[-2]) > time.monotonic()
    assert int(argv[-1]) == os.getpid()
    assert url not in " ".join(argv) and payload["prompt"] not in " ".join(argv)
    assert captured["stdin"] == {"url": url, "payload": payload}


@pytest.fixture
def redirects():
    state = {"bytes": 1024, "trickle": False, "requests": []}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            state["requests"].append(self.path)
            if self.path == "/ok":
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
                return
            code, hops = map(int, self.path.strip("/").split("/"))
            self.send_response(code)
            self.send_header("Location", f"/{code}/{hops - 1}" if hops > 1 else "/ok")
            size = 60 if state["trickle"] else state["bytes"]
            self.send_header("Content-Length", str(size))
            self.end_headers()
            try:
                while size:
                    chunk = min(size, 1 if state["trickle"] else 65536)
                    self.wfile.write(b"x" * chunk)
                    self.wfile.flush()
                    size -= chunk
                    if state["trickle"]:
                        time.sleep(.04)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_oversized_redirect_body_is_rejected_before_following(redirects, code):
    state, endpoint = redirects
    state["bytes"] = 32 * 1024 * 1024
    with pytest.raises(RuntimeError, match="redirect response exceeds"):
        llm._get(f"{endpoint}/{code}/1", 5)
    assert state["requests"] == [f"/{code}/1"]


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_bounded_multi_hop_redirects_keep_existing_behavior(redirects, code):
    from urllib.request import HTTPRedirectHandler

    state, endpoint = redirects
    if code == 308 and not hasattr(HTTPRedirectHandler, "http_error_308"):
        # Python 3.10 rejects 308; preserve and assert that behavior rather
        # than silently adding support or skipping the regression.
        with pytest.raises(OSError, match="HTTP Error 308"):
            llm._get(f"{endpoint}/{code}/2", 5)
        assert state["requests"] == [f"/{code}/2"]
        return
    assert llm._get(f"{endpoint}/{code}/2", 5) == {}
    assert state["requests"] == [f"/{code}/2", f"/{code}/1", "/ok"]


def test_redirect_trickle_still_has_deadline_and_clean_retry(redirects):
    state, endpoint = redirects
    state["trickle"] = True
    children_before = {item.pid for item in child_processes(os.getpid())}
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="deadline"):
        llm._get(endpoint + "/302/1", .4)
    assert time.monotonic() - started < 1.3
    assert {item.pid for item in child_processes(os.getpid())} <= children_before
    state["trickle"] = False
    assert llm._get(endpoint + "/302/1", 5) == {}
