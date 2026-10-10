"""Only explicit links establish execution relations, never Step coincidence."""

import json

import pytest

from xlayer_telemetry.events import CorrelationContext, EventRecorder, SpanIdentity


def span(sid, start=0, end=1, *, trace='trace', run='run', parent=None, node='n', links=None, **attributes):
    return {'record_type': 'span', 'run_id': run, 'trace_id': trace, 'span_id': sid,
            'node': node, 'worker_id': '0', 'producer': 'sdk', 'role': 'rollout', 'step': 1,
            'name': attributes.pop('name', sid), 'phase': attributes.pop('phase', 'generation'),
            'boundary_accuracy': 'exact', 'duration_seconds': end - start,
            'start_time_unix_nano': round(start * 1e9), 'end_time_unix_nano': round(end * 1e9),
            'parent_span_id': parent, 'attributes': attributes, 'links': links or []}


def link(sid, *, trace='trace', relation='depends_on'):
    return {'trace_id': trace, 'span_id': sid, 'relation': relation}


def graph(records, **kwargs):
    from xlayer_telemetry.analysis.execution_graph import build_graph
    return build_graph(records, run_id='run', **kwargs)


def test_sdk_links_are_additive_and_work_without_otel_dependency(tmp_path):
    from xlayer_telemetry.events import SpanLink
    recorder = EventRecorder(tmp_path, CorrelationContext('run', 'sdk', 'actor', '0', 'n'))
    with recorder.span('trainer.update', phase='training', step=9,
                       links=[SpanLink(SpanIdentity('rollout-trace', 'generated'), 'consumes')]):
        pass
    row = json.loads(recorder.path.read_text())
    assert row['links'] == [link('generated', trace='rollout-trace', relation='consumes')]


def test_async_cross_trace_link_survives_different_step_worker_and_node():
    rollout = span('sample', step_scope='rollout')
    trainer = span('update', 2, 3, trace='train', node='trainer', links=[link('sample', relation='consumes')])
    trainer.update(step=91, worker_id='trainer')
    result = graph([rollout, trainer, span('foreign', run='other')])
    assert len(result['nodes']) == 2
    assert result['edges'][0]['kind'] == 'observed'
    assert result['edges'][0]['relation'] == 'consumes'
    assert result['edges'][0]['timing_status'] == 'unknown'
    assert result['quality']['foreign_records'] == 1


def test_matching_step_and_temporal_overlap_never_create_edges():
    assert graph([span('a'), span('b')])['edges'] == []
    result = graph([span('a'), span('b')], relations=[{'source': ['trace', 'a'],
        'target': ['trace', 'b'], 'kind': 'temporal', 'relation': 'overlaps'}])
    assert result['edges'][0]['kind'] == 'temporal'


def test_missing_parent_and_duplicate_id_are_unknown_not_arbitrary():
    result = graph([span('a', parent='lost'), span('b'), span('b', end=2)])
    assert result['quality']['duplicate_spans'] == 1
    assert result['quality']['unresolved_edges'] == 1
    assert result['edges'][0]['kind'] == 'unknown'
    assert result['nodes'][1]['ambiguous']


def test_cycle_is_reported_and_budget_is_bounded():
    result = graph([span('a', parent='b'), span('b', parent='a')])
    assert result['quality']['cycle']
    assert graph([span(str(i)) for i in range(4)], max_spans=2)['quality']['truncated']


@pytest.mark.parametrize('relation', ['fake', '', 'causes'])
def test_sdk_rejects_unknown_link_semantics(relation):
    from xlayer_telemetry.events import SpanLink
    with pytest.raises(ValueError):
        SpanLink(SpanIdentity('t', 's'), relation)


@pytest.mark.parametrize('accuracy', [[], {}, None, 'unknown', 'approximate'])
def test_malformed_or_imprecise_clock_record_remains_unknown(accuracy):
    record = span('a')
    record['boundary_accuracy'] = accuracy
    result = graph([record])
    assert not result['nodes'][0]['interval']['valid']


def test_sdk_rank_gpu_and_reported_policy_identity_survive_projection():
    record = span('a')
    record.update(rank=3, local_rank=1, gpu='GPU-explicit', policy_version=7)
    node = graph([record])['nodes'][0]
    assert {key: node[key] for key in ('rank', 'local_rank', 'gpu', 'policy_version')} == {
        'rank': 3, 'local_rank': 1, 'gpu': 'GPU-explicit', 'policy_version': 7}
