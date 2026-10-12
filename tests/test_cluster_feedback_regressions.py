"""Reproduce actionable failures reported by an independent cluster operator."""
import json
from pathlib import Path
import re

import pytest

from xlayer_telemetry.telemetry_health import finish

ROOT = Path(__file__).parents[1]


def test_loki_all_filters_have_a_nonempty_stream_matcher():
    d = json.loads((ROOT / 'examples/dashboards/run-logs.json').read_text())
    expressions = [target['expr'] for p in d['panels'] for target in p.get('targets', [])]
    expressions += [v['query'] for v in d['templating']['list'] if 'label_values({' in v.get('query', '')]
    assert expressions
    for expression in expressions:
        selector = re.search(r'\{[^}]*\}', expression).group()
        matches = re.findall(r'[a-zA-Z_]\w*\s*(=~|=)\s*"([^"]*)"', selector)
        assert any(value and '$' not in value and (op == '=' or re.fullmatch(value, '') is None)
                   for op, value in matches), expression


@pytest.mark.parametrize('outcome', ['missing', 'failed', 'interrupted'])
def test_terminal_health_does_not_reuse_live_sidecar_observations(tmp_path, outcome):
    (tmp_path / 'telemetry-health.json').write_text(json.dumps({
        'sidecars': {'bridge': {'pid': 99999999, 'alive': True, 'status': 'observed'}}}))
    result = finish(tmp_path, 7, outcome, 'disabled')
    assert result['workload']['exit_code'] == 7
    assert result['sidecars']['bridge']['alive'] is False
    assert result['sidecars']['bridge']['status'] == 'stopped'
    assert f'bridge:final_export:{outcome}' in result['issues']


def test_mooncake_batch_counters_keep_rpc_and_item_units_separate():
    d = json.loads((ROOT / 'examples/dashboards/agent-rl-stages.json').read_text())
    row = next(p for p in d['panels'] if p['id'] == 95)
    queries = [t['expr'] for p in row['panels'] for t in p.get('targets', [])]
    for name in ['master_batch_put_start_requests_total', 'master_batch_put_start_items_total',
                 'master_batch_put_start_failures_total', 'master_batch_get_replica_list_requests_total']:
        assert any(name in q for q in queries), name
    vocabulary = json.loads((ROOT / 'config/metrics.json').read_text())
    names = {m['source'] for m in vocabulary['metrics']}
    assert any('master_batch_put_start_items_total' in source for source in names)


def test_terminal_health_reports_a_still_running_sidecar_as_pending(tmp_path):
    import os
    (tmp_path / 'telemetry-health.json').write_text(json.dumps({
        'sidecars': {'bridge': {'pid': os.getpid(), 'alive': True, 'status': 'observed'}}}))
    result = finish(tmp_path, 7, 'interrupted', 'disabled')
    assert result['sidecars']['bridge']['alive'] is True
    assert result['sidecars']['bridge']['status'] == 'stop_pending'


def test_async_history_deduplicates_exact_records_but_preserves_distinct_updates(tmp_path):
    from xlayer_telemetry.step_history import StepHistoryWriter
    path = tmp_path / 'history.jsonl'
    first = {'step': 3, 'data': {'timing_s/step': 1.0, 'policy_version': 2}}
    other = {'step': 3, 'data': {'timing_s/step': 2.0, 'policy_version': 3}}
    writer = StepHistoryWriter(path, run_id='r', node='n', worker_id='driver', execution_mode='async')
    assert writer.append(first) is not None
    assert writer.append(first) is None
    assert writer.append(other) is not None
    restarted = StepHistoryWriter(path, run_id='r', node='n', worker_id='driver', execution_mode='async')
    assert restarted.append(first) is None and restarted.append(other) is None
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 2 and len({row['record_id'] for row in rows}) == 2
    assert all(row['boundary_scope'] == 'trainer_update' for row in rows)


def test_batch_request_rates_do_not_report_legacy_zero_as_no_batch_activity(tmp_path):
    import os
    import shutil
    import subprocess
    tool = os.environ.get('PROMTOOL') or shutil.which('promtool')
    if not tool:
        pytest.skip('set PROMTOOL for actual batch PromQL evaluation')
    d = json.loads((ROOT / 'examples/dashboards/agent-rl-stages.json').read_text())
    row = next(p for p in d['panels'] if p['id'] == 95)
    panel = next(p for p in row['panels'] if p['id'] == 68)
    expression = panel['targets'][0]['expr'].replace('$cluster', 'lab').replace('$node', 'gpu').replace('$__rate_interval', '1m')
    labels = '{job="native",telemetry_source="mooncake",cluster="lab",node="gpu",instance="master:19203",component="master"}'
    fixture = {'rule_files': [], 'evaluation_interval': '30s', 'tests': [{
        'interval': '30s', 'input_series': [
            {'series': 'master_put_start_requests_total' + labels, 'values': '0+0x4'},
            {'series': 'master_batch_put_start_requests_total' + labels, 'values': '0+1x4'}],
        'promql_expr_test': [{'expr': expression, 'eval_time': '2m',
            'exp_samples': [{'labels': labels, 'value': 1 / 30}]}]}]}
    path = tmp_path / 'batch.json'
    path.write_text(json.dumps(fixture))
    result = subprocess.run([tool, 'test', 'rules', str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('sidecars', [None, {'bridge': None}, {'bridge': []}])
def test_terminal_health_handles_malformed_old_sidecar_state(tmp_path, sidecars):
    (tmp_path / 'telemetry-health.json').write_text(json.dumps({'sidecars': sidecars}))
    result = finish(tmp_path, 7, 'interrupted', 'disabled')
    assert result['workload']['exit_code'] == 7
    assert isinstance(result['sidecars'], dict)
