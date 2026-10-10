"""Actual demo producers → saved artifact projection, without hardware claims."""
import copy
import json

from xlayer_telemetry.demos.multi_job import make_schedule, record_job
from xlayer_telemetry.operations import runs


def test_actual_multi_job_artifacts_preserve_metadata_through_catalog_and_publish(tmp_path):
    root = tmp_path / 'runs'
    schedule = make_schedule(start=1000, node='shared-node')
    for job in schedule['jobs']:
        record_job(root / job['scenario']['run_id'], job)
    result = runs.catalog(root)
    assert len(result['runs']) == 3
    for job in schedule['jobs']:
        row = next(row for row in result['runs'] if row['run_id'] == job['scenario']['run_id'])
        assert row['model'] == job['model']
        assert row['data_origin'] == 'synthetic'
        assert row['cluster'] == 'scenes-demo'
        assert row['observer_node'] == 'shared-node'
        assert row['execution_mode'] == 'sync'  # Actual StepHistoryWriter boundary, not inferred from async scalars.
        assert row['steps'] == 2
    runs.publish(root, tmp_path / 'dashboards')
    published = json.loads((tmp_path / 'dashboards/xlayer-run-catalog.json').read_text())['xlayerRunCatalog']
    assert published['runs'] == result['runs']


def test_legacy_demo_metadata_is_not_reclassified_as_observed(tmp_path):
    job = make_schedule(start=1000, node='shared-node')['jobs'][0]
    root = tmp_path / 'legacy'
    record_job(root, job)
    path = root / 'telemetry-manifest.json'
    manifest = json.loads(path.read_text())
    manifest['configuration'] = {}  # Previously-produced demo format.
    path.write_text(json.dumps(manifest))
    row = runs.summarize(root)
    assert row['model'] == job['model']
    assert row['data_origin'] == 'synthetic'
    assert row['cluster'] is None  # No cluster declaration in the legacy artifact.


def test_actual_producer_pair_preserves_values_and_blocks_incomparable_control(tmp_path):
    job = make_schedule(start=1000, node='shared-node')['jobs'][0]
    for name, model in [('a', job['model']), ('b', job['model']), ('control', 'Different synthetic model')]:
        source = copy.deepcopy(job)
        source['scenario']['run_id'] = name
        source['model'] = model
        record_job(tmp_path / name, source)
    a, b, control = [runs.summarize(tmp_path / name) for name in ('a', 'b', 'control')]
    assert runs.compare(a, b)['comparability'] == 'Verified'
    comparison = runs.compare(a, control)
    assert comparison['comparability'] == 'Incomparable'
    assert all(row['delta_percent'] is None for row in comparison['metrics'])
    assert a['metrics'] and control['metrics']


def test_conflicting_model_and_origin_declarations_remain_partial(tmp_path):
    job = make_schedule(start=1000, node='shared-node')['jobs'][0]
    root = tmp_path / 'run'
    record_job(root, job)
    path = root / 'telemetry-manifest.json'
    manifest = json.loads(path.read_text())
    manifest['model']['identifier'] = 'Conflicting model'
    manifest['data_origin'] = 'observed'
    path.write_text(json.dumps(manifest))
    row = runs.summarize(root)
    assert row['model'] is None and row['data_origin'] == 'mixed'
    assert row['quality'] == 'partial'
    assert set(row['metadata_conflicts']) == {'model', 'data_origin'}
    assert row['metrics']
