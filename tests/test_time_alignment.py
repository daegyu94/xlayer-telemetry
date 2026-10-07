"""Userspace time mapping must never manufacture precise cross-node evidence."""
import json
from pathlib import Path

import pytest

from xlayer_telemetry.time_alignment import CalibrationCache, estimate, validate_alignment
from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.step_history import StepHistoryWriter


def calibration(**changes):
    return dict(schema_version=1, method='four_timestamp', node='gpu-a', boot_id='boot',
                reference_id='monitor', offset_seconds=-12, round_trip_seconds=.04,
                uncertainty_seconds=.02, local_anchor=112., monotonic_anchor=50.,
                valid_from=111.96, valid_until=172., drift_ppm=100., **changes)


def cache(tmp_path, *, raw=122., mono=60., boot='boot', payload=None):
    path = tmp_path / 'clock.json'
    path.write_text(json.dumps(payload or calibration()))
    return CalibrationCache(path, node='gpu-a', wall_clock=lambda: raw,
                            monotonic=lambda: mono, boot_id=lambda: boot)


def test_four_timestamps_offset_sign_and_asymmetric_error_bound():
    measured = estimate(112, 100.01, 100.015, 112.045, elapsed=.045)
    assert measured['offset_seconds'] == pytest.approx(-12.01)
    assert measured['round_trip_seconds'] == pytest.approx(.04)
    assert measured['uncertainty_seconds'] == pytest.approx(.02)
    assert abs(measured['offset_seconds'] + 12) <= measured['uncertainty_seconds']


@pytest.mark.parametrize('times,elapsed', [((1, 2, 2, 0), 1), ((1, 2, 1, 3), 2),
                                         ((1, 2, 5, 2), 1), ((1, 2, 2, 13), 1),
                                         ((1, float('nan'), 2, 3), 2)])
def test_reject_invalid_exchange_or_client_clock_jump(times, elapsed):
    with pytest.raises(ValueError):
        estimate(*times, elapsed=elapsed)


def test_mapping_preserves_raw_window_and_drift_quality(tmp_path):
    mapped = cache(tmp_path).project(120, 122)
    assert mapped['start'] == 108 and mapped['end'] == 110
    alignment = mapped['time_alignment']
    assert alignment['raw_window'] == {'start': 120, 'end': 122}
    assert alignment['uncertainty_seconds'] >= .021
    assert validate_alignment(mapped, 'monitor', max_uncertainty=1)['status'] == 'aligned'
    assert validate_alignment(mapped, 'other', max_uncertainty=1)['status'] == 'unknown'
    assert validate_alignment(mapped, 'monitor', max_uncertainty=.001)['status'] == 'unsafe'


@pytest.mark.parametrize('raw,mono,boot', [(180, 118, 'boot'), (125, 60, 'boot'),
                                          (122, 60, 'reboot')])
def test_stale_jump_and_reboot_do_not_apply_mapping(tmp_path, raw, mono, boot):
    mapped = cache(tmp_path, raw=raw, mono=mono, boot=boot).project(raw-2, raw)
    assert mapped['start'] == raw-2
    assert mapped['time_alignment']['status'] != 'aligned'


def test_event_and_step_use_shared_axis_without_changing_duration(tmp_path):
    mapping = cache(tmp_path)
    ticks = iter([120_000_000_000, 122_000_000_000])
    mono = iter([58_000_000_000, 60_000_000_000])
    recorder = EventRecorder(tmp_path, CorrelationContext('r', 'agent', 'rollout', '0', 'gpu-a'),
                             clock_ns=lambda: next(ticks), monotonic_ns=lambda: next(mono),
                             time_calibration=mapping)
    with recorder.span('tool.call', phase='environment', attributes={'tool': 'pytest'},
                       trace_id='trace', parent_span_id='parent'):
        pass
    span = json.loads(recorder.path.read_text())
    assert span['start_time_unix_nano'] == 120_000_000_000
    assert span['event_time_unix_nano'] == 108_000_000_000
    assert span['start_time_ms'] == 108000
    assert span['duration_seconds'] == 2
    assert span['boundary_accuracy'] == 'calibrated'
    assert span['parent_span_id'] == 'parent'
    writer = StepHistoryWriter(tmp_path / 'steps.jsonl', run_id='r', node='gpu-a', worker_id='0',
                               clock=lambda: 122, time_calibration=mapping)
    step = writer.append({'step': 1, 'data': {'timing_s/step': 2}})
    assert step['observed_at'] == 122
    assert step['correlation_observed_at'] == 110
    assert step['analysis_window']['start'] == 108
    assert step['boundary_accuracy'] == 'calibrated_approximate'
    replay = writer.append({'step': 2, 'data': {'timing_s/step': 2}}, live=False)
    assert replay['analysis_window']['start'] is None


def test_corrupt_missing_and_wrong_node_are_failure_isolated(tmp_path):
    mapping = cache(tmp_path, payload={'schema_version': 1})
    assert mapping.project(120, 122)['time_alignment']['status'] == 'unknown'
    (tmp_path / 'clock.json').unlink()
    assert mapping.project(120, 122)['time_alignment']['status'] == 'unknown'
    wrong = calibration()
    wrong['node'] = 'gpu-b'
    assert cache(tmp_path, payload=wrong).project(120, 122)['time_alignment']['status'] == 'unknown'


def test_historical_mapping_is_not_recomputed_with_current_offset(tmp_path):
    old = cache(tmp_path).project(120, 122)
    assert validate_alignment(old, 'monitor', max_uncertainty=1)['status'] == 'aligned'
    broken = json.loads(json.dumps(old))
    broken['end'] += 10
    assert validate_alignment(broken, 'monitor', max_uncertainty=1)['status'] == 'unknown'


def test_diagnosis_uses_mapped_window_and_preserves_os_clock_warning(tmp_path):
    from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine
    calls = []
    def stats(value):
        return dict(min=value, max=value, mean=value, last=value, sample_count=3)
    class Backend:
        def query_range(self, expression, start, end, step):
            calls.append((expression, start, end))
            if 'timex' in expression: return stats(0)
            if 'node_time_seconds' in expression:
                return stats(0 if expression.startswith('time()') else 12)
            if 'kv_cache' in expression: return stats(.99)
            if 'preemptions' in expression: return stats(5) | {'max_series_delta': 2}
            if 'waiting' in expression: return stats(5)
            return None
    mapping = cache(tmp_path)
    writer = StepHistoryWriter(tmp_path / 'steps.jsonl', run_id='r', node='gpu-a', worker_id='0',
                               clock=lambda: 122, time_calibration=mapping)
    step = writer.append({'step': 1, 'data': {'timing_s/step': 2}})
    config = {'prometheus': {'url': 'unused'}, 'cluster': 'lab',
              'clock': {'calibration_reference': 'monitor'}}
    engine = DiagnosticEngine(config, prometheus=Backend())
    report = engine.analyze(step, [])
    assert report['clock_quality']['status'] == 'aligned'
    assert report['clock_quality']['system_clock_screening']['status'] == 'unsafe'
    assert any(c['id'] == 'kv_cache_pressure' for c in report['candidates'])
    assert all(start == 108 and end == 110 for _, start, end in calls)
    wrong = DiagnosticEngine(config | {'clock': {'calibration_reference': 'wrong'}}, prometheus=Backend()).analyze(step, [])
    assert wrong['verdict'] == 'insufficient_data' and not wrong['candidates']
    assert not wrong['comparison']['signals']


def test_calibrated_tool_join_requires_matching_reference(tmp_path):
    from xlayer_telemetry.analysis.diagnostics import tool_span_window
    mapping = cache(tmp_path)
    ticks = iter([120_000_000_000, 122_000_000_000])
    rec = EventRecorder(tmp_path, CorrelationContext('r', 'agent', 'rollout', '0', 'gpu-a'),
                        clock_ns=lambda: next(ticks), time_calibration=mapping)
    with rec.span('tool.call', phase='environment', attributes={'tool': 'pytest'}): pass
    result = tool_span_window(tmp_path, 'r', 108, 110, reference_id='monitor')
    assert result['max'] == 2
    assert tool_span_window(tmp_path, 'r', 108, 110, reference_id='wrong') is None


def test_baseline_with_unregistered_time_and_short_window_are_withheld(tmp_path):
    from xlayer_telemetry.analysis.clock_quality import assess_interval
    def query(*args): return dict(min=0, max=0, last=0)
    mapped = cache(tmp_path).project(120, 122)
    config = {'calibration_reference': 'monitor'}
    assert assess_interval(query, cluster='lab', nodes=['gpu-a'], window=mapped,
                           producer_node='gpu-a', config=config)['status'] == 'aligned'
    assert assess_interval(query, cluster='lab', nodes=['gpu-a'], window={'start': 108, 'end': 110},
                           producer_node='gpu-a', config=config)['status'] == 'unknown'
    short = cache(tmp_path).project(121.99, 122)
    assert assess_interval(query, cluster='lab', nodes=['gpu-a'], window=short,
                           producer_node='gpu-a', config=config)['status'] == 'unsafe'
    # 3FS producer DateTime values are not Prometheus scrape timestamps.
    assert assess_interval(query, cluster='lab', nodes=['gpu-a'], window=mapped,
                           producer_node='gpu-a', config=config, producer_clock_nodes=['storage'])['status'] != 'aligned'


def test_real_http_exchange_and_second_refresh_cover_longer_interval(tmp_path):
    import threading
    import time
    from xlayer_telemetry.operations.clock import ClockServer, calibrate, save_calibration
    server = ClockServer(('127.0.0.1', 0), reference_id='monitor')
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f'http://127.0.0.1:{server.server_port}'
        clock = lambda: time.time()+12
        first = calibrate(url, node='gpu-a', reference_id='monitor', samples=3, ttl=60, wall_clock=clock)
        assert first['offset_seconds'] == pytest.approx(-12, abs=.05)
        path = tmp_path / 'clock.json'
        save_calibration(path, first)
        began = clock()
        second = calibrate(url, node='gpu-a', reference_id='monitor', samples=2, ttl=60, wall_clock=clock)
        save_calibration(path, second)
        mapping = CalibrationCache(path, node='gpu-a', wall_clock=clock)
        window = mapping.project(began, clock())
        assert window['time_alignment']['status'] == 'aligned'
        assert window['start'] == pytest.approx(began-12, abs=.05)
        with pytest.raises(ValueError):
            calibrate(url, node='gpu-a', reference_id='other', samples=1)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
    assert not worker.is_alive()


def test_cli_clock_help_and_status_without_user_config(tmp_path):
    from xlayer_telemetry.cli import main
    assert main(['clock', 'status', '--file', str(tmp_path / 'missing'), '--node', 'gpu-a']) == 1
    with pytest.raises(SystemExit) as exit:
        main(['clock', 'calibrate', '--help'])
    assert exit.value.code == 0


def test_metric_freshness_projects_only_timestamp_not_sample_value(tmp_path):
    from xlayer_telemetry.metrics import Metric, MetricEmitter
    from xlayer_telemetry.metrics.textfile import build_metrics
    mapping = cache(tmp_path)
    emitter = MetricEmitter(tmp_path, run_id='r', node='gpu-a', producer='verl', role='trainer',
                            worker_id='0', clock=lambda: 122, time_calibration=mapping)
    path = emitter.emit(step=1, samples=[Metric('training_loss', .5)])
    snap = json.loads(path.read_text())
    assert snap['observed_at'] == 122
    metrics = build_metrics([snap])
    assert next(m.value for m in metrics if m.name == 'training_sample_timestamp_seconds') == 110
    assert next(m.value for m in metrics if m.name == 'training_loss') == .5
    snap['time_alignment']['status'] = 'unknown'
    assert not any(m.name == 'training_sample_timestamp_seconds' for m in build_metrics([snap]))


def test_llm_direct_collection_obeys_same_calibration_policy(tmp_path, monkeypatch):
    from xlayer_telemetry.analysis import llm_diagnosis as llm
    calls = []
    class Backend:
        def __init__(self, *args): pass
        def query_range(self, expr, *args): return dict(min=12, max=12, last=12)
        def query_range_detail(self, expr, start, end, *args):
            calls.append((start, end))
            return {'aggregate': None, 'series': []}
    monkeypatch.setattr(llm, 'PrometheusClient', Backend)
    mapped = cache(tmp_path).project(120, 122)
    config = {'prometheus_url': 'http://unused', 'cluster': 'lab',
              'context': {'node': 'gpu-a'}, 'clock': {'nodes': ['gpu-a'], 'calibration_reference': 'monitor'},
              'current_interval': mapped, 'baseline_interval': None,
              'queries': [{'signal': 'gpu_util', 'query': 'gpu_util', 'unit': '%', 'scope': 'device'}]}
    packet = llm.collect_packet(config)
    assert packet['clock_quality']['status'] == 'aligned'
    assert calls == [(108, 110)]
    assert llm.model_view(packet)['clock_quality']['method'] == 'four_timestamp'


def test_calibrated_baseline_survives_local_wall_clock_reversal(tmp_path):
    from xlayer_telemetry.analysis.diagnosis_analysis import select_baseline
    old = cache(tmp_path).project(120, 122)
    new_payload = calibration()
    new_payload.update(offset_seconds=8, local_anchor=112, valid_from=111, valid_until=180)
    newer = cache(tmp_path, raw=112, mono=50, payload=new_payload).project(111, 112)
    common = dict(run_id='r', node='gpu-a', worker_id='0', boundary_scope='rl_step', step_duration_seconds=2)
    previous = common | {'observed_at': 122, 'analysis_window': old}
    current = common | {'observed_at': 112, 'analysis_window': newer}
    assert select_baseline(current, [previous]) == previous


def test_alignment_cache_reads_once_under_concurrent_event_load(tmp_path, monkeypatch):
    import concurrent.futures
    mapping = cache(tmp_path)
    import os
    original, reads = os.open, []
    def tracked(path, *args, **kwargs):
        if Path(path) == tmp_path / 'clock.json': reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'open', tracked)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: mapping.project(120, 122), range(500)))
    assert all(r['end'] == 110 for r in results)
    assert len(reads) == 1


def test_timeline_accepts_calibrated_spans_without_calling_them_exact():
    root = Path(__file__).resolve().parents[1]
    dashboard = json.loads((root / 'examples/dashboards/cross-layer-timeline.json').read_text())
    lane = next(p for p in dashboard['panels'] if p['id'] == 2)
    assert 'calibrated' in lane['targets'][0]['expr']
    assert 'calibrated' in lane['title'].lower()
    assert 'span_boundary_label' in lane['targets'][0]['expr']
    assert 'or .span_boundary_label (printf "%s [%s]" .phase .boundary_accuracy)' in lane['targets'][0]['expr']
    assert '[exact, node clock]' not in lane['targets'][0]['expr']


def test_fifo_calibration_cannot_block_workload(tmp_path):
    import os
    path = tmp_path / 'fifo'
    os.mkfifo(path)
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-c',
        'from pathlib import Path; from xlayer_telemetry.time_alignment import CalibrationCache; '
        'import sys; print(CalibrationCache(Path(sys.argv[1]), node="gpu-a").project(120, 122))', str(path)],
        capture_output=True, text=True, timeout=1)
    assert result.returncode == 0 and 'unknown' in result.stdout


def test_timeout_attempts_and_bad_configuration_are_bounded(tmp_path, monkeypatch):
    from xlayer_telemetry.operations import clock
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(kwargs['timeout'])
        raise TimeoutError('down')
    monkeypatch.setattr(clock, 'request_clock_bytes', unavailable)
    with pytest.raises(ValueError):
        clock.calibrate('http://127.0.0.1:1', node='gpu-a', reference_id='monitor', samples=3, timeout=.1)
    assert calls == [.1]*3
    with pytest.raises(ValueError):
        clock.calibrate('http://127.0.0.1:1', node='gpu-a', reference_id='monitor', samples=100000)
    assert len(calls) == 3


def test_reference_restart_invalidates_mixed_exchange_and_history(tmp_path, monkeypatch):
    from xlayer_telemetry.operations import clock
    from urllib.parse import parse_qs, urlsplit
    import time
    calls = []
    def reply(url, **kwargs):
        t1, m1 = time.time(), time.monotonic()
        calls.append(1)
        nonce = parse_qs(urlsplit(url).query)['nonce'][0]
        raw = json.dumps(dict(schema_version=1, reference_id='monitor', reference_session=str(len(calls)),
                              clock_stable=True, nonce=nonce, t2=t1, t3=t1)).encode()
        return raw, dict(t1=t1, m1=m1, t4=time.time(), m4=time.monotonic())
    monkeypatch.setattr(clock, 'request_clock_bytes', reply)
    with pytest.raises(ValueError):
        clock.calibrate('http://reference', node='gpu-a', reference_id='monitor', samples=3)
    assert len(calls) == 2


def test_projection_rejects_low_quality_reference_for_3fs_without_hiding_source(tmp_path):
    from xlayer_telemetry.analysis.clock_quality import assess_interval
    window = cache(tmp_path).project(120, 122)
    def query(expr, *args):
        value = 1 if 'timex' in expr else 12 if 'storage' in expr and not expr.startswith('time()') else 0
        return dict(min=value, max=value, last=value)
    result = assess_interval(query, cluster='lab', nodes=['gpu-a', 'storage'], window=window,
                             producer_node='gpu-a', config={'calibration_reference': 'monitor'},
                             producer_clock_nodes=['storage'])
    assert result['status'] == 'unsafe'
    assert result['producer_clock_screening']['nodes']['storage']['status'] == 'unsafe'


def test_continuous_invalid_options_fail_instead_of_retrying_forever(tmp_path):
    from xlayer_telemetry.cli import main
    assert main(['clock', 'calibrate', '--url', 'http://unused', '--reference-id', 'monitor',
                 '--node', 'gpu-a', '--file', str(tmp_path/'clock.json'), '--samples', '0', '--interval', '1']) == 2


def test_periodic_lookback_is_also_mapped_when_covered(tmp_path):
    from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine
    class Backend:
        def query_range(self, expr, *args): return None
    engine = DiagnosticEngine({'node': 'gpu-a', 'cluster': 'lab', 'lookback_seconds': 2,
                               'clock': {'calibration_reference': 'monitor'}, 'prometheus': {'url': 'unused'}},
                              prometheus=Backend(), clock=lambda: 122, time_calibration=cache(tmp_path))
    report = engine.analyze(None, [])
    assert report['analysis_window']['start'] == 108
    assert report['clock_quality']['status'] == 'aligned'


@pytest.mark.parametrize('change', [{'schema_version': True}, {'uncertainty_seconds': -.1},
                                     {'reference_id': 'bad reference'}, {'drift_ppm': float('nan')}])
def test_malformed_calibration_is_not_trusted(tmp_path, change):
    value = calibration()
    value.update(change)
    assert cache(tmp_path, payload=value).project(120, 122)['time_alignment']['status'] == 'unknown'


def test_malformed_alignment_with_baseline_returns_insufficient_not_crash():
    from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine
    class Backend:
        def query_range(self, *args): return None
    common = dict(run_id='r', node='gpu-a', worker_id='0', boundary_scope='rl_step', step_duration_seconds=2)
    old = common | {'observed_at': 10, 'analysis_window': {'start': 8, 'end': 10}}
    current = common | {'observed_at': 20, 'analysis_window': {'start': 18, 'end': 20, 'time_alignment': None}}
    report = DiagnosticEngine({'cluster':'lab', 'prometheus': {'url': 'unused'},
                               'clock': {'calibration_reference': 'monitor'}}, prometheus=Backend()).analyze(current, [old])
    assert report['verdict'] == 'insufficient_data'
    assert report['clock_quality']['status'] == 'unknown'


def test_expired_exchange_is_not_saved_as_success(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    from xlayer_telemetry.operations import clock
    def response(url, **kwargs):
        raw = json.dumps(dict(schema_version=1, reference_id='monitor', reference_session='session',
                              nonce=parse_qs(urlsplit(url).query)['nonce'][0], clock_stable=True,
                              t2=100.02, t3=100.02)).encode()
        return raw, dict(t1=100., m1=0., t4=100.04, m4=.04)
    monkeypatch.setattr(clock, 'request_clock_bytes', response)
    real = iter([0., 0., .04, .04, 20.04])
    monkeypatch.setattr(clock.time, 'monotonic', lambda: next(real))
    wall = iter([112., 112.04, 132.04])
    mono = iter([50., 50.04, 70.04])
    with pytest.raises(ValueError, match='Exchange expired'):
        clock.calibrate('http://reference', node='gpu-a', reference_id='monitor', samples=1, ttl=1,
                        wall_clock=lambda: next(wall), monotonic=lambda: next(mono))
