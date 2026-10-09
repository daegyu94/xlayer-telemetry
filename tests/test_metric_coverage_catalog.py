"""Capability catalogue is not a promise of live collector availability."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('metric_coverage', ROOT/'scripts/metric_coverage.py')
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)


def test_catalogue_distinguishes_definitions_bridge_aliases_and_manual_hooks():
    rows = {row['name']: row for row in coverage.catalogue(ROOT)}
    assert len(rows) == 152
    assert rows['training_tokens_per_second_per_gpu']['collection'] == 'verl_reported'
    assert rows['training_tokens_per_second']['collection'] == 'explicit_integration'
    assert rows['checkpoint_size_bytes']['collection'] == 'explicit_integration'
    assert rows['sandbox_active']['collection'] == 'runtime_instrumentation_required'
    assert rows['gpu_utilization_percent']['raw_names'] == ['telemetry_gpu_utilization_percent']
    assert 'default' in rows['gpu_utilization_percent']['query_profiles']
    assert rows['gpu_utilization_percent']['panels']
    assert rows['agent_tool_call_duration_seconds']['collection'] == 'explicit_integration'
    assert not rows['agent_tool_call_duration_seconds']['automatic_from_span']


def test_generated_catalogue_is_current_and_never_calls_smart_a_supported_source():
    assert coverage.render(ROOT) == (ROOT/'docs/_includes/metric-coverage.md').read_text()
    assert all('smart' not in row['collection'].lower() for row in coverage.catalogue(ROOT))
