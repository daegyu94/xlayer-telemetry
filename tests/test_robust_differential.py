"""Noise, finite cohorts and ownership remain distinct from observed pressure."""

import pytest

from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine
from tests.test_baseline_cohort_integrity import record, FreshPressureMetrics


def analyze(values, *, duration=20, workload=None, robust=True):
    current = record(100, duration=duration, **({'workload': workload} if workload else {}))
    history = [record(10 + i * 22, duration=value) for i, value in enumerate(values)]
    config = {'prometheus': {'url': 'http://unused'}, 'sampling': {'check_source_freshness': True},
              'baseline': {'match_fields': ['perf/total_num_tokens'], 'robust': {'enabled': robust}}}
    source = FreshPressureMetrics()
    return DiagnosticEngine(config, prometheus=source, clock=lambda: 110).analyze(current, history)


def test_noisy_cohort_cannot_turn_ordinary_tail_into_strong_signal():
    report = analyze([5, 10, 10, 20], duration=16)
    candidate = next(c for c in report['candidates'] if c['id'] == 'gpu_starvation')
    assert candidate['state'] != 'strong_signal'
    assert 'robust_baseline:step_duration_seconds:insufficient_cohort' in candidate['missing_evidence']


def test_mad_preserves_shift_and_withholds_noisy_tail():
    from xlayer_telemetry.analysis.robust_differential import assess
    assert assess(20, [9.8, 10, 10, 10.1, 10.2])['status'] == 'shift_observed'
    noisy = assess(16, [5, 10, 10, 20, 20])
    assert noisy['status'] == 'within_variation'
    assert noisy['mad'] == 5
    assert noisy['median_interval']['coverage'] == pytest.approx(.9375)
    assert assess(12, [10] * 5)['scale_seconds'] > 0


def test_duplicate_records_do_not_certify_cohort_size():
    current, prior = record(100, duration=20), record(60)
    report = DiagnosticEngine({'prometheus': {'url': 'http://unused'},
        'baseline': {'match_fields': ['perf/total_num_tokens'], 'robust': {'enabled': True}}},
        prometheus=FreshPressureMetrics(), clock=lambda: 110).analyze(current, [prior] * 9)
    quality = report['comparison']['robust_baseline']['signals']['step_duration_seconds']
    assert quality['count'] == 1
    assert quality['status'] == 'insufficient_cohort'


def test_existing_opt_out_preserves_rule_behavior():
    report = analyze([10], robust=False)
    assert next(c for c in report['candidates'] if c['id'] == 'gpu_starvation')['state'] == 'strong_signal'


@pytest.mark.parametrize('bad', [{'minimum_cohort': 2}, {'cohort_size': 1000}, {'z_threshold': 0}, {'enabled': 'yes'}])
def test_robust_policy_is_bounded(bad):
    from xlayer_telemetry.analysis.diagnosis_analysis import validate_baseline_policy
    with pytest.raises(ValueError):
        validate_baseline_policy({'robust': bad})


def test_robust_metadata_reaches_independent_llm_input_without_rule_verdict():
    from xlayer_telemetry.analysis.llm_diagnosis import packet_from_report, validate_packet, model_view
    report = analyze([10], duration=20)
    packet = packet_from_report(report)
    validate_packet(packet)
    step = next(row for row in packet['observations'] if row['signal'] == 'step_duration_seconds')
    assert step['robust_baseline']['status'] == 'insufficient_cohort'
    view = model_view(packet)
    assert 'robust_baseline' in view['observation_columns']
    assert 'candidates' not in packet and 'verdict' not in packet


def test_noisy_five_member_cohort_and_overlap_are_assessed_independently():
    from xlayer_telemetry.analysis.diagnosis_analysis import recent_baseline_history
    current = record(1000, duration=16)
    history = [record(100 + i * 30, duration=value) for i, value in enumerate([5, 10, 10, 20, 20])]
    history += [record(999, duration=1), record(99, duration=1, run_id='other')]
    config = {'prometheus': {'url': 'http://unused'}, 'baseline': {'match_fields': ['perf/total_num_tokens'], 'robust': {'enabled': True}}}
    report = DiagnosticEngine(config, prometheus=FreshPressureMetrics(), clock=lambda: 1010).analyze(current, history)
    row = report['comparison']['robust_baseline']['signals']['step_duration_seconds']
    assert row['count'] == 5 and row['status'] == 'within_variation'
    assert len(recent_baseline_history(current, history, policy=config['baseline'])) == 5
    assert not report['symptom']['slow_stages']


def test_summary_rejects_unbounded_reference_input():
    from xlayer_telemetry.analysis.robust_differential import assess
    with pytest.raises(ValueError, match='31'):
        assess(10, iter([10] * 10000))


def test_missing_stage_container_cannot_certify_stage_regression():
    history = [record(100 + i * 30, duration=10, stage_durations_seconds=None if i < 4 else {'gen': 10})
               for i in range(5)]
    report = DiagnosticEngine({'prometheus': {'url': 'http://unused'}, 'baseline': {
        'match_fields': ['perf/total_num_tokens'], 'robust': {'enabled': True}}},
        prometheus=FreshPressureMetrics(), clock=lambda: 1010).analyze(record(1000, duration=20), history)
    assert report['symptom']['slow_stages'] == []
    assert report['comparison']['robust_baseline']['signals']['rollout_duration_seconds']['count'] == 1
