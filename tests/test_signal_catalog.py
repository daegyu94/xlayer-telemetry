"""Catalog consumers and the small metadata boundary exposed in the App."""
import ast
import inspect
import json
from pathlib import Path
import re

import pytest

from xlayer_telemetry.analysis.diagnosis_analysis import BASELINE_REQUIRED, SIGNAL_SCOPE, compare_signals, evaluate_rules
from xlayer_telemetry.analysis.diagnostic_signals import signal_values
from xlayer_telemetry.analysis.diagnostics import DEFAULT_QUERIES
from xlayer_telemetry.analysis.metric_queries import (
    DEFAULT_SIGNALS, PROFILE_SIGNALS, SIGNAL_CATALOG, SIGNAL_REDUCERS, SIGNAL_SOURCES, custom_query_spec,
)

ROOT = Path(__file__).resolve().parents[1]


def test_consumers_derive_current_signals_and_preserve_counter_alias():
    assert DEFAULT_QUERIES == {name: spec.expression for name, spec in DEFAULT_SIGNALS.items()}
    assert SIGNAL_SCOPE == {name: spec.scope for name, spec in SIGNAL_CATALOG.items()}
    assert BASELINE_REQUIRED == {name for name, spec in SIGNAL_CATALOG.items() if spec.baseline_required}
    assert all(SIGNAL_REDUCERS[name] == spec.statistic for name, spec in PROFILE_SIGNALS.items())
    assert SIGNAL_SOURCES['vllm_preemptions_delta'] == 'vllm_preemptions_total'
    assert SIGNAL_CATALOG['vllm_preemptions_total'] is SIGNAL_CATALOG['vllm_preemptions_delta']
    assert signal_values(None, {'vllm_preemptions_total': {'max_series_delta': 0}}, []) == {'vllm_preemptions_delta': 0}
    rows = compare_signals({'disk_busy_ratio': 0, 'gpu_utilization_percent': 0}, {})
    assert {row['signal']: (row['unit'], row['window_statistic']) for row in rows} == {
        'disk_busy_ratio': ('percentunit', 'max'), 'gpu_utilization_percent': ('percent', 'mean'),
    }


def _pressure_contract_drift(text):
    contracts = dict((name, (scope, unit, statistic)) for name, scope, unit, statistic in re.findall(
        r"(\w+): \{scope: '([^']+)', unit: '([^']+)', statistic: '([^']+)'\}", text))
    assert len(contracts) == 5
    for name, metadata in contracts.items():
        spec = SIGNAL_CATALOG[name]
        assert metadata == (spec.scope, spec.unit, spec.statistic)


def _pressure_panel_drift(text):
    # UI labels may be prose and aggregated panels have their own scopes.
    destinations = {'stage': 'agent-rl-stages', 'compute': 'compute-communication',
                    'storage': 'data-storage'}
    expected = {'vllm_requests_waiting': ('stage', 9, 'A', 'requests', 1),
                'sandbox_io_pressure_ratio': ('stage', 12, 'A', '%', 100),
                'rdma_transmit_wait_ticks_per_second': ('compute', 43, 'A', 'ticks/s', 1),
                'storage_device_busy_ratio': ('storage', 3, 'A', '%', 100),
                'gpu_utilization_percent': ('compute', 2, 'A', '%', 1)}
    blocks = re.findall(r'\{\s*name:.*?\n  \}', text[text.index('export const PRESSURE_SPECS'):], re.S)
    observed = {}
    for block in blocks:
        signal = re.search(r'signal: "([^"]+)"', block)
        if not signal:
            continue
        name = signal[1]
        dashboard = re.search(r'dashboard: "([^"]+)"', block)[1]
        panel_id = int(re.search(r'panel: (\d+)', block)[1])
        ref = re.search(r'refs: \["([^"]+)"\]', block)[1]
        unit = re.search(r'unit: "([^"]*)"', block)[1]
        scale = re.search(r'scale: (\d+)', block)
        observed[name] = (dashboard, panel_id, ref, unit, int(scale[1]) if scale else 1)
        assert name in SIGNAL_CATALOG
        source = json.loads((ROOT / 'examples/dashboards' / (destinations[dashboard]+'.json')).read_text())
        def panels(rows):
            for row in rows:
                yield row
                yield from panels(row.get('panels', []))
        panel = next(p for p in panels(source['panels']) if p['id'] == panel_id)
        assert any(target['refId'] == ref for target in panel['targets'])
        native_unit = panel['fieldConfig']['defaults']['unit']
        allowed = {'requests': {'short'}, '%': {'percent', 'percentunit'}, 'ticks/s': {'suffix:ticks/s'}}
        assert native_unit in allowed[unit]
    assert observed == expected


def test_app_metadata_and_canonical_panel_mapping_match_catalog():
    _pressure_contract_drift((ROOT / 'grafana/xlayer-app/src/distributed.ts').read_text())
    _pressure_panel_drift((ROOT / 'grafana/xlayer-app/src/scene-specs.ts').read_text())


@pytest.mark.parametrize('old,new', [('scope: \'device\'', 'scope: \'node\''),
                                   ('unit: \'percent\'', 'unit: \'ratio\''),
                                   ('statistic: \'mean\'', 'statistic: \'max\''),
                                   ('gpu_utilization_percent:', 'unknown_gpu:')])
def test_app_metadata_mutation_is_detected(old, new):
    text = (ROOT / 'grafana/xlayer-app/src/distributed.ts').read_text()
    with pytest.raises((AssertionError, KeyError)):
        _pressure_contract_drift(text.replace(old, new))


def test_panel_mapping_mutation_is_detected():
    text = (ROOT / 'grafana/xlayer-app/src/scene-specs.ts').read_text()
    with pytest.raises((AssertionError, StopIteration)):
        _pressure_panel_drift(text.replace('panel: 43', 'panel: 42'))


def test_custom_query_uses_same_metadata_contract_without_guessing_signal_meaning():
    config = {'signal': 'gpu_utilization_percent', 'query': 'custom', 'unit': 'custom_unit', 'scope': 'custom_scope'}
    spec = custom_query_spec(config)
    assert (spec.expression, spec.unit, spec.scope, spec.statistic) == ('custom', 'custom_unit', 'custom_scope', 'max')
    for change in ({'unit': ''}, {'scope': None}, {'kind': 'histogram'}, {'statistic': 'sum'}, {'statistic': []}):
        with pytest.raises(ValueError):
            custom_query_spec(config | change)


def test_every_literal_rule_signal_is_registered():
    tree = ast.parse(inspect.getsource(evaluate_rules))
    names = {call.args[0].value for call in ast.walk(tree)
             if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
             and call.func.id in {'val', 'high', 'low', 'raised', 'dropped'}
             and call.args and isinstance(call.args[0], ast.Constant)
             and isinstance(call.args[0].value, str)}
    assert names and names <= SIGNAL_CATALOG.keys()
