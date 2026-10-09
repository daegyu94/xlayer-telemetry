"""Pure CPU contracts for bounded, context-preserving visual validation."""
import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


spec = importlib.util.spec_from_file_location('design_validate', Path(__file__).with_name('design_validate.py'))
DESIGN = importlib.util.module_from_spec(spec)
spec.loader.exec_module(DESIGN)


def test_all_thirteen_screens_have_distinct_app_paths_and_reference_hashes():
    assert len(DESIGN.SCREENS) == 13
    assert len({value[0] for value in DESIGN.SCREENS.values()}) == 13
    assert DESIGN.SCREENS['deep-dive'][0] == 'deep'
    assert DESIGN.SCREENS['infrastructure'][0] == 'infra'
    assert DESIGN.SCREENS['bottleneck-summary'][0] == 'summary'
    assert DESIGN.selected_screens('overview,storage,overview') == ['overview', 'storage']
    with pytest.raises(ValueError, match='Unknown screen'):
        DESIGN.selected_screens('overview,v2')


@pytest.mark.parametrize('url', ['https://example.com', 'file:///tmp/a',
                              'http://secret:password@localhost:3000', 'http://localhost.evil.test'])
def test_only_owned_loopback_without_credentials_is_opened(url):
    with pytest.raises(ValueError, match='loopback'):
        DESIGN.owned_origin(url)


def test_routes_preserve_multi_entity_and_selected_step_without_exporting_secret_query():
    context = DESIGN.selected_context('http://localhost:3000/a/xlayer-telemetry-app?from=1000&to=2000'
        '&var-run_id=job-a&var-run_id=job-b&var-worker=w0&var-record_id=step-id'
        '&var-device=nvme0n1&token=secret&orgId=1')
    url = DESIGN.app_url('http://localhost:3000', 'storage', context, 'dark')
    query = parse_qs(urlparse(url).query)
    assert query['var-run_id'] == ['job-a', 'job-b']
    assert query['var-record_id'] == ['step-id'] and query['var-device'] == ['nvme0n1']
    assert query['theme'] == ['dark'] and query['from'] == ['1000']
    assert 'secret' not in url and 'token' not in query and 'orgId' not in query
    assert DESIGN.context_changes(context, DESIGN.selected_context(url)) == {}


def test_context_comparison_accepts_scenes_epoch_iso_and_all_normalization_but_detects_loss():
    before = {'from': ['1000'], 'to': ['2000'], 'var-node': ['$__all'],
              'var-record_id': ['selected'], 'var-run_id': ['b', 'a']}
    after = {'from': ['1970-01-01T00:00:01.000Z'], 'to': ['1970-01-01T00:00:02.000Z'],
             'var-node': ['.*'], 'var-record_id': ['selected'], 'var-run_id': ['a', 'b'],
             'var-extra_default': ['.*']}
    assert DESIGN.context_changes(before, after) == {}
    del after['var-record_id']
    assert DESIGN.context_changes(before, after) == {
        'var-record_id': {'before': ['selected'], 'after': None}}
    assert DESIGN.context_changes({'var-candidate_id': []}, {}) == {}


def test_existing_selected_context_fixture_is_reused_without_prometheus_or_playwright():
    result = DESIGN.selected_context('http://127.0.0.1:3000?var-cluster=initial',
        {'selected_context': {'var-cluster': ['scenes-demo'], 'var-record_id': ['record'],
                              'var-worker': 'rollout-1', 'from': '1000', 'to': '2000'}})
    assert result['var-cluster'] == ['scenes-demo']
    assert result['var-worker'] == ['rollout-1'] and result['from'] == ['1000']
    assert DESIGN.VIEWPORTS == {'desktop': {'width': 1440, 'height': 1000},
                               'mobile': {'width': 390, 'height': 844}}


def test_comparison_html_preserves_reference_and_actual_pair_with_safe_labels(tmp_path):
    filename = DESIGN.comparison_html(tmp_path, [{'screen': '<unsafe>', 'viewport': 'desktop',
        'theme': 'light', 'reference_screenshot': 'reference.png', 'screenshot': 'actual.png'}])
    html = (tmp_path / filename).read_text()
    assert '&lt;unsafe&gt;' in html and '<unsafe>' not in html
    assert html.index('src="reference.png"') < html.index('src="actual.png"')
    assert 'No pixel-equality score is claimed' in html
