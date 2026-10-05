"""Shared range parsing preserves labels, counter evidence and failure semantics."""
import pytest

from xlayer_telemetry import prometheus
from xlayer_telemetry.analysis.diagnostics import PrometheusClient as DiagnosticPrometheusClient
from xlayer_telemetry.prometheus import PrometheusClient, range_series, series_stats


def matrix(series):
    return {'status': 'success', 'data': {'result': series}}


def test_client_is_shared_and_preserves_entity_boundaries(monkeypatch):
    assert DiagnosticPrometheusClient is PrometheusClient
    payload = matrix([
        {'metric': {'engine': 'A'}, 'values': [[0, '100'], [1, '0'], [2, '20']]},
        {'metric': {'engine': 'B'}, 'values': [[0, '0'], [1, '0'], [2, '0']]},
    ])
    monkeypatch.setattr(prometheus, '_read_json', lambda *_: payload)
    detail = PrometheusClient('http://unused').query_range_detail('counter', 0, 2, 1)
    assert [item['labels']['engine'] for item in detail['series']] == ['A', 'B']
    assert [item['stats']['max_series_delta'] for item in detail['series']] == [20, 0]
    assert detail['aggregate']['sample_count'] == 6
    assert all(set(item) == {'labels', 'stats'} for item in detail['series'])


def test_malformed_points_are_missing_measurements_not_zero(monkeypatch):
    bad = [None, 1, {}, '12', [], [1], [1, '2', '3'], [False, '1'], [1, True],
           [1, 'NaN'], ['Inf', '1'], [1, 10**1000], [1, {'value': 3}]]
    payload = matrix([{'metric': {'node': 'bad'}, 'values': bad},
                      {'metric': {'node': 'good'}, 'values': bad + [[1, '4.5']]}])
    parsed = range_series(payload)
    assert parsed == [{'labels': {'node': 'good'}, 'points': [[1, 4.5]]}]
    stats = series_stats(parsed)
    assert stats['max_series_delta'] is None and stats['sample_count'] == 1
    monkeypatch.setattr(prometheus, '_read_json', lambda *_: matrix([{'values': bad}]))
    assert PrometheusClient('http://unused').query_range('missing', 0, 1, 1) is None


@pytest.mark.parametrize('payload', [[], {'status': 'error', 'error': 'backend unavailable'},
                                    {'status': 'success', 'data': {'result': None}},
                                    matrix([{'metric': None}]), matrix([{'values': None}])])
def test_invalid_response_envelopes_raise_backend_error(payload):
    with pytest.raises(RuntimeError):
        range_series(payload)


def test_source_timestamps_only_use_valid_samples(monkeypatch):
    monkeypatch.setattr(prometheus, '_read_json', lambda *_: matrix([
        {'values': [None, [10, '8'], [11, '8'], [12, 'NaN'], [13, '9']]},
    ]))
    detail = PrometheusClient('http://unused').query_range_detail('timestamp(sample)', 10, 13, 1)
    assert detail['series'][0]['source_timestamps'] == [8, 8, 9]


def test_query_label_escaping_keeps_quotes_newlines_and_backslashes():
    assert prometheus.escape_label('node"\\\n') == 'node\\"\\\\\\n'


def test_query_response_body_is_bounded_before_decoding(monkeypatch):
    from io import BytesIO
    from urllib.request import Request
    body = BytesIO(b' ' * (prometheus.MAX_RESPONSE_BYTES + 1))
    monkeypatch.setattr(prometheus, 'urlopen', lambda *args, **kwargs: body)
    with pytest.raises(RuntimeError, match='8 MiB'):
        prometheus._read_json(Request('http://unused'), 1)
    assert body.closed


def test_excessive_query_entities_fail_instead_of_returning_partial_success(monkeypatch):
    with pytest.raises(RuntimeError, match='1000 series'):
        range_series(matrix([{'values': [[1, '1']]}] * 1001))
    monkeypatch.setattr(prometheus, 'MAX_RESULT_POINTS', 2)
    with pytest.raises(RuntimeError, match='200000 points'):
        range_series(matrix([{'values': [[1, '1'], [2, '2']]}, {'values': [[1, '0']]}]))


@pytest.mark.parametrize('result_type', ['vector', 'scalar', 'string', None])
def test_range_query_rejects_non_matrix_result_type(result_type):
    payload = matrix([{'metric': {'engine': 'a'}, 'values': [[1, '1']]}])
    payload['data']['resultType'] = result_type
    with pytest.raises(RuntimeError, match='matrix'):
        range_series(payload)


@pytest.mark.parametrize('labels', [{'engine': ['a']}, {'engine': None}, {'engine': 1}, {1: 'a'}])
def test_range_query_rejects_invalid_entity_labels(labels):
    with pytest.raises(RuntimeError, match='labels'):
        range_series(matrix([{'metric': labels, 'values': [[1, '1']]}]))


def test_range_query_rejects_duplicate_entity_identity():
    # Key order cannot distinguish the same entity. Consumers otherwise either
    # double count its samples or silently overwrite one row in an identity map.
    payload = matrix([
        {'metric': {'node': 'n', 'engine': 'a'}, 'values': [[1, '10']]},
        {'metric': {'engine': 'a', 'node': 'n'}, 'values': [[1, '50']]},
    ])
    with pytest.raises(RuntimeError, match='duplicate.*series'):
        range_series(payload)


@pytest.mark.parametrize('points', [
    [[2, '100'], [1, '90']],
    [[1, '100'], [1, '90']],
    [[1, '90'], [1, '90']],
])
def test_counter_samples_require_strictly_increasing_timestamps(points):
    # An unordered pair can look like a reset (+90 rather than +10), while
    # equal timestamps cannot establish any elapsed counter observation.
    with pytest.raises(RuntimeError, match='timestamp'):
        range_series(matrix([{'metric': {'engine': 'a'}, 'values': points}]))


def test_malformed_counter_matrix_is_missing_without_discarding_other_sources(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine

    def response(request, timeout):
        query = parse_qs(urlsplit(request.full_url).query)['query'][0]
        if 'num_preemptions_total' in query:
            return matrix([{'metric': {'engine': 'a'}, 'values': [[2, '100'], [1, '90']]}])
        if 'node_disk_read_bytes_total' in query:
            return matrix([{'metric': {'instance': 'n'}, 'values': [[1, '10'], [2, '20']]}])
        return matrix([])

    monkeypatch.setattr(prometheus, '_read_json', response)
    report = DiagnosticEngine({'prometheus': {'url': 'http://unused'}}, clock=lambda: 3).analyze(None, [])
    assert 'vllm_preemptions_total' not in report['evidence']
    assert 'prometheus:vllm_preemptions_total:RuntimeError' in report['missing_sources']
    assert report['evidence']['disk_read_bytes_per_second']['mean'] == 15
    assert not report['query_execution']['sources']['prometheus']['unavailable']
