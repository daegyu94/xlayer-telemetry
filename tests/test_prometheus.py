"""Shared range parsing preserves labels, counter evidence and failure semantics."""
import pytest

from xlayer_telemetry import prometheus
from xlayer_telemetry.diagnostics import PrometheusClient as LegacyPrometheusClient
from xlayer_telemetry.prometheus import PrometheusClient, range_series, series_stats


def matrix(series):
    return {'status': 'success', 'data': {'result': series}}


def test_client_keeps_legacy_import_and_entity_boundaries(monkeypatch):
    assert LegacyPrometheusClient is PrometheusClient
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
