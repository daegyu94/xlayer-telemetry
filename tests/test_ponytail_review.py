"""Public boundary regressions found during the repository review."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_run_explorer import saved
from xlayer_telemetry.analysis import llm_diagnosis as llm
from xlayer_telemetry.operations import runs

ROOT = Path(__file__).parents[1]


def catalog_cli(tmp_path, root):
    config = tmp_path / 'config.toml'
    config.write_text('[telemetry]\nTELEMETRY_HOME=' + json.dumps(str(tmp_path / 'state')) + '\n')
    result = subprocess.run([sys.executable, '-m', 'xlayer_telemetry.cli', '--config', str(config),
        'runs', 'list', '--root', str(root), '--json'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("invalid", [{"current": 5, "scope": "device"}, None, {"signal": []}])
def test_invalid_diagnosis_signal_does_not_hide_valid_run_or_peer(tmp_path, invalid):
    root = tmp_path / 'runs'
    run = saved(root, 'a'); saved(root, 'b')
    (run / 'diagnostics').mkdir()
    valid = {'signal': 'gpu_utilization_percent', 'current': 25, 'scope': 'device',
             'labels': {'node': 'gpu-a', 'gpu': '0'}, 'unit': '%', 'window_statistic': 'mean'}
    report = {'record_type': 'bottleneck_diagnosis', 'run_id': 'a',
              'comparison': {'signals': [valid, invalid]}}
    (run / 'diagnostics/diagnostics.jsonl').write_text(json.dumps(report) + '\n')
    catalog = catalog_cli(tmp_path, root)
    a, b = catalog['runs']
    assert a['run_id'] == 'a' and b['run_id'] == 'b'
    assert a['average_step'] == b['average_step'] == 10
    assert a['quality'] == 'partial' and b['quality'] == 'complete'
    assert any(row['metric'] == 'gpu_utilization_percent' for row in a['metrics'])


def test_deeply_nested_manifest_is_isolated_from_valid_peer(tmp_path):
    root = tmp_path / 'runs'
    run = saved(root, 'a'); saved(root, 'b')
    (run / 'telemetry-manifest.json').write_text('{"run_id":"a","configuration":' + '[' * 10000 + '0' + ']' * 10000 + '}')
    result = catalog_cli(tmp_path, root)
    assert [row['run_id'] for row in result['runs']] == ['b']


def test_finite_recorded_durations_keep_a_finite_catalog_mean(tmp_path):
    root = tmp_path / 'runs'; run = saved(root, 'a')
    path = run / 'telemetry-events/verl-steps.jsonl'
    record = json.loads(path.read_text())
    path.write_text(''.join(json.dumps({**record, 'record_id': str(i),
        'step_duration_seconds': 1e308}) + '\n' for i in range(2)))
    row = catalog_cli(tmp_path, root)['runs'][0]
    assert row['average_step'] == 1e308 and row['steps'] == 2


def test_bridge_skips_deep_json_and_exports_following_valid_record(tmp_path):
    source = tmp_path / 'logger.jsonl'; output = tmp_path / 'metrics'; history = tmp_path / 'steps.jsonl'
    valid = lambda step: json.dumps({'step': step, 'data': {'timing_s/gen': 1}}) + '\n'
    source.write_text(valid(1) + '{"data":' + '[' * 10000 + '0' + ']' * 10000 + '}\n' + valid(2))
    result = subprocess.run([sys.executable, '-m', 'xlayer_telemetry.adapters.verl',
        '--input', str(source), '--metrics-dir', str(output), '--history', str(history),
        '--run-id', 'review', '--node', 'gpu-a'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert [json.loads(line)['step'] for line in history.read_text().splitlines()] == [1, 2]


@pytest.mark.parametrize("missing_source", [False, True])
def test_llm_sampling_quality_is_scoped_to_each_returned_series(monkeypatch, missing_source):
    def query(self, expression, start, end, step):
        counts = {'A': 5, 'B': 1}
        rows = []
        for engine, count in counts.items():
            if expression.startswith('timestamp('):
                if missing_source and engine == 'B' and start == 90:
                    continue
                stamp = end - (1 if engine == 'A' else 6)
                rows.append({'labels': {'engine': engine}, 'stats': {'max': stamp},
                             'source_timestamps': [start, stamp] if engine == 'A' else [stamp]})
            else:
                rows.append({'labels': {'engine': engine}, 'stats': {'mean': 10, 'sample_count': count}})
        return {'series': rows, 'aggregate': {'sample_count': 6}}
    monkeypatch.setattr(llm.PrometheusClient, 'query_range_detail', query)
    packet = llm.collect_packet({'prometheus_url': 'http://unused',
        'current_interval': {'start': 90, 'end': 100}, 'baseline_interval': {'start': 70, 'end': 80},
        'query_step_seconds': 1, 'sampling': {'check_source_freshness': True},
        'queries': [{'signal': 'latency', 'query': 'latency{node="n"}', 'scope': 'service', 'unit': 's'}]})
    for observation in packet['observations']:
        engine = observation['labels']['engine']
        for window, quality in observation['sampling_quality'].items():
            missing = missing_source and engine == 'B' and window == 'current'
            assert quality['evaluation_count'] == (5 if engine == 'A' else 1)
            assert quality['observed_source_samples'] == (None if missing else 2 if engine == 'A' else 1)
            assert quality['source_age_seconds'] == (None if missing else 1 if engine == 'A' else 6)
            assert ('source_freshness_unknown' in quality['warnings']) == missing
            assert ('fewer_than_two_query_evaluations' in quality['warnings']) == (engine == 'B')
    llm.validate_packet(packet)
