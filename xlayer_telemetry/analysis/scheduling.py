"""Elapsed scheduling is independent of calibrated observation timestamps."""
from pathlib import Path
import uuid

from ..measurements import finite_number
from ..time_alignment import sample_time, alignment_metadata


def clock_id(*, injected=False):
    if injected:return 'injected:'+uuid.uuid4().hex
    try:return 'monotonic:'+Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:return 'monotonic:'+uuid.uuid4().hex


def elapsed(engine):
    return getattr(engine,'elapsed_clock',engine.clock)()


def retry_state(engine, previous):
    value=previous.get('retry_timing') or {}
    if not isinstance(value,dict):return {}
    if value.get('clock_id')==getattr(engine,'elapsed_clock_id',None) and finite_number(value.get('first_attempt')) is not None and (
            value.get('retry_at') is None or finite_number(value.get('retry_at')) is not None):
        return value
    # Old journals and another boot have no trustworthy elapsed axis. A new,
    # bounded retry period is safer than subtracting unrelated epoch values.
    return {}


def ready(engine, record, settle, now, active_keys):
    if settle<=0:return True
    window=record.get('analysis_window')
    if not isinstance(window,dict):return True
    end=finite_number(window.get('end'))
    if end is None:return True
    key=(record.get('run_id'),record.get('node'),record.get('record_id'),end)
    active_keys.add(key)
    deadlines=getattr(engine,'_settle_deadlines',None)
    if deadlines is None:
        deadlines={};engine._settle_deadlines=deadlines
    if key not in deadlines:
        calibration=getattr(engine,'time_calibration',None)
        same_axis='time_alignment' not in window and calibration is None
        if calibration is not None:
            raw=engine.raw_clock()
            projected=calibration.project(raw,raw)
            aligned=projected.get('time_alignment',{})
            reference=sample_time({'observed_at':raw,'time_alignment':aligned})
            metadata=alignment_metadata(window)
            same_axis=(reference is not None and metadata.get('reference_id')==aligned.get('reference_id')
                       and metadata.get('reference_session')==aligned.get('reference_session'))
            if same_axis:now=reference
        age=max(0,now-end) if same_axis else 0
        deadlines[key]=elapsed(engine)+max(0,settle-age)
    return elapsed(engine)>=deadlines[key]
