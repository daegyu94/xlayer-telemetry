"""CPU math contracts for the bounded DOM audit, without a browser dependency."""
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location('readability_validate', Path(__file__).with_name('readability_validate.py'))
READ = importlib.util.module_from_spec(spec)
spec.loader.exec_module(READ)


def test_css_color_supports_resolved_srgb_and_alpha_without_guessing_unknown_paint():
    assert READ.parse_css_color('#000') == (0, 0, 0, 1)
    assert READ.parse_css_color('#ffff') == (1, 1, 1, 1)
    assert READ.parse_css_color('rgba(255, 0, 0, .5)') == (1, 0, 0, .5)
    assert READ.parse_css_color('rgb(100% 0% 0% / 50%)') == (1, 0, 0, .5)
    assert READ.parse_css_color('color(srgb 1 0 0 / .5)') == (1, 0, 0, .5)
    assert READ.parse_css_color('transparent') == (0, 0, 0, 0)
    for value in ('linear-gradient(red,blue)', 'url(#svgGradient)', 'color(display-p3 1 0 0)', 'rgba(nan, 0, 0, 1)'):
        assert READ.parse_css_color(value) is None


def test_source_over_alpha_preserves_transparent_layers_and_opaque_foreground():
    assert READ.composite((0, 0, 0, 0), (1, 1, 1, 1)) == (1, 1, 1, 1)
    assert READ.composite((0, 0, 0, 1), (1, 1, 1, 1)) == (0, 0, 0, 1)
    assert READ.composite((0, 0, 0, .5), (1, 1, 1, 1)) == (.5, .5, .5, 1)
    assert READ.composite((0, 0, 0, 0), (0, 0, 0, 0)) == (0, 0, 0, 0)


def test_element_background_and_ancestor_opacity_are_composited_as_a_group():
    front, background = READ.effective_colors('black', [
        {'color': 'white', 'opacity': .5}, {'color': '#000', 'opacity': 1}])
    assert front == (0, 0, 0, 1) and background == (.5, .5, .5, 1)
    assert READ.effective_colors('rgba(0,0,0,.5)', [{'color': 'white'}]) == (
        (.5, .5, .5, 1), (1, 1, 1, 1))
    assert READ.effective_colors('black', [{'color': 'url(#gradient)'}]) is None
    assert READ.effective_colors('black', [{'color': 'white', 'opacity': 'invalid'}]) is None
    assert READ.effective_colors('black', [{'color': 'white'}], .5) == (
        (.5, .5, .5, 1), (1, 1, 1, 1))


def test_known_wcag_contrast_boundaries_use_unrounded_values():
    black, white = READ.parse_css_color('black'), READ.parse_css_color('white')
    assert READ.contrast_ratio(black, white) == pytest.approx(21)
    assert READ.contrast_ratio(white, black) == pytest.approx(21)
    assert READ.contrast_ratio(black, black) == pytest.approx(1)
    assert READ.contrast_ratio(READ.parse_css_color('#767676'), white) > 4.5
    assert READ.contrast_ratio(READ.parse_css_color('#777777'), white) < 4.5


def test_large_text_threshold_uses_18pt_regular_and_14pt_bold_not_18px():
    assert READ.contrast_threshold(23.99, 400) == 4.5
    assert READ.contrast_threshold(24, 400) == 3
    assert READ.contrast_threshold(18.66, 700) == 4.5
    assert READ.contrast_threshold(14 * 96 / 72, 700) == 3
    assert READ.contrast_threshold(20, 600) == 4.5


def sample(color='#777777', size=12, unknown=None):
    return {'role': 'metadata', 'text': 'Source scope', 'font_px': size, 'weight': 400,
            'color': color, 'layers': [{'color': 'white'}], 'unknown_paint': unknown or []}


def test_unknown_background_is_neither_passed_nor_counted_as_contrast_failure():
    inactive = {**sample(), 'exempt_reason': 'Inactive control (contrast requirement exception)'}
    report = READ.summarize_text([sample(), sample('#000', unknown=['background image / gradient']), inactive])
    assert report['roles']['metadata']['samples'] == 3
    assert report['roles']['metadata']['qualified'] == 1
    assert report['roles']['metadata']['unknown'] == 1
    assert report['roles']['metadata']['exempt'] == 1
    assert report['roles']['metadata']['below_threshold'] == 1
    assert len(report['unknown_examples']) == 1


def test_small_text_is_separate_readability_observation_with_bounded_examples():
    report = READ.summarize_text([sample('#000', 11)] * 100)
    assert report['roles']['metadata']['below_12px'] == 100
    assert report['roles']['metadata']['below_threshold'] == 0
    assert len(report['small_text_examples']) == 40
    assert len(report['roles']['metadata']['worst_examples']) == 6


def test_audit_screenshot_sizes_include_tablet_and_original_context_contract():
    assert READ.VIEWPORTS == {'desktop': {'width': 1440, 'height': 1000},
                              'tablet': {'width': 1024, 'height': 1000},
                              'mobile': {'width': 390, 'height': 844}}
    assert 'elementFromPoint' in READ.TEXT_AUDIT_JS
    assert 'SVG paint background needs manual review' in READ.TEXT_AUDIT_JS
    assert "element!==document.body" in READ.SCROLL_TARGET_JS
    assert 'document.scrollingElement' in READ.SCROLL_TARGET_JS


def test_comparison_page_escapes_text_and_distinguishes_synthetic_reference(tmp_path):
    name = READ.comparison_page(tmp_path, [{'screen': '<component>', 'viewport': 'mobile',
        'theme': 'dark', 'screenshot': 'actual.png', 'reference_screenshot': 'reference.png'}])
    html = (tmp_path / name).read_text()
    assert '&lt;component&gt;' in html and '<component>' not in html
    assert 'Reference values are fictional' in html
    assert html.index('reference.png') < html.index('actual.png')
