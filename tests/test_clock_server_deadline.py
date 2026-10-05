"""The serial clock service has one absolute deadline per owned connection."""

import json
import socket
import threading
import time
from urllib.request import urlopen

import pytest

from xlayer_telemetry.operations.clock import ClockServer


GUARD_NAME = "xlayer-clock-connection-guard"


def guards():
    return {thread for thread in threading.enumerate() if thread.name == GUARD_NAME}


@pytest.mark.parametrize("prefix", [b"GET /time?nonce=", b"GET /time?nonce=" + b"a" * 32 + b" HTTP/1.0\r\nX-Slow: "])
def test_trickled_request_line_or_headers_cannot_starve_next_client(monkeypatch, prefix):
    monkeypatch.setattr(ClockServer, "_REQUEST_TIMEOUT", .2, raising=False)
    before = guards()
    server = ClockServer(("127.0.0.1", 0), reference_id="monitor")
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    slow = socket.create_connection(server.server_address, timeout=.5)
    stop = threading.Event()

    def trickle():
        try:
            slow.sendall(prefix)
            while not stop.wait(.025):
                slow.sendall(b"a")
        except OSError:
            pass

    sender = threading.Thread(target=trickle, daemon=True)
    sender.start()
    try:
        time.sleep(.35)
        url = f"http://127.0.0.1:{server.server_port}/time?nonce=" + "a" * 32
        for _ in range(2):
            with urlopen(url, timeout=.5) as response:
                value = json.load(response)
            assert value["reference_id"] == "monitor" and value["clock_stable"]
        assert len(guards() - before) == 1
    finally:
        stop.set()
        slow.close()
        sender.join(timeout=1)
        server.shutdown()
        server.server_close()
        server.server_close()  # Closing twice must not touch a later socket.
        serving.join(timeout=1)
    assert guards() <= before
    assert not serving.is_alive() and not sender.is_alive()


def test_bind_failure_does_not_start_guard():
    before = guards()
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        with pytest.raises(OSError):
            ClockServer(occupied.getsockname(), reference_id="monitor")
    assert guards() <= before


def test_close_interrupts_owned_connection_only_and_joins_guard(monkeypatch):
    monkeypatch.setattr(ClockServer, "_REQUEST_TIMEOUT", 30, raising=False)
    before = guards()
    server = ClockServer(("127.0.0.1", 0), reference_id="monitor")
    unrelated = socket.socketpair()
    client = socket.create_connection(server.server_address, timeout=.5)
    try:
        owned, _ = server.get_request()
        server.server_close()
        server.server_close()
        assert owned.fileno() == -1
        assert client.recv(1) == b""
        unrelated[0].sendall(b"still-owned-by-test")
        assert unrelated[1].recv(100) == b"still-owned-by-test"
        assert guards() <= before
    finally:
        client.close()
        for stream in unrelated:
            stream.close()
        server.server_close()


@pytest.mark.parametrize("failure", [RuntimeError("thread unavailable"), OSError("thread unavailable")])
@pytest.mark.parametrize("stage", ["construct", "start"])
def test_guard_start_failure_closes_bound_socket_without_joining_unstarted_thread(monkeypatch, failure, stage):
    opened = []
    initialize = socket.socket.__init__
    def remember(stream, *args, **kwargs):
        initialize(stream, *args, **kwargs)
        opened.append(stream)
    def fail(thread):
        raise failure
    monkeypatch.setattr(socket.socket, "__init__", remember)
    if stage == "construct":
        monkeypatch.setattr(threading, "Thread", lambda *args, **kwargs: fail(None))
    else:
        monkeypatch.setattr(threading.Thread, "start", fail)
    with pytest.raises(type(failure), match="thread unavailable"):
        ClockServer(("127.0.0.1", 0), reference_id="monitor")
    assert opened and all(stream.fileno() == -1 for stream in opened)
