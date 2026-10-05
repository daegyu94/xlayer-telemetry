"""A bounded clock exchange preserves request-boundary timing and identity."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tests._process_helpers import child_processes
from xlayer_telemetry.operations import clock


@pytest.fixture
def reference():
    state = {"mode": "ok", "requests": [], "change": {}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            began = time.time()
            state["requests"].append(self.path)
            route = urlsplit(self.path)
            nonce = parse_qs(route.query).get("nonce", [""])[0]
            if state["mode"] == "redirect" and route.path != "/redirected":
                self.send_response(state.get("redirect_code", 302))
                self.send_header("Location", "/redirected?nonce=" + nonce)
                self.end_headers()
                return
            value = {"schema_version": 1, "reference_id": "monitor", "reference_session": "fixture",
                     "nonce": nonce, "t2": began, "t3": time.time(), "clock_stable": True}
            if state["mode"] == "rotate":
                value["reference_session"] = str(len(state["requests"]))
            value.update(state["change"])
            if state["mode"] == "oversized":
                value["padding"] = "x" * 4096
            self.send_response(200)
            self.end_headers()
            try:
                if state["mode"] == "trickle":
                    for _ in range(30):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(.03)
                self.wfile.write(json.dumps(value).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        assert not worker.is_alive()


@pytest.mark.parametrize("custom_clock", [False, True])
def test_clock_trickle_cannot_publish_late_calibration_even_with_injected_clock(reference, custom_clock):
    state, url = reference
    state["mode"] = "trickle"
    before = {p.pid for p in child_processes(os.getpid())}
    started = time.monotonic()
    kwargs = {"wall_clock": lambda: time.time() + 12, "monotonic": lambda: time.monotonic() + 40} if custom_clock else {}
    with pytest.raises(ValueError, match="No valid clock exchange"):
        clock.calibrate(url, node="n", reference_id="monitor", samples=1, timeout=.3, **kwargs)
    assert time.monotonic() - started < .8
    assert state["requests"]
    assert {p.pid for p in child_processes(os.getpid())} <= before
    state["mode"] = "ok"
    result = clock.calibrate(url, node="n", reference_id="monitor", samples=1, **kwargs)
    assert result["offset_seconds"] == pytest.approx(-12 if custom_clock else 0, abs=.03)
    assert result["uncertainty_seconds"] >= result["round_trip_seconds"] / 2
    assert {p.pid for p in child_processes(os.getpid())} <= before


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_clock_redirect_is_rejected_before_contacting_destination(reference, status):
    state, url = reference
    state["mode"] = "redirect"
    state["redirect_code"] = status
    with pytest.raises(ValueError, match="No valid clock exchange"):
        clock.calibrate(url, node="n", reference_id="monitor", samples=1)
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("change", [{"nonce": "bad"}, {"reference_id": "other"},
                                      {"clock_stable": False}, {"reference_session": ""}])
def test_invalid_clock_identity_remains_invalid(reference, change):
    state, url = reference
    state["change"] = change
    with pytest.raises(ValueError, match="No valid clock exchange"):
        clock.calibrate(url, node="n", reference_id="monitor", samples=1)


def test_clock_response_cap_remains_4096_bytes(reference):
    state, url = reference
    state["mode"] = "oversized"
    with pytest.raises(ValueError, match="No valid clock exchange"):
        clock.calibrate(url, node="n", reference_id="monitor", samples=1)


def test_startup_and_return_ipc_delay_are_not_network_rtt(reference, monkeypatch):
    from xlayer_telemetry import _http_transport as transport
    _, url = reference
    popen = transport.subprocess.Popen
    timed_request = clock.request_clock_bytes
    exchanges = []

    def delayed(*args, **kwargs):
        time.sleep(.2)
        process = popen(*args, **kwargs)
        communicate = process.communicate
        def receive(*args, **kwargs):
            result = communicate(*args, **kwargs)
            time.sleep(.2)
            return result
        process.communicate = receive
        return process

    def record(*args, **kwargs):
        body, timing = timed_request(*args, **kwargs)
        exchanges.append((json.loads(body), timing))
        return body, timing

    monkeypatch.setattr(transport.subprocess, "Popen", delayed)
    monkeypatch.setattr(clock, "request_clock_bytes", record)
    started = time.monotonic()
    result = clock.calibrate(url, node="n", reference_id="monitor", samples=1, timeout=2,
                             wall_clock=lambda: time.time() + 12,
                             monotonic=lambda: time.monotonic() + 40)
    assert time.monotonic() - started >= .4
    reply, timing = exchanges[0]
    actual_rtt = timing["m4"] - timing["m1"] - (reply["t3"] - reply["t2"])
    assert result["round_trip_seconds"] == actual_rtt
    assert result["round_trip_seconds"] < .15
    assert result["uncertainty_seconds"] > actual_rtt / 2 + .000035
    assert abs(result["offset_seconds"] + 12) <= result["uncertainty_seconds"] + .000001
    assert result["monotonic_anchor"] == pytest.approx(timing["m4"] + 40, abs=.001)


@pytest.mark.parametrize("axis", ["wall", "monotonic"])
def test_injected_clock_jump_is_rejected(reference, axis):
    _, url = reference
    calls = [0]
    def jumping():
        calls[0] += 1
        return (time.time() if axis == "wall" else time.monotonic()) + (0 if calls[0] == 1 else 1)
    kwargs = {"wall_clock" if axis == "wall" else "monotonic": jumping}
    with pytest.raises(ValueError, match="No valid clock exchange"):
        clock.calibrate(url, node="n", reference_id="monitor", samples=1, **kwargs)


@pytest.mark.parametrize("change", [None, [], {"m1": float("nan")}, {"t4": float("inf")},
                                      {"m4": True}, {"m1": -1}, {"m4": 1e100},
                                      {"t1": 1e100}, {"m4": -1}])
def test_invalid_worker_clock_metadata_is_rejected(monkeypatch, change):
    from xlayer_telemetry import _http_transport as transport

    class Process:
        returncode = 0
        stdin = stdout = None
        def communicate(self, *args, **kwargs):
            now, mono = time.time(), time.monotonic()
            timing = dict(t1=now, t4=now, m1=mono, m4=mono)
            timing = timing | change if isinstance(change, dict) else change
            return json.dumps({"ok": True, "timing": timing}).encode() + b"\n{}", None
        def poll(self):
            return 0
        def terminate(self):
            pytest.fail("completed worker must not be signaled")
        kill = terminate

    monkeypatch.setattr(transport.subprocess, "Popen", lambda *args, **kwargs: Process())
    with pytest.raises(ValueError, match="Invalid HTTP clock timing"):
        transport.request_clock_bytes("http://unused", 1)


def test_transport_return_delay_counts_against_ttl(reference, monkeypatch):
    _, url = reference
    request = clock.request_clock_bytes
    def delayed(*args, **kwargs):
        value = request(*args, **kwargs)
        time.sleep(.05)
        return value
    monkeypatch.setattr(clock, "request_clock_bytes", delayed)
    with pytest.raises(ValueError, match="Exchange expired"):
        clock.calibrate(url, node="n", reference_id="monitor", samples=1, ttl=.01)


def test_reference_session_changes_in_real_exchange_reject_all_samples(reference):
    state, url = reference
    state["mode"] = "rotate"
    with pytest.raises(ValueError, match="No valid clock exchange"):
        clock.calibrate(url, node="n", reference_id="monitor", samples=3)
    assert len(state["requests"]) == 2


@pytest.mark.parametrize("rate", [.99, 1.01])
@pytest.mark.parametrize("start,end,total", [(.15, .151, .301), (.3, .301, .3011), (.0001, .0011, .3011)])
def test_drifting_clock_projection_preserves_boundary_order_and_receive_age(rate, start, end, total):
    before = (100., 40., 0., 0.)
    after = (100. + total * rate, 40. + total * rate, total, 0.)
    timing = {"m1": start, "m4": end}
    t1, t4, m4, elapsed, error = clock._project_exchange(before, after, timing, 10000)
    assert t1 == pytest.approx(100. + start * rate, abs=1e-10, rel=0)
    assert t4 == pytest.approx(100. + end * rate, abs=1e-10, rel=0)
    assert m4 == pytest.approx(40. + end * rate, abs=1e-10, rel=0)
    assert before[0] <= t1 <= t4 <= after[0]
    assert after[1] - m4 == pytest.approx((total - end) * rate)
    assert elapsed == end - start
    assert error >= (start + total - end) * .01


@pytest.mark.parametrize("rate", [.99, 1.01])
@pytest.mark.parametrize("startup,returned", [(.15, .15), (.3, 0.)])
def test_real_drifting_clock_calibration_can_be_saved_and_used(reference, monkeypatch, tmp_path, rate, startup, returned):
    from xlayer_telemetry import _http_transport as transport
    from xlayer_telemetry.time_alignment import CalibrationCache, boot_id, validate_snapshot
    _, url = reference
    popen = transport.subprocess.Popen
    request = clock.request_clock_bytes
    timings = []
    base_wall, base_mono = time.time(), time.monotonic()
    wall = lambda: base_wall + (time.monotonic() - base_mono) * rate + 12
    mono = lambda: time.monotonic() + 40

    def delayed(*args, **kwargs):
        time.sleep(startup)
        process = popen(*args, **kwargs)
        communicate = process.communicate
        def receive(*args, **kwargs):
            result = communicate(*args, **kwargs)
            time.sleep(returned)
            return result
        process.communicate = receive
        return process

    def record(*args, **kwargs):
        raw, timing = request(*args, **kwargs)
        timings.append(timing)
        return raw, timing

    monkeypatch.setattr(transport.subprocess, "Popen", delayed)
    monkeypatch.setattr(clock, "request_clock_bytes", record)
    result = clock.calibrate(url, node="n", reference_id="monitor", samples=1, timeout=2,
                             wall_clock=wall, monotonic=mono, drift_ppm=10000)
    validate_snapshot(result, node="n", boot=boot_id())
    timing = timings[0]
    assert result["round_trip_seconds"] < .15
    assert result["local_anchor"] == pytest.approx(base_wall + (timing["m4"] - base_mono) * rate + 12, abs=.0001)
    assert result["valid_from"] <= result["local_anchor"] <= wall()
    assert result["monotonic_anchor"] == pytest.approx(timing["m4"] + 40, abs=.0001)
    path = tmp_path / "clock.json"
    clock.save_calibration(path, result)
    mapping = CalibrationCache(path, node="n", wall_clock=wall, monotonic=mono)
    now = wall()
    projected = mapping.project(now, now)
    assert projected["time_alignment"]["status"] == "aligned"
    assert abs(projected["start"] - time.time()) <= projected["time_alignment"]["uncertainty_seconds"] + .001


@pytest.mark.parametrize("total", [.801, .800603])
def test_long_startup_does_not_extend_drifting_receive_ttl(monkeypatch, total):
    before = (100., 40., 0., 0.)
    after = (100. + total * .99, 40. + total, total, 0.)
    samples = iter([before, after])
    monkeypatch.setattr(clock, "_clock_sample", lambda *args: next(samples))
    monkeypatch.setattr(clock.time, "monotonic", lambda: total)
    def reply(url, **kwargs):
        nonce = parse_qs(urlsplit(url).query)["nonce"][0]
        raw = json.dumps(dict(schema_version=1, reference_id="monitor", reference_session="fixture",
                              nonce=nonce, clock_stable=True, t2=100.80005, t3=100.80005)).encode()
        return raw, dict(t1=100.8, t4=100.8001, m1=.8, m4=.8001)
    monkeypatch.setattr(clock, "request_clock_bytes", reply)
    with pytest.raises(ValueError, match="Exchange expired"):
        clock.calibrate("http://reference", node="n", reference_id="monitor", samples=1, ttl=.0005,
                        wall_clock=lambda: after[0], monotonic=lambda: after[1], drift_ppm=10000)


def test_calibrate_never_returns_snapshot_with_unrepresentable_ttl(monkeypatch):
    before, after = (100., 40., 0., 0.), (100.001, 40.001, .001, 0.)
    samples = iter([before, after])
    monkeypatch.setattr(clock, "_clock_sample", lambda *args: next(samples))
    monkeypatch.setattr(clock.time, "monotonic", lambda: .001)
    def reply(url, **kwargs):
        nonce = parse_qs(urlsplit(url).query)["nonce"][0]
        raw = json.dumps(dict(schema_version=1, reference_id="monitor", reference_session="fixture",
                              nonce=nonce, clock_stable=True, t2=100.0005, t3=100.0005)).encode()
        return raw, dict(t1=100., t4=100.001, m1=0., m4=.001)
    monkeypatch.setattr(clock, "request_clock_bytes", reply)
    with pytest.raises(ValueError, match="Invalid calibration bounds"):
        clock.calibrate("http://reference", node="n", reference_id="monitor", samples=1, ttl=1e-20,
                        wall_clock=lambda: after[0], monotonic=lambda: after[1])
