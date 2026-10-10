"""Production parser → clock screening and scrape-configuration contracts."""
from urllib.parse import parse_qs, urlsplit

import pytest

from xlayer_telemetry import prometheus
from xlayer_telemetry.analysis.clock_quality import assess_clocks
from xlayer_telemetry.analysis.storage_overview import storage_overview
from xlayer_telemetry.operations.health import _target_configuration
from xlayer_telemetry.subsystems import summarize_sources


@pytest.mark.parametrize('fault', ['two_single_points', 'warning', 'info', 'discarded_point', 'different_source', 'wrong_node', 'missing_labels', 'normal'])
def test_clock_checks_actual_parser_source_and_partial_response(monkeypatch, fault):
    calls = []
    def response(request, timeout):
        query = parse_qs(urlsplit(request.full_url).query)['query'][0]
        calls.append(query)
        labels = {'job': 'telemetry', 'cluster': 'lab', 'instance': 'node', 'exporter': 'a'}
        if fault == 'wrong_node':
            labels['instance'] = 'other-node'
        elif fault == 'missing_labels':
            labels = {}
        value = '1' if 'sync_status' in query else '.001'
        if fault == 'different_source' and 'timex_offset' in query:
            labels['exporter'] = 'b'
        series = [{'metric': labels, 'values': [[100, value], [110, value]]}]
        if fault == 'two_single_points':
            series = [{'metric': {**labels, 'exporter': source}, 'values': [[100, value]]} for source in ('a', 'b')]
        if fault == 'discarded_point':
            series[0]['values'].append([111, 'NaN'])
        payload = {'status': 'success', 'data': {'resultType': 'matrix', 'result': series}}
        if fault in ('warning', 'info'):
            payload['warnings' if fault == 'warning' else 'infos'] = ['private backend text must not leak']
        return payload
    monkeypatch.setattr(prometheus, '_read_json', response)
    result = assess_clocks(prometheus.PrometheusClient('http://unused').query_range,
                           cluster='lab', nodes=['node'], start=100, end=120)
    assert result['status'] == ('aligned' if fault == 'normal' else 'unknown')
    assert 'private backend text' not in str(result)
    assert len(calls) == 5  # Same five clock queries; no new source discovery requests.
    if fault == 'two_single_points':
        assert result['nodes']['node']['offset_seconds']['series_count'] == 2
        assert result['nodes']['node']['offset_seconds']['min_series_sample_count'] == 1


@pytest.mark.parametrize('url,expected', [
    ('http://host:8000/metrics', 'configuration_mismatch'),
    ('https://host:8000/model-b/metrics', 'configuration_mismatch'),
    ('http://other:8000/model-b/metrics', 'configuration_mismatch'),
    ('http://host:8000/model-b/metrics', 'up'),
])
def test_native_source_matches_actual_scrape_route(url, expected):
    labels = {'job': 'native', 'cluster': 'lab', 'telemetry_source': 'vllm',
              'component': 'engine', 'instance': 'host:8000'}
    group = {'targets': ['host:8000'], 'labels': {**labels, '__scheme__': 'http', '__metrics_path__': '/model-b/metrics'}}
    row = summarize_sources([group], [{'labels': labels, 'health': 'up', 'scrapeUrl': url}],
                            'http://grafana', 'lab')['sources'][0]
    assert row['status'] == expected
    assert row['scrape_health'] == 'up'


def test_host_scrape_scheme_must_match_configured_http():
    assert _target_configuration('10.0.0.20', [{'scrapeUrl': 'https://10.0.0.20:19100/metrics'}]) != 'matched'


@pytest.mark.parametrize('missing,status,baseline_status', [
    ([], 'no_data', 'unavailable'),
    (['prometheus:mooncake_dfs_read_p95_seconds:baseline_no_data'], 'no_data', 'no_data'),
    (['prometheus:mooncake_dfs_read_p95_seconds:RuntimeError'], 'query_failed', 'unavailable'),
])
def test_storage_current_availability_is_not_baseline_availability(missing, status, baseline_status):
    result = storage_overview({}, {'mooncake_dfs_read_p95_seconds': 'query'}, missing,
                              unsafe_clock=False, threefs_configured=False, threefs_rows=[])
    row = next(row for row in result['signals'] if row['signal'] == 'mooncake_dfs_read_p95_seconds')
    assert row['status'] == status
    assert row['baseline_status'] == baseline_status


@pytest.mark.parametrize('targets,configuration', [
    ([], 'unknown'),
    ([{'health': 'up'}], 'unknown'),
    ([{'health': 'up', 'scrapeUrl': 'http://host:8000/model-b/metrics'}] * 2, 'ambiguous'),
    ([{'health': 'up', 'scrapeUrl': 'http://host:8001/model-b/metrics'}], 'mismatch'),
])
def test_native_source_never_certifies_missing_or_ambiguous_route(targets, configuration):
    labels = {'job': 'native', 'cluster': 'lab', 'telemetry_source': 'vllm', 'component': 'engine', 'instance': 'host:8000'}
    group = {'targets': ['host:8000'], 'labels': {**labels, '__metrics_path__': '/model-b/metrics'}}
    row = summarize_sources([group], [{**target, 'labels': labels} for target in targets], 'http://grafana', 'lab')['sources'][0]
    assert row['configuration_status'] == configuration
    assert row['status'] != 'up'


def test_storage_baseline_query_failure_cannot_change_successful_empty_current_to_failed():
    name = 'mooncake_dfs_read_p95_seconds'
    result = storage_overview({}, {name: 'query'}, [f'prometheus:{name}:RuntimeError'],
        unsafe_clock=False, threefs_configured=False, threefs_rows=[], sampling_quality={name: {'current': {}}})
    assert result['signals'][0]['status'] == 'no_data'
    assert result['signals'][0]['baseline_status'] == 'query_failed'
