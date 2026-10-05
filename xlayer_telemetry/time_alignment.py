"""Optional node-wall to monitoring-wall mapping; never changes a system clock."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time

from .measurements import finite_number


ENV_FILE = 'TELEMETRY_TIME_CALIBRATION_FILE'
MAX_FILE_BYTES = 65536


def alignment_metadata(window: dict) -> dict:
    value = window.get('time_alignment')
    return value if isinstance(value, dict) else {}


def read_calibration_file(path: Path) -> object:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Calibration must be a regular local file')
        data = stream.read(MAX_FILE_BYTES+1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError('Calibration exceeds size limit')
    return json.loads(data)


def boot_id() -> str:
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def estimate(t1: float, t2: float, t3: float, t4: float, *, elapsed: float) -> dict:
    """Four timestamps: client send, server receive/send, client receive.

    Offset is reference minus node. RTT/2 bounds path asymmetry under the
    nonnegative-delay, stable-clock assumption, not absolute UTC accuracy.
    """
    if any(finite_number(value) is None for value in (t1, t2, t3, t4, elapsed)):
        raise ValueError('Exchange timestamps must be finite numbers')
    processing = t3-t2
    rtt = elapsed-processing
    if elapsed < 0 or processing < 0 or rtt < 0 or abs(t4-t1-elapsed) > .05:
        raise ValueError('Invalid exchange or clock discontinuity')
    return {'offset_seconds': ((t2-t1)+(t3-t4))/2,
            'round_trip_seconds': rtt, 'uncertainty_seconds': rtt/2}


def validate_snapshot(value: object, *, node: str, boot: str) -> dict:
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value.get('schema_version') != 1 or value.get('method') != 'four_timestamp':
        raise ValueError('Invalid calibration schema')
    if value.get('node') != node or not boot or value.get('boot_id') != boot:
        raise ValueError('Calibration node/boot mismatch')
    if not isinstance(value.get('reference_id'), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value['reference_id']):
        raise ValueError('Calibration needs reference_id')
    keys = ('offset_seconds', 'round_trip_seconds', 'uncertainty_seconds', 'local_anchor',
            'monotonic_anchor', 'valid_from', 'valid_until', 'drift_ppm')
    if any(finite_number(value.get(key)) is None for key in keys):
        raise ValueError('Invalid calibration numeric field')
    if not (0 <= value['round_trip_seconds'] <= 60
            and value['uncertainty_seconds'] >= value['round_trip_seconds']/2
            and 0 <= value['drift_ppm'] <= 10000
            and 0 <= value['valid_from'] <= value['local_anchor'] < value['valid_until']
            and value['valid_until']-value['local_anchor'] <= 3600):
        raise ValueError('Invalid calibration bounds')
    return value


class CalibrationCache:
    """At most one small file read per second; failures only mark time unknown."""
    def __init__(self, path: Path, *, node: str, wall_clock=time.time,
                 monotonic=time.monotonic, boot_id=boot_id):
        self.path, self.node = Path(path), node
        self.wall_clock, self.monotonic, self.boot_id = wall_clock, monotonic, boot_id
        self._next_read = float('-inf')
        self._values = []
        self._issue = 'calibration_unavailable'
        self._lock = threading.Lock()
        self._pid = os.getpid()

    @classmethod
    def from_env(cls, node: str) -> CalibrationCache | None:
        path = os.environ.get(ENV_FILE)
        return cls(Path(path), node=node) if path else None

    def project(self, start: float, end: float) -> dict:
        result = {'start': start, 'end': end}
        try:
            # A fork copies a possibly held lock, not the thread that owns it.
            # Reset before acquiring that lock and revalidate the child's file.
            if self._pid != os.getpid():
                self._lock = threading.Lock()
                self._next_read = float('-inf')
                self._values = []
                self._issue = 'calibration_unavailable'
                self._pid = os.getpid()
            with self._lock:
                now_mono = self.monotonic()
                if now_mono >= self._next_read:
                    self._next_read = now_mono+1
                    self._values = []
                    try:
                        current = validate_snapshot(read_calibration_file(self.path), node=self.node, boot=self.boot_id())
                        self._values = [current]
                        history = current.get('history', [])
                        if not isinstance(history, list) or len(history) > 31:
                            raise ValueError('Invalid calibration history')
                        for prior in history:
                            value = validate_snapshot(prior, node=self.node, boot=current['boot_id'])
                            if (value['reference_id'], value.get('reference_session')) == (current['reference_id'], current.get('reference_session')):
                                self._values.append(value)
                    except (OSError, ValueError, TypeError, KeyError):
                        self._issue = 'calibration_unavailable_or_invalid'
                        self._values = []
                if not self._values:
                    raise ValueError(self._issue)
                now = self.wall_clock()
                if any(finite_number(v) is None for v in (start, end, now)) or start > end:
                    raise ValueError('invalid_source_window')
                usable = [v for v in self._values if v['valid_from'] <= start <= end <= v['valid_until']
                          and v['local_anchor'] <= now <= v['valid_until']]
                if not usable:
                    raise ValueError('calibration_expired_or_window_uncovered')
                value = min(usable, key=lambda v: v['uncertainty_seconds'] + max(abs(start-v['local_anchor']), abs(end-v['local_anchor']))*v['drift_ppm']/1e6)
                age = now_mono-value['monotonic_anchor']
                if age < 0 or abs(now-value['local_anchor']-age) > .05 + age*value['drift_ppm']/1e6:
                    raise ValueError('local_clock_discontinuity')
                uncertainty = value['uncertainty_seconds'] + max(abs(start-value['local_anchor']), abs(end-value['local_anchor']))*value['drift_ppm']/1e6
                alignment = {key: value[key] for key in ('method', 'node', 'reference_id', 'offset_seconds',
                                                         'valid_from', 'valid_until', 'local_anchor',
                                                         'drift_ppm', 'round_trip_seconds')}
                alignment.update(status='aligned', uncertainty_seconds=uncertainty,
                                 exchange_uncertainty_seconds=value['uncertainty_seconds'],
                                 raw_window={'start': start, 'end': end})
                if value.get('reference_session'):
                    alignment['reference_session'] = value['reference_session']
                result.update(start=start+value['offset_seconds'], end=end+value['offset_seconds'],
                              time_alignment=alignment)
        except (OSError, ValueError, TypeError, OverflowError) as exc:
            result['time_alignment'] = {'status': 'unknown', 'method': 'four_timestamp',
                                        'node': self.node, 'issue': str(exc)}
        return result


def validate_alignment(window: dict, reference_id: str | None, *, max_uncertainty: float) -> dict:
    """Validate persisted mapping at its event time, not today's calibration."""
    try:
        alignment = window['time_alignment']
        if not isinstance(alignment, dict) or alignment.get('status') != 'aligned':
            raise ValueError('calibration_unavailable')
        if not reference_id or alignment['reference_id'] != reference_id or alignment['method'] != 'four_timestamp':
            raise ValueError('calibration_reference_mismatch')
        raw = alignment['raw_window']
        keys = ('offset_seconds', 'uncertainty_seconds', 'exchange_uncertainty_seconds', 'valid_from',
                'valid_until', 'local_anchor', 'drift_ppm', 'round_trip_seconds')
        if any(finite_number(alignment.get(key)) is None for key in keys) or any(
                finite_number(v) is None for v in (window['start'], window['end'], raw['start'], raw['end'])):
            raise ValueError('invalid_calibration_metadata')
        expected = alignment['exchange_uncertainty_seconds'] + max(
            abs(raw['start']-alignment['local_anchor']), abs(raw['end']-alignment['local_anchor']))*alignment['drift_ppm']/1e6
        if not (0 <= alignment['round_trip_seconds'] <= 60
                and alignment['exchange_uncertainty_seconds'] >= alignment['round_trip_seconds']/2
                and 0 <= alignment['drift_ppm'] <= 10000
                and 0 <= alignment['valid_from'] <= raw['start'] <= raw['end'] <= alignment['valid_until']
                and alignment['valid_from'] <= alignment['local_anchor'] < alignment['valid_until']
                and alignment['valid_until']-alignment['local_anchor'] <= 3600
                and alignment['uncertainty_seconds'] >= expected
                and math.isclose(window['start'], raw['start']+alignment['offset_seconds'], abs_tol=1e-6, rel_tol=0)
                and math.isclose(window['end'], raw['end']+alignment['offset_seconds'], abs_tol=1e-6, rel_tol=0)):
            raise ValueError('invalid_calibration_metadata')
        return {'status': 'aligned' if alignment['uncertainty_seconds'] <= max_uncertainty else 'unsafe',
                'reference': reference_id, 'method': 'four_timestamp',
                'uncertainty_seconds': alignment['uncertainty_seconds'], 'max_uncertainty_seconds': max_uncertainty}
    except (ValueError, TypeError, KeyError):
        return {'status': 'unknown', 'method': 'four_timestamp', 'issue': 'invalid_or_unavailable_time_mapping'}


def event_window(record: dict, *, reference_id: str | None = None) -> tuple[float | None, float | None]:
    """Shared event query window; mismatched/unknown mapping is never local time."""
    start = finite_number(record.get('start_time_unix_nano', record.get('timestamp_unix_nano')))
    end = finite_number(record.get('end_time_unix_nano', start))
    if start is None or end is None or record.get('boundary_accuracy') == 'clock_discontinuity':
        return None, None
    if 'time_alignment' in record:
        alignment = record['time_alignment']
        if not isinstance(alignment, dict) or alignment.get('node') != record.get('node'):
            return None, None
        raw = alignment.get('raw_window', {})
        if not isinstance(raw, dict) or raw.get('start') != start/1e9 or raw.get('end') != end/1e9:
            return None, None
        offset = finite_number(alignment.get('offset_seconds'))
        if offset is None:
            return None, None
        window = {'start': start/1e9+offset, 'end': end/1e9+offset, 'time_alignment': alignment}
        limit = min(1., (end-start)/1e10) if end > start else 1.
        if validate_alignment(window, reference_id or alignment.get('reference_id'), max_uncertainty=limit)['status'] != 'aligned':
            return None, None
        return window['start'], window['end']
    # In a calibrated investigation, an uncalibrated remote event cannot be joined.
    return (None, None) if reference_id else (start/1e9, end/1e9)


def sample_time(record: dict, *, key: str = 'observed_at') -> float | None:
    """Freshness gauge in reference time; raw local time still orders snapshots."""
    raw = finite_number(record.get(key))
    if 'time_alignment' not in record:
        return raw
    alignment = record.get('time_alignment')
    if not isinstance(alignment, dict) or raw is None or (record.get('node') and alignment.get('node') != record['node']):
        return None
    offset = finite_number(alignment.get('offset_seconds'))
    if offset is None or alignment.get('raw_window') != {'start': raw, 'end': raw}:
        return None
    window = {'start': raw+offset, 'end': raw+offset, 'time_alignment': alignment}
    return window['end'] if validate_alignment(window, alignment.get('reference_id'), max_uncertainty=1)['status'] == 'aligned' else None


def observation_time(record: dict) -> float | None:
    """Use the persisted reference axis when ordering calibrated step history."""
    window = record.get('analysis_window')
    if isinstance(window, dict) and isinstance(window.get('time_alignment'), dict):
        alignment = window['time_alignment']
        if validate_alignment(window, alignment.get('reference_id'), max_uncertainty=1)['status'] == 'aligned':
            return window['end']
    return finite_number(record.get('observed_at'))


def reference_now(calibration: CalibrationCache | None, raw: float) -> float:
    if calibration is not None:
        projected = calibration.project(raw, raw)
        value = sample_time({'observed_at': raw, 'time_alignment': projected['time_alignment']})
        if value is not None:
            return value
    return raw
