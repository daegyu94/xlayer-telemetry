import pytest

from xlayer_telemetry.analysis.evidence_quality import check_source, quality, timestamp_query, validate_quality


@pytest.mark.parametrize("query", [
    'rate(bytes{node="n"}[1m] offset 1h)',
    'bytes{node="n"} @ 100',
    'bytes{node="n"} / capacity',
    'sum_over_time(bytes{node="n"}[5m:1m])',
    'bytes{node="n"} + other{node="n"}',
])
def test_ambiguous_source_never_queries_unrelated_current_timestamps(query):
    class NoCalls:
        def query_range_detail(self, *args):
            pytest.fail("ambiguous source triggered a timestamp query")
    assert timestamp_query(query) is None
    assert check_source(NoCalls(), query, 0, 10, 1) == {}


@pytest.mark.parametrize("query,selector", [
    ('sum by (node) (rate(bytes{node="n"}[1m]))', 'bytes{node="n"}'),
    ('clamp_min(rate(bytes{node="n"}[1m]), 0)', 'bytes{node="n"}'),
    ('bytes{label="offset @ }"}', 'bytes{label="offset @ }"}'),
    ('gpu_utilization', 'gpu_utilization'),
])
def test_simple_source_queries_keep_supported_freshness(query, selector):
    assert timestamp_query(query) == f"timestamp({selector})"


def test_future_source_timestamp_is_unknown_not_zero_age():
    result = quality('metric{node="n"}', 90, 100, 2, {}, source={'last_source_timestamp': 110})
    assert result['last_source_timestamp'] == 110
    assert result['source_age_seconds'] is None
    assert result['freshness'] == 'unknown'
    assert 'source_timestamp_in_future' in result['warnings']
    validate_quality({'current': result})
