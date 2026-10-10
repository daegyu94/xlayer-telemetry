"""Critical paths need complete blocking dependencies and usable clocks."""

import pytest

from tests.test_execution_graph import graph, link, span


def trajectory():
    return [span('root', 0, 8, phase='trajectory', execution_contract='dependency_dag',
                 children_complete=True, completion_span_id='finish'),
            span('generate', 0, 2, parent='root'),
            span('tool', 2, 5, parent='root', phase='tool', links=[link('generate')]),
            span('parallel', 2, 3, parent='root', phase='sandbox', links=[link('generate')]),
            span('queue', 5, 6, parent='root', phase='queue', time_kind='wait', links=[link('tool')]),
            span('finish', 6, 8, parent='root', links=[link('queue'), link('parallel')])]


def analyze(records, **kwargs):
    from xlayer_telemetry.analysis.trajectory_path import critical_path
    return critical_path(graph(records, **kwargs), ('trace', 'root'))


def test_parallel_duration_is_not_added_to_the_critical_path():
    result = analyze(trajectory())
    assert result['status'] == 'observed_path'
    assert result['path'] == [['trace', x] for x in ('generate', 'tool', 'queue', 'finish')]
    assert result['observed_path_seconds'] == 8
    assert result['measured_wait_seconds'] == 1
    assert result['unattributed_seconds'] == 0
    assert result['coverage'] == 1
    assert result['delay_candidates'][0]['name'] == 'tool'


@pytest.mark.parametrize('change', ['incomplete', 'no_links', 'generic_links', 'cycle', 'overlap', 'missing', 'nested', 'clock_jump'])
def test_unverified_path_is_withheld(change):
    records = trajectory()
    if change == 'incomplete': records[0]['attributes']['children_complete'] = False
    elif change == 'no_links': records[-1]['links'] = []
    elif change == 'generic_links': records[-1]['links'] = [link('queue', relation='associated')]
    elif change == 'cycle': records[1]['links'] = [link('finish')]
    elif change == 'overlap': records[2]['start_time_unix_nano'] = 1_000_000_000; records[2]['duration_seconds'] = 4
    elif change == 'missing': records.pop(2)
    elif change == 'nested': records[3]['parent_span_id'] = 'tool'
    elif change == 'clock_jump': records[1]['boundary_accuracy'] = 'clock_discontinuity'
    result = analyze(records)
    assert result['status'] == 'unknown'
    assert result['observed_path_seconds'] is None
    assert result['missing_evidence']


def test_unobserved_gaps_are_never_invented_as_queue_time():
    records = trajectory()
    records[0]['end_time_unix_nano'] = 9_000_000_000
    records[0]['duration_seconds'] = 9
    result = analyze(records)
    assert result['unattributed_seconds'] == 1
    assert result['measured_wait_seconds'] == 1


def test_remote_clock_quality_and_uncertainty_cannot_be_bypassed():
    records = trajectory()
    for row in records:
        row.update(boundary_accuracy='calibrated', time_reference='monitor', time_uncertainty_seconds=.001,
                   correlation_start_time_unix_nano=row['start_time_unix_nano'],
                   correlation_end_time_unix_nano=row['end_time_unix_nano'])
        row['time_alignment'] = {'status': 'aligned', 'method': 'four_timestamp', 'node': row['node'],
            'reference_id': 'monitor', 'offset_seconds': 0., 'uncertainty_seconds': .001,
            'exchange_uncertainty_seconds': .001, 'round_trip_seconds': .002,
            'valid_from': 0., 'valid_until': 10., 'local_anchor': 0., 'drift_ppm': 0.,
            'raw_window': {'start': row['start_time_unix_nano']/1e9, 'end': row['end_time_unix_nano']/1e9}}
    records[2]['node'] = 'remote'
    records[2]['time_alignment']['node'] = 'remote'
    assert analyze(records)['status'] == 'unknown'
    clocks = {'status': 'aligned', 'window': {'start': 0, 'end': 8},
              'nodes': {'n': {'status': 'aligned'}, 'remote': {'status': 'aligned'}}}
    # Adjacent boundaries ±1ms cannot establish a precise remote ordering.
    assert analyze(records, clock_quality=clocks)['status'] == 'unknown'
    records[2].update(correlation_start_time_unix_nano=2_010_000_000,
                      correlation_end_time_unix_nano=4_990_000_000,
                      start_time_unix_nano=2_010_000_000, end_time_unix_nano=4_990_000_000, duration_seconds=2.98)
    records[2]['time_alignment']['raw_window'] = {'start': 2.01, 'end': 4.99}
    result = analyze(records, clock_quality=clocks)
    assert result['status'] == 'observed_path'
    assert result['coverage'] < 1
    records[2]['time_alignment']['valid_until'] = 1.
    assert analyze(records, clock_quality=clocks)['status'] == 'unknown'


def test_calibrated_label_alone_cannot_bypass_existing_time_mapping_contract():
    from xlayer_telemetry.analysis.execution_graph import build_graph
    records = trajectory()
    records[1].update(boundary_accuracy='calibrated', time_reference='monitor', time_uncertainty_seconds=0.,
        correlation_start_time_unix_nano=0, correlation_end_time_unix_nano=2_000_000_000)
    result = build_graph(records, run_id='run')
    assert not result['nodes'][1]['interval']['valid']


def test_long_tail_requires_matched_run_workload_and_quality():
    from xlayer_telemetry.analysis.trajectory_path import compare_paths
    current = analyze(trajectory())
    references = [{**current, 'observed_path_seconds': 4., 'trajectory_seconds': 4.,
                   'workload': {'tokens': 100}, 'sequence': i} for i in range(6)]
    current.update(workload={'tokens': 100}, sequence=10)
    result = compare_paths(current, references)
    assert result['long_tail']
    current['workload']['tokens'] = 1000
    assert compare_paths(current, references)['long_tail'] is None


def test_latest_completion_rather_than_longest_duration_selects_blocking_branch():
    records = [span('root', 0, 9, execution_contract='dependency_dag', children_complete=True,
                    completion_span_id='finish'),
               span('long', 0, 6, parent='root'), span('late', 6, 8, parent='root'),
               span('finish', 8, 9, parent='root', links=[link('long'), link('late')])]
    result = analyze(records)
    assert result['path'] == [['trace', 'late'], ['trace', 'finish']]
    assert result['unattributed_seconds'] == 6
    assert result['measured_wait_seconds'] == 0


def test_declared_relations_and_wrong_clock_window_never_certify_path():
    records = trajectory()
    records[-1]['links'] = []
    relation = {'source': ['trace', 'queue'], 'target': ['trace', 'finish'],
                'kind': 'configured', 'relation': 'depends_on'}
    assert analyze(records, relations=[relation])['status'] == 'unknown'
    from xlayer_telemetry.analysis.execution_graph import aligned
    a, b = graph([span('a'), span('b', node='remote')])['nodes']
    for row in (a, b):
        row['interval'].update(accuracy='calibrated', reference='same', uncertainty_seconds=0.)
    clocks = {'status': 'aligned', 'window': {'start': 2, 'end': 3},
              'nodes': {'n': {'status': 'aligned'}, 'remote': {'status': 'aligned'}}}
    assert not aligned(a, b, clocks)


@pytest.mark.parametrize('earlier_offset', [.02, .01])
def test_calibration_refresh_cannot_reverse_or_erase_same_node_completion_order(earlier_offset):
    records = [span('root', 10, 15, execution_contract='dependency_dag', children_complete=True,
                    completion_span_id='finish'),
               span('earlier', 10.1, 12, parent='root'),
               span('later', 10.1, 12.01, parent='root'),
               span('finish', 13, 15, parent='root', links=[link('earlier'), link('later')])]
    for row in records:
        offset = earlier_offset if row['span_id'] == 'earlier' else 0.
        row.update(boundary_accuracy='calibrated', time_reference='monitor', time_uncertainty_seconds=.001,
            correlation_start_time_unix_nano=row['start_time_unix_nano']+round(offset*1e9),
            correlation_end_time_unix_nano=row['end_time_unix_nano']+round(offset*1e9))
        row['time_alignment'] = {'status': 'aligned', 'method': 'four_timestamp', 'node': row['node'],
            'reference_id': 'monitor', 'offset_seconds': offset, 'uncertainty_seconds': .001,
            'exchange_uncertainty_seconds': .001, 'round_trip_seconds': .002,
            'valid_from': 0., 'valid_until': 20., 'local_anchor': 0., 'drift_ppm': 0.,
            'raw_window': {'start': row['start_time_unix_nano']/1e9, 'end': row['end_time_unix_nano']/1e9}}
    dependency_graph = graph(records)
    assert all(node['interval']['valid'] for node in dependency_graph['nodes'])
    assert all(edge['timing_status'] == 'aligned' for edge in dependency_graph['edges'])
    result = analyze(records)
    assert result['status'] == 'unknown'
    assert result['path'] == [] and result['delay_candidates'] == []
    assert 'original_node_completion_order_conflicts_with_mapping' in result['missing_evidence']

    # A refresh that preserves ordering still permits the existing path.
    records[1]['correlation_start_time_unix_nano'] = records[1]['start_time_unix_nano']
    records[1]['correlation_end_time_unix_nano'] = records[1]['end_time_unix_nano']
    records[1]['time_alignment']['offset_seconds'] = 0.
    assert analyze(records)['path'] == [['trace', 'later'], ['trace', 'finish']]
