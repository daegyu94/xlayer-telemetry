"""Shared decisions retain evidence limits without transferring entity quality."""
from copy import deepcopy

import pytest

from xlayer_telemetry.analysis import llm_diagnosis as llm
from xlayer_telemetry.analysis.diagnosis_analysis import compare_signals, evaluate_rules
from xlayer_telemetry.analysis.diagnosis_pipeline import (
    assess_candidate_context, assess_comparisons, cap_candidate_quality, describe_signals,
)
from xlayer_telemetry.analysis.observation_contracts import EvidenceBundle


def packet(warnings=(), *, partial=False):
    quality = {'warnings': list(warnings)}
    if partial:
        quality['query_result'] = {'scope': 'query', 'warning_count': 1, 'info_count': 0,
                                   'discarded_sample_count': 0, 'discarded_series_count': 0}
    return {'schema_version': 1, 'record_type': 'llm_observation_packet',
            'current_interval': {'start': 10, 'end': 20, 'accuracy': 'exact'},
            'observations': [{'id': 'm1', 'signal': 'gpu_utilization_percent', 'current': 0,
                              'baseline': 90, 'observation_scope': 'device', 'labels': {'gpu': '0', 'node': 'n'},
                              'sampling_quality': {'current': quality}}]}


def answer():
    return {'assessment': 'bottleneck_suspected', 'summary': 'Possible GPU pressure requires inspection.',
            'candidates': [{'title': 'Possible GPU pressure', 'explanation': 'Inspect m1 in this interval.',
                            'evidence_ids': ['m1'], 'counter_evidence_ids': [], 'missing_evidence': [],
                            'observation_scope': 'device', 'next_checks': ['Inspect resource usage.']}],
            'limitations': ['Resource ownership is unverified.']}


@pytest.mark.parametrize('warning', ['source_sample_before_interval', 'source_timestamp_in_future'])
def test_stale_raw_measurements_are_not_accepted_as_llm_hypothesis_evidence(warning):
    data = packet([warning])
    assert llm.model_view(data)['unusable_current_observations'] == ['m1']
    with pytest.raises(ValueError, match='current measurements'):
        llm.validate_diagnosis(answer(), data)
    assert EvidenceBundle.decode(data).available_signals() == {}
    assert data['observations'][0]['current'] == 0


@pytest.mark.parametrize('warnings,partial', [(['fewer_than_two_query_evaluations'], False), ([], True)])
def test_partial_sampling_caps_rule_and_requires_visible_llm_limitation(warnings, partial):
    data = packet(warnings, partial=partial)
    bundle = EvidenceBundle.decode(data)
    expected = list(bundle.limits('gpu_utilization_percent'))
    assert llm.model_view(data)['evidence_limits']['m1'] == expected
    with pytest.raises(ValueError, match='preserve sampling limitations'):
        llm.validate_diagnosis(answer(), data)
    accepted = answer()
    accepted['candidates'][0]['missing_evidence'] = expected
    llm.validate_diagnosis(accepted, data)
    candidate = {'id': 'gpu_starvation', 'state': 'strong_signal', 'missing_evidence': [],
                 'evidence': [{'signal': 'gpu_utilization_percent', 'value': 0,
                               'observation_scope': 'device', 'labels': {'node': 'n', 'gpu': '0'}}]}
    quality = {'gpu_utilization_percent': data['observations'][0]['sampling_quality']}
    capped = cap_candidate_quality([candidate], bundle, quality, {})
    assert capped[0]['state'] == capped[0]['signal_strength'] == 'supporting_signal'
    assert capped[0]['missing_evidence'] == expected
    assert cap_candidate_quality(capped, bundle, quality, {}) == capped
    assert candidate['state'] == 'strong_signal'  # Pure decision leaves its input intact.


def test_entity_local_sampling_limits_do_not_borrow_other_gpu_quality():
    data = packet()
    other = deepcopy(data['observations'][0])
    other.update(id='m2', labels={'node': 'n', 'gpu': '1'},
                 sampling_quality={'current': {'warnings': ['fewer_than_two_query_evaluations']}})
    data['observations'].append(other)
    llm.validate_diagnosis(answer(), data)
    view = llm.model_view(data)
    assert 'm1' not in view['evidence_limits'] and 'm2' in view['evidence_limits']
    candidate = answer()
    candidate['candidates'][0]['evidence_ids'] = ['m2']
    candidate['candidates'][0]['explanation'] = 'Inspect m2.'
    with pytest.raises(ValueError, match='preserve sampling limitations'):
        llm.validate_diagnosis(candidate, data)


def test_workload_and_sampling_caps_commute_and_are_idempotent():
    data = packet(['fewer_than_two_query_evaluations'])
    bundle = EvidenceBundle.decode(data)
    quality = {'gpu_utilization_percent': data['observations'][0]['sampling_quality']}
    candidates = [{'id': 'gpu_starvation', 'state': 'strong_signal', 'missing_evidence': [],
                   'evidence': [{'signal': 'step_duration_seconds', 'value': 20, 'baseline': 10,
                                 'observation_scope': 'application'},
                                {'signal': 'gpu_utilization_percent', 'value': 0,
                                 'source': 'prometheus', 'observation_scope': 'device', 'labels': {'node': 'n', 'gpu': '0'}}]}]
    def context(values):
        return assess_candidate_context(values, config={}, run_id='r', workload_comparability='unverified',
                                        missing=[], tool_event=None)
    def sampling(values):
        return cap_candidate_quality(values, bundle, quality, {})
    first, second = sampling(context(candidates)), context(sampling(candidates))
    assert first[0]['state'] == second[0]['state'] == 'supporting_signal'
    assert set(first[0]['missing_evidence']) == set(second[0]['missing_evidence'])
    assert sampling(context(first)) == first
    assert first[0]['signal_strength'] == first[0]['state']


BASE = {'step_duration_seconds': 10, 'rollout_duration_seconds': 5, 'communication_duration_seconds': 1,
        'actor_update_duration_seconds': 1, 'critic_update_duration_seconds': 1, 'checkpoint_duration_seconds': 1,
        'gpu_utilization_percent': 90, 'gpu_memory_usage_ratio': .5, 'gpu_evictions_delta': 0,
        'host_memory_available_ratio': .5, 'host_swap_activity': 0, 'storage_device_busy_ratio': .2,
        'threefs_p99_latency': 2, 'threefs_throughput_bytes_per_second': 100, 'network_utilization_ratio': .2,
        'rdma_bytes_per_second': 10, 'vllm_requests_waiting': 0, 'vllm_kv_cache_usage': .4,
        'vllm_preemptions_delta': 0, 'tool_duration_seconds': 1, 'vllm_queue_p95_seconds': 1}
CASES = [
    ('storage_queue_saturation', {'threefs_p99_latency': 12, 'storage_device_busy_ratio': .96}),
    ('device_limited_storage', {'threefs_p99_latency': 12, 'storage_device_busy_ratio': .96}),
    ('network_limited_storage', {'threefs_p99_latency': 12, 'network_utilization_ratio': .9}),
    ('small_io_pressure', {'threefs_p99_latency': 12, 'storage_request_bytes': 1024}),
    ('gpu_starvation', {'step_duration_seconds': 20, 'gpu_utilization_percent': 45, 'vllm_requests_waiting': 3}),
    ('gpu_memory_pressure', {'gpu_memory_usage_ratio': .96, 'gpu_evictions_delta': 2}),
    ('rollout_queue_backlog', {'rollout_duration_seconds': 9, 'vllm_requests_waiting': 3}),
    ('kv_cache_pressure', {'vllm_kv_cache_usage': .95, 'vllm_preemptions_delta': 3, 'vllm_requests_waiting': 3}),
    ('communication_bound', {'communication_duration_seconds': 3, 'rdma_bytes_per_second': 30,
                             'gpu_utilization_percent': 50}),
    ('host_memory_pressure', {'host_memory_available_ratio': .05, 'host_swap_activity': 10}),
    ('ray_object_store_disk_pressure', {'step_duration_seconds': 20, 'ray_mmap_disk_bytes': 100}),
    ('rollout_queue_latency', {'rollout_duration_seconds': 10, 'vllm_queue_p95_seconds': 3}),
    ('sandbox_local_storage_pressure', {'tool_duration_seconds': 3, 'sandbox_io_pressure_ratio': .4,
                                        'sandbox_device_busy_ratio': .95}),
    *[(f'host_{resource}_stalls', {'step_duration_seconds': 20, f'host_{resource}_pressure_ratio': .4})
      for resource in ('cpu', 'memory', 'io')],
    *[(f'{stage}_{resource}_stalls', {f'{stage}_duration_seconds': 3, f'host_{resource}_pressure_ratio': .4})
      for stage, resource in (('actor_update', 'cpu'), ('critic_update', 'cpu'), ('checkpoint', 'io'))],
    *[(f'mooncake_dfs_{direction}_pressure', {'step_duration_seconds': 20,
                                           f'mooncake_dfs_{direction}_errors_per_second': 1})
      for direction in ('read', 'write')],
    ('straggler', {}),
]


@pytest.mark.parametrize('identifier,changes', CASES, ids=[case[0] for case in CASES])
def test_every_supported_rule_keeps_its_characterized_candidate_through_shared_assessment(identifier, changes):
    current = BASE | changes
    rows = describe_signals(compare_signals(current, BASE), {}, {})
    bundle = EvidenceBundle.from_rows(rows, window={'start': 10, 'end': 20},
                                     baseline_window={'start': 0, 'end': 10}, clock_quality={'status': 'aligned'})
    context = {'participant_durations_seconds': {'rank-0': 10, 'rank-1': 11, 'rank-2': 22}} if identifier == 'straggler' else {}
    candidates = evaluate_rules(bundle.available_signals(), bundle.available_signals('baseline'), thresholds={}, context=context)
    original = next(item for item in candidates if item['id'] == identifier)
    assessed = cap_candidate_quality(candidates, bundle, {}, {})
    kept = next(item for item in assessed if item['id'] == identifier)
    assert kept['state'] == original['state'] and kept['evidence']
    assert kept['signal_strength'] == kept['state']
    unsafe = EvidenceBundle.from_rows(rows, window={'start': 10, 'end': 20}, baseline_window=None,
                                     clock_quality={'status': 'unsafe'})
    assert cap_candidate_quality(candidates, unsafe, {}, {}) == []
    assert evaluate_rules({}, {}, thresholds={}, context={}) == []


def test_comparison_sampling_assessment_keeps_zero_and_is_idempotent():
    data = packet(['fewer_than_two_query_evaluations'])
    row = {'signal': 'gpu_utilization_percent', 'current': 0, 'baseline': 90,
           'delta': -90, 'delta_percent': -100, 'scope': 'device',
           'sampling_quality': data['observations'][0]['sampling_quality']}
    bundle = EvidenceBundle.from_rows([row], window=data['current_interval'],
                                     baseline_window=None, clock_quality={})
    output = assess_comparisons([row], bundle)
    assert output[0]['current'] == 0 and output[0]['baseline'] == 90
    assert output[0]['delta'] is output[0]['delta_percent'] is None
    assert output[0]['comparison_status'] == 'insufficient_sampling_coverage'
    assert assess_comparisons(output, bundle) == output
    assert row['delta'] == -90


def test_canonical_counter_alias_keeps_executed_query_unit_and_statistic():
    rows = describe_signals([{'signal': 'vllm_preemptions_delta', 'current': 0, 'baseline': 1}],
                            {}, {'vllm_preemptions_delta': 'executed_counter_query'})
    assert rows[0]['query'] == 'executed_counter_query'
    assert rows[0]['unit'] == 'preemptions' and rows[0]['window_statistic'] == 'max_series_delta'


def test_saved_withheld_comparison_needs_explicit_llm_limitation():
    data = packet()
    data['observations'][0].update(comparison_status='different_report_population', delta=None)
    with pytest.raises(ValueError, match='preserve sampling limitations'):
        llm.validate_diagnosis(answer(), data)
    accepted = answer()
    accepted['candidates'][0]['missing_evidence'] = llm.model_view(data)['evidence_limits']['m1']
    assert accepted['candidates'][0]['missing_evidence'] == [
        'baseline:gpu_utilization_percent:comparison_withheld:different_report_population']
    llm.validate_diagnosis(accepted, data)


def test_same_entity_custom_queries_keep_independent_observation_quality():
    data = packet()
    other = deepcopy(data['observations'][0])
    other.update(id='m2', query='different_query',
                 sampling_quality={'current': {'warnings': ['fewer_than_two_query_evaluations']}})
    data['observations'].append(other)
    llm.validate_diagnosis(answer(), data)
    assert 'm1' not in llm.model_view(data)['evidence_limits']


def test_rule_candidate_cannot_borrow_selected_scalar_quality_from_another_gpu():
    data = packet(['fewer_than_two_query_evaluations'])
    bundle = EvidenceBundle.decode(data)
    candidate = {'id': 'gpu_starvation', 'state': 'strong_signal', 'missing_evidence': [],
                 'evidence': [{'signal': 'gpu_utilization_percent', 'value': 0, 'observation_scope': 'device',
                               'labels': {'node': 'n', 'gpu': '1'}}]}
    result = cap_candidate_quality([candidate], bundle,
                                  {'gpu_utilization_percent': data['observations'][0]['sampling_quality']}, {})
    assert result[0]['missing_evidence'] == ['current:gpu_utilization_percent:selected_entity_evaluation_count_unknown']
    assert result[0]['state'] == 'supporting_signal'
    assert 'fewer_than_two_query_evaluations' not in str(result)


def test_all_stale_measurements_cannot_be_reported_as_no_issue():
    result = {'assessment': 'no_issue_observed', 'summary': 'No issue observed.',
              'candidates': [], 'limitations': []}
    with pytest.raises(ValueError, match='current measurements are unavailable'):
        llm.validate_diagnosis(result, packet(['source_sample_before_interval']))
