"""Query collection preserves independent failure, zero and source-quality bounds."""
from xlayer_telemetry.analysis.metric_observations import collect_metrics


def test_collection_preserves_query_annotations_zero_and_missing_baseline_after_peer_failure():
    class Client:
        def __init__(self): self.queries = []
        def query_range_detail(self, expression, start, end, step):
            self.queries.append((expression, start, end, step))
            if expression == 'broken': raise TimeoutError('fixture query failure')
            return {'aggregate': {'mean': 0, 'sample_count': 2} if start == 90 else None,
                    'series': [{'labels': {'node': 'n'}, 'stats': {'mean': 0, 'sample_count': 2}}],
                    'result_quality': {'scope': 'query', 'warning_count': 1, 'info_count': 0,
                                       'discarded_sample_count': 0, 'discarded_series_count': 0}}
    client = Client()
    collected = collect_metrics(client, {'bad': 'broken', 'gpu': 'gpu{node="{node}"}'},
        {'node': 'a"b'}, 90, 100, 2, baseline_window={'start': 70, 'end': 80})
    assert collected.evidence['gpu']['mean'] == 0 and 'gpu' not in collected.baseline
    assert collected.current_series['gpu'][0]['labels'] == {'node': 'n'}
    assert collected.executed_queries['gpu'] == 'gpu{node="a\\"b"}'
    assert 'prometheus:bad:TimeoutError' in collected.missing
    assert 'prometheus:gpu:baseline_no_data' in collected.missing
    assert 'prometheus:gpu:current:backend_warnings:1' in collected.missing
    assert collected.sampling_quality['gpu']['current']['query_result']['warning_count'] == 1
    assert len(client.queries) == 3
