"""Regressions reproduced while navigating the live Grafana demo."""
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs

import pytest

ROOT = Path(__file__).parents[1]


def panels(items):
    for panel in items:
        yield panel
        yield from panels(panel.get('panels', []))


@pytest.fixture
def generated(tmp_path):
    subprocess.run([sys.executable, str(ROOT / 'scripts/provision_dashboards.py'),
                    '--enable-logs', '--output', str(tmp_path)], check=True)
    return {d['uid']: d for p in tmp_path.glob('*.json')
            if (d := json.loads(p.read_text()))}


def test_timeline_aggregates_preserve_resource_identity(generated):
    timeline = generated['xlayer-cross-layer-timeline']
    by_id = {p['id']: p for p in panels(timeline['panels'])}
    for panel_id in (21, 23, 24):
        for target in by_id[panel_id]['targets']:
            grouping = re.search(r'by\s*\(([^)]*)\)', target['expr'])
            assert grouping
            assert {'cluster', 'nodename', 'instance'} <= set(grouping[1].replace(' ', '').split(','))
            # The panel is a node total. It must not advertise a removed device label.
            assert '{{device}}' not in target['legendFormat']


def test_policy_lag_has_dimensionless_version_axis(generated):
    panel = next(p for p in panels(generated['agent-rl-stage-correlation']['panels']) if p['id'] == 7)
    lag = next(o for o in panel['fieldConfig']['overrides']
               if o['matcher'] == {'id': 'byFrameRefID', 'options': 'B'})
    properties = {p['id']: p['value'] for p in lag['properties']}
    assert properties['unit'] == 'suffix: versions'
    assert properties['custom.axisPlacement'] == 'right'
    assert panel['fieldConfig']['defaults']['unit'] == 's'


def test_active_query_filters_are_visible_in_every_generated_dashboard(generated):
    for dashboard in generated.values():
        queries = '\n'.join(t.get('expr', '') for p in panels(dashboard['panels'])
                            for t in p.get('targets', []))
        used = set(re.findall(r'\$([a-z][a-z0-9_]*)\b', queries))
        for variable in dashboard['templating']['list']:
            if variable['name'] in used:
                assert variable.get('hide', 0) == 0, (dashboard['uid'], variable['name'])


def test_reset_filters_preserves_run_cluster_and_time_but_clears_restrictions(generated):
    for dashboard in generated.values():
        reset = next(l for l in dashboard['links'] if l['title'] == 'Reset filters')
        assert reset['keepTime'] and not reset['includeVars']
        assert '${cluster:queryparam}' in reset['url']
        assert '${run_id:queryparam}' in reset['url']
        params = parse_qs(reset['url'].split('?', 1)[1])
        assert params['var-record_id'] == ['.*']
        assert params['var-trace_id'] == ['.*']
        if 'var-phase' in params:
            assert params['var-phase'] == ['$__all']
        if 'var-source_node' in params:
            assert params['var-source_node'] in (['$__all'], ['.*'])


def test_sandbox_pressure_legend_identifies_each_node(generated):
    stage = generated['agent-rl-stage-correlation']
    panel = next(p for p in panels(stage['panels']) if p['id'] == 12)
    for target in panel['targets']:
        assert '{{cluster}}' in target['legendFormat']
        assert '{{nodename}}' in target['legendFormat']


def test_run_kpis_and_timeline_keep_distinct_observation_scopes(generated):
    run = generated['telemetry-overview']
    by_id = {p['id']: p for p in panels(run['panels'])}
    gpu = by_id[33]
    expr = gpu['targets'][0]['expr']
    assert expr.startswith('topk(1, ')
    assert 'telemetry_gpu_sample_timestamp_seconds' in expr
    assert 'run_id=' not in expr
    assert '${__field.labels.gpu}' in gpu['fieldConfig']['defaults']['displayName']
    latency = by_id[32]['targets'][0]['expr']
    assert 'histogram_quantile(0.95' in latency
    assert 'instance, model_name, engine, le' in latency
    assert 'run_id=' not in latency
    assert by_id[34]['fieldConfig']['defaults']['unit'] == 'percentunit'
    assert len(by_id[34]['targets']) == 1
    # The central timeline reuses real span boundaries, never stage durations.
    assert by_id[35]['targets'] == next(
        p for p in panels(generated['xlayer-cross-layer-timeline']['panels']) if p['id'] == 2)['targets']
    assert by_id[35]['gridPos']['y'] < by_id[20]['gridPos']['y'] < by_id[7]['gridPos']['y']


def test_unknown_comparison_units_and_candidate_review_remain_inspectable(generated):
    summary = generated['xlayer-bottleneck-summary']
    by_id = {p['id']: p for p in panels(summary['panels'])}
    assert by_id[4]['title'].startswith('What changed?')
    reviews = [link for override in by_id[3]['fieldConfig']['overrides']
               for prop in override['properties'] if prop['id'] == 'links'
               for link in prop['value'] if link['title'].startswith('Review supporting')]
    assert len(reviews) == 1 and 'viewPanel=6' in reviews[0]['url']
    assert 'window_start_ms' in reviews[0]['url'] and 'record_id' in reviews[0]['url']
    for p in (by_id[4], by_id[6]):
        assert all(not t.get('options', {}).get('excludeByName') for t in p['transformations'])
        assert 'workload_comparability' in str(p['fieldConfig'])
        assert 'Unknown' in str(p['fieldConfig'])
