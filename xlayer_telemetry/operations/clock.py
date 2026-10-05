"""Unprivileged optional clock exchange; independent of managed stack lifecycle."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import re
import socket
import threading
from pathlib import Path
import time
from urllib.parse import parse_qs, urlsplit
import uuid

from .._http_transport import request_clock_bytes
from ..fileio import atomic_write_text
from ..time_alignment import CalibrationCache, boot_id, estimate, read_calibration_file, validate_alignment, validate_snapshot
from .config import ConfigError


class ClockServer(HTTPServer):
    """Serial handler with one server-wide guard, never a thread per client."""
    _REQUEST_TIMEOUT = 2.0
    def __init__(self, address, *, reference_id: str, wall_clock=time.time, monotonic=time.monotonic):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', reference_id):
            raise ValueError('reference_id must be a stable identifier')
        self.reference_id, self.reference_session = reference_id, uuid.uuid4().hex
        self.wall_clock, self.monotonic = wall_clock, monotonic
        self.last_wall, self.last_monotonic = wall_clock(), monotonic()
        self._connection_lock = threading.Condition()
        self._active_connection = None
        self._closing = False
        self._guard = None
        super().__init__(address, ClockHandler)
        # Binding can fail and call server_close. Start only after it succeeds.
        try:
            self._guard = threading.Thread(target=self._guard_connection, daemon=True,
                                           name="xlayer-clock-connection-guard")
            self._guard.start()
        except (OSError, RuntimeError):
            self._guard = None  # An unstarted thread cannot be joined.
            self.server_close()
            raise

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(self._REQUEST_TIMEOUT)
        with self._connection_lock:
            if self._closing:
                connection.close()
                raise OSError('Clock server is closed')
            self._active_connection = (connection, time.monotonic() + self._REQUEST_TIMEOUT)
            self._connection_lock.notify_all()
        return connection, address

    def _close_connection(self):
        # Always called under the lock; retain the socket object, never an fd
        # which the next connection or an unrelated caller might reuse.
        if self._active_connection is not None:
            connection, _ = self._active_connection
            self._active_connection = None
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def _guard_connection(self):
        with self._connection_lock:
            while not self._closing:
                if self._active_connection is None:
                    self._connection_lock.wait()
                    continue
                remaining = self._active_connection[1] - time.monotonic()
                if remaining <= 0:
                    self._close_connection()
                else:
                    self._connection_lock.wait(remaining)

    def shutdown_request(self, request):
        with self._connection_lock:
            if self._active_connection is not None and self._active_connection[0] is request:
                self._active_connection = None
                self._connection_lock.notify_all()
            super().shutdown_request(request)

    def server_close(self):
        with self._connection_lock:
            self._closing = True
            self._close_connection()
            self._connection_lock.notify_all()
        super().server_close()
        if self._guard is not None:
            self._guard.join(timeout=1)


class ClockHandler(BaseHTTPRequestHandler):
    def handle(self):
        try:
            super().handle()
        except OSError:
            # The absolute connection guard can interrupt reads or writes.
            pass

    def do_GET(self):
        began, monotonic = self.server.wall_clock(), self.server.monotonic()
        age = monotonic-self.server.last_monotonic
        stable = abs(began-self.server.last_wall-age) <= .05 + max(0, age)*.0001
        self.server.last_wall, self.server.last_monotonic = began, monotonic
        if not stable:
            self.server.reference_session = uuid.uuid4().hex
        route = urlsplit(self.path)
        nonce = parse_qs(route.query).get('nonce', [''])[0]
        if route.path != '/time' or not re.fullmatch(r'[a-f0-9]{32}', nonce):
            self.send_error(400, 'Expected /time?nonce=<32 hex characters>')
            return
        ended = self.server.wall_clock()
        elapsed = self.server.monotonic()-monotonic
        valid = stable and abs(ended-began-elapsed) <= .05
        body = json.dumps({'schema_version': 1, 'reference_id': self.server.reference_id,
                           'reference_session': self.server.reference_session, 'nonce': nonce,
                           't2': began, 't3': ended, 'clock_stable': valid}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (OSError, TimeoutError):
            pass

    def log_message(self, *args):
        pass


def validate_request(url: str, node: str, reference_id: str, samples: int, timeout: float, ttl: float, drift_ppm: float) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}):
        raise ValueError('Reference URL must be an HTTP(S) origin without credentials')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', node) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', reference_id):
        raise ValueError('node and reference_id must be stable identifiers')
    if type(samples) is not int or not 1 <= samples <= 16:
        raise ValueError('samples must be an integer from 1 to 16')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (timeout, ttl, drift_ppm)) or not (0 < timeout <= 10 and 0 < ttl <= 3600 and 0 <= drift_ppm <= 10000):
        raise ValueError('timeout <= 10s, TTL <= 3600s and drift_ppm <= 10000 are required')


def _clock_sample(wall_clock, monotonic):
    # Bracket caller-supplied axes against the worker's machine monotonic axis.
    # This preserves clock injection without serializing arbitrary callables or
    # letting custom clocks bypass the real transport deadline.
    low = time.monotonic()
    wall, mono = wall_clock(), monotonic()
    high = time.monotonic()
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (wall, mono)):
        raise ValueError('Invalid client clock')
    return wall, mono, (low + high) / 2, (high - low) / 2


def _project_exchange(before, after, timing, drift_ppm):
    start_delta, end_delta = timing['m1'] - before[2], timing['m4'] - after[2]
    elapsed = timing['m4'] - timing['m1']
    if start_delta < -before[3] or end_delta > after[3]:
        raise ValueError('Invalid clock timing boundary')
    # Startup/IPC are not network RTT. Retain their worst-case drift, at the
    # same bounded rate assumed by the saved calibration, and both brackets
    # before aligning the sampled axes to the actual request boundaries.
    error = before[3] + after[3] + (abs(start_delta) + abs(end_delta)) * drift_ppm / 1e6
    if abs(after[1] - before[1] - (after[2] - before[2])) > .05 + error:
        raise ValueError('Client monotonic clock changed')
    raw_t1, raw_t4 = before[0] + start_delta, after[0] + end_delta
    discrepancy = raw_t4 - raw_t1 - elapsed
    if abs(discrepancy) > .05:
        raise ValueError('Client wall clock changed')
    duration = after[2] - before[2]
    if (duration <= 0 or not 0 <= start_delta <= timing['m4'] - before[2] <= duration
            or after[0] < before[0] or after[1] < before[1]):
        raise ValueError('Invalid clock timing boundary')
    # Interpolate one clock axis across both samples. Projecting each endpoint
    # independently at unit rate can reverse a short exchange under legal drift,
    # or move its receive anchor into the future after asymmetric IPC delays.
    t1 = before[0] + (after[0] - before[0]) * start_delta / duration
    receive_fraction = (timing['m4'] - before[2]) / duration
    t4 = before[0] + (after[0] - before[0]) * receive_fraction
    m4 = before[1] + (after[1] - before[1]) * receive_fraction
    error += max(abs(t1 - raw_t1), abs(t4 - raw_t4), abs(discrepancy) / 2)
    return t1, t4, m4, elapsed, error


def calibrate(url: str, *, node: str, reference_id: str, samples: int = 5,
              timeout: float = 2, ttl: float = 60, drift_ppm: float = 100,
              wall_clock=time.time, monotonic=time.monotonic) -> dict:
    validate_request(url, node, reference_id, samples, timeout, ttl, drift_ppm)
    observations, session, changed = [], None, False
    for _ in range(samples):
        nonce = uuid.uuid4().hex
        try:
            before = _clock_sample(wall_clock, monotonic)
            raw, timing = request_clock_bytes(url.rstrip('/')+'/time?nonce='+nonce, timeout=timeout)
            after = _clock_sample(wall_clock, monotonic)
            t1, t4, m4, elapsed, projection_error = _project_exchange(before, after, timing, drift_ppm)
            reply = json.loads(raw) if len(raw) <= 4096 else None
            if (not isinstance(reply, dict) or reply.get('schema_version') != 1
                    or reply.get('nonce') != nonce or reply.get('reference_id') != reference_id
                    or reply.get('clock_stable') is not True
                    or not isinstance(reply.get('reference_session'), str) or not reply['reference_session']):
                raise ValueError('Invalid clock reply')
            if session is not None and session != reply['reference_session']:
                changed = True
                break
            session = reply['reference_session']
            measured = estimate(t1, reply.get('t2'), reply.get('t3'), t4, elapsed=elapsed)
            measured['uncertainty_seconds'] += projection_error
            observations.append((measured | {'local_anchor': t4, 'monotonic_anchor': m4,
                                            'valid_from': t1, 'valid_until': t4+ttl}, timing['m4']))
        except (OSError, RuntimeError, ValueError, TypeError):
            # Bounded attempts; do not leak endpoint/auth details into SDK logs.
            continue
    if changed or not observations:
        raise ValueError('No valid clock exchange; check reference ID, URL and connectivity')
    now, now_mono, real_now = wall_clock(), monotonic(), time.monotonic()
    observations = [v for v, received in observations if 0 <= real_now-received <= ttl
                    and v['local_anchor'] <= now <= v['valid_until']
                    and now_mono >= v['monotonic_anchor']
                    and abs(now-v['local_anchor']-(now_mono-v['monotonic_anchor'])) <=
                    .05+(now_mono-v['monotonic_anchor'])*drift_ppm/1e6]
    if not observations:
        raise ValueError('Exchange expired or client clock changed; reduce samples/timeout or increase TTL')
    best = min(observations, key=lambda item: item['round_trip_seconds'])
    result = best | {'schema_version': 1, 'method': 'four_timestamp', 'node': node,
                     'boot_id': boot_id(), 'reference_id': reference_id,
                     'reference_session': session, 'drift_ppm': drift_ppm}
    return validate_snapshot(result, node=node, boot=result['boot_id'])


def save_calibration(path, measured: dict):
    # Retain a small history so a refresh does not invalidate spans that began
    # under the preceding calibration. Reference/boot changes discard history.
    validate_snapshot(measured, node=measured['node'], boot=boot_id())
    history = []
    try:
        prior = read_calibration_file(path)
        if all(prior.get(k) == measured[k] for k in ('node', 'boot_id', 'reference_id', 'reference_session')):
            history = [{k: v for k, v in prior.items() if k != 'history'}, *prior.get('history', [])]
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    valid_history = []
    for value in history[:31]:
        try:
            validate_snapshot(value, node=measured['node'], boot=measured['boot_id'])
            bound = value['uncertainty_seconds']+measured['uncertainty_seconds']+abs(value['local_anchor']-measured['local_anchor'])*measured['drift_ppm']/1e6
            if value['valid_until'] >= measured['local_anchor'] and abs(value['offset_seconds']-measured['offset_seconds']) <= bound:
                valid_history.append(value)
        except (ValueError, TypeError, KeyError):
            continue
    history = valid_history
    atomic_write_text(path, json.dumps(measured | {'history': history}, allow_nan=False)+'\n')


def add_commands(commands):
    clock = commands.add_parser('clock', help='Optional userspace time mapping; never changes OS clocks')
    actions = clock.add_subparsers(dest='clock_action', required=True)
    serve = actions.add_parser('serve', help='Serve monitoring-host clock in foreground (same clock as Prometheus)')
    serve.add_argument('--bind', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=19120)
    serve.add_argument('--reference-id', required=True)
    measure = actions.add_parser('calibrate', help='Measure node offset and atomically save optional calibration')
    measure.add_argument('--url', required=True)
    measure.add_argument('--reference-id', required=True)
    measure.add_argument('--node', required=True)
    measure.add_argument('--file', type=Path, required=True)
    measure.add_argument('--samples', type=int, default=5)
    measure.add_argument('--timeout', type=float, default=2)
    measure.add_argument('--ttl', type=float, default=60)
    measure.add_argument('--drift-ppm', type=float, default=100)
    measure.add_argument('--interval', type=float, default=0, help='Refresh in foreground every N seconds; 0 = once')
    status = actions.add_parser('status', help='Show node-local calibration validity as JSON')
    status.add_argument('--file', type=Path, required=True)
    status.add_argument('--node', required=True)


def execute(args):
    if args.clock_action == 'serve':
        if not 1 <= args.port <= 65535:
            raise ConfigError('Clock port must be between 1 and 65535')
        with ClockServer((args.bind, args.port), reference_id=args.reference_id) as server:
            print(f'Clock reference {args.reference_id}: http://{args.bind}:{args.port} (foreground)', flush=True)
            server.serve_forever()
        return 0
    path = args.file.expanduser().absolute()
    if args.clock_action == 'status':
        now = time.time()
        mapped = CalibrationCache(path, node=args.node).project(now, now)
        quality = validate_alignment(mapped, mapped['time_alignment'].get('reference_id'), max_uncertainty=1)
        print(json.dumps(mapped | {'quality': quality}, indent=2))
        return 0 if quality['status'] == 'aligned' else 1
    if not math.isfinite(args.interval) or args.interval < 0 or args.interval >= args.ttl:
        raise ConfigError('Refresh interval must be 0 or positive and smaller than TTL')
    try:
        validate_request(args.url, args.node, args.reference_id, args.samples, args.timeout, args.ttl, args.drift_ppm)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    while True:
        try:
            value = calibrate(args.url, node=args.node, reference_id=args.reference_id,
                              samples=args.samples, timeout=args.timeout, ttl=args.ttl, drift_ppm=args.drift_ppm)
            save_calibration(path, value)
            print(json.dumps({'status': 'aligned', 'file': str(path), 'offset_seconds': value['offset_seconds'],
                              'uncertainty_seconds': value['uncertainty_seconds']}), flush=True)
        except ValueError as exc:
            if args.interval == 0:
                raise ConfigError(str(exc)) from exc
            print(json.dumps({'status': 'unknown', 'issue': 'calibration_refresh_failed'}), flush=True)
        if args.interval == 0:
            return 0
        time.sleep(args.interval)
