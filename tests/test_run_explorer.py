import json
from pathlib import Path
import pytest
from xlayer_telemetry.operations import runs


def saved(root, name, duration=10, model='Qwen', fingerprint='batch-v1', **fields):
    run = root / name
    (run/'telemetry-events').mkdir(parents=True)
    config = {'cluster': 'lab', 'observer_node': 'gpu-a', 'model_identifier': model,
              'execution_mode': 'sync', **({'workload_fingerprint': fingerprint} if fingerprint else {})}
    (run/'telemetry-manifest.json').write_text(json.dumps({'schema_version': 1, 'run_id': name,
        'created_at': '2026-10-10T00:00:00+00:00', 'configuration': config}))
    (run/'telemetry-health.json').write_text(json.dumps({'workload': {'status': 'exited', 'exit_code': 0}}))
    record = {'schema_version': 1, 'record_id': name+'-step', 'run_id': name, 'node': 'gpu-a', 'worker_id': 'driver',
        'execution_mode': 'sync', 'boundary_scope': 'rl_step', 'step': 1, 'step_duration_seconds': duration,
        'stage_durations_seconds': {'rollout': 8}, 'workload': {'perf/total_num_tokens': 100},
        'analysis_window': {'start': 100, 'end': 100+duration, 'accuracy': 'approximate'},
        'window_start_ms': 100000, 'window_end_ms': (100+duration)*1000, **fields}
    (run/'telemetry-events/verl-steps.jsonl').write_text(json.dumps(record)+'\n')
    return run


def test_saved_runs_are_searchable_after_backend_retention_and_do_not_leak_paths_or_argv(tmp_path):
    saved(tmp_path, 'a')
    catalog = runs.catalog(tmp_path)
    row = catalog['runs'][0]
    assert row['run_id'] == 'a' and row['model'] == 'Qwen' and row['steps'] == 1
    assert row['metrics'][0]['value'] == 10
    assert str(tmp_path) not in json.dumps(catalog)
    assert row['source'] == 'stored_artifact' and row['status'] == 'reported_completed'


def test_comparison_requires_recorded_workload_scope_entity_and_quality(tmp_path):
    saved(tmp_path, 'a'); saved(tmp_path, 'b', 15)
    a,b = runs.catalog(tmp_path)['runs']
    comparison = runs.compare(a,b)
    assert comparison['comparability'] == 'Verified'
    assert comparison['metrics'][0]['delta_percent'] == 50
    b['model'] = 'Llama'
    comparison = runs.compare(a,b)
    assert comparison['comparability'] == 'Incomparable'
    assert all(row['delta_percent'] is None for row in comparison['metrics'])


def test_unknown_fingerprint_and_resource_owner_do_not_produce_delta(tmp_path):
    saved(tmp_path, 'a', fingerprint=None); saved(tmp_path, 'b', 15, fingerprint=None)
    a,b = runs.catalog(tmp_path)['runs']
    result = runs.compare(a,b)
    assert result['comparability'] == 'Partial'
    assert result['metrics'][0]['delta_percent'] is None
    assert 'workload_fingerprint_missing' in result['reasons']


def test_multiworker_identity_does_not_become_a_run_average_and_wrong_run_record_is_ignored(tmp_path):
    run = saved(tmp_path, 'a')
    path = run/'telemetry-events/verl-steps.jsonl'
    record = json.loads(path.read_text())
    path.write_text(path.read_text()+json.dumps({**record,'record_id':'worker','worker_id':'other','step_duration_seconds':99})+'\n'+
        json.dumps({**record,'record_id':'foreign','run_id':'b','step_duration_seconds':999})+'\n')
    row = runs.catalog(tmp_path)['runs'][0]
    assert row['steps'] == 2 and row['average_step'] is None
    assert len([m for m in row['metrics'] if m['metric']=='step_duration_seconds']) == 2


def test_zero_partial_tail_and_synthetic_origin_are_preserved(tmp_path):
    run = saved(tmp_path, 'a', 0, data_origin='synthetic')
    saved(tmp_path, 'b', 1, data_origin='synthetic')
    a,b = runs.catalog(tmp_path)['runs']
    assert a['average_step'] == 0 and a['data_origin'] == 'synthetic'
    assert runs.compare(a,b)['metrics'][0]['delta_percent'] is None
    with (run/'telemetry-events/verl-steps.jsonl').open('a') as stream: stream.write('{partial')
    a,b = runs.catalog(tmp_path)['runs']
    assert a['quality'] == 'partial' and runs.compare(a,b)['comparability'] == 'Partial'


def test_projection_is_read_only_metadata_and_bounded(tmp_path):
    saved(tmp_path/'runs','a')
    target = tmp_path/'dashboards'
    result = runs.publish(tmp_path/'runs', target)
    dashboard = json.loads((target/'xlayer-run-catalog.json').read_text())
    assert dashboard['uid'] == runs.CATALOG_UID
    assert dashboard['xlayerRunCatalog']['runs'][0]['run_id'] == 'a'
    assert dashboard['panels'] == [] and result['run_count'] == 1


def test_explicit_mixed_origin_unknown_scope_and_shared_metrics_are_not_job_attribution(tmp_path):
    saved(tmp_path,'a'); saved(tmp_path,'b',15,data_origin='synthetic')
    a,b = runs.catalog(tmp_path)['runs']
    assert runs.compare(a,b)['comparability'] == 'Incomparable'
    a['metrics'].append({'metric':'gpu_utilization','value':75,'unit':'%','scope':'node/device','statistic':'mean',
                         'entity':{'node':'gpu-a','gpu':'0'},'quality':'clock_unknown'})
    b['data_origin'] = a['data_origin']
    b['metrics'].append({**a['metrics'][-1],'value':62})
    row = runs.compare(a,b)['metrics'][-1]
    assert row['delta_percent'] is None and row['scope']=='node/device'


def test_different_workload_populations_with_same_unique_shapes_are_not_comparable(tmp_path):
    a=saved(tmp_path,'a'); b=saved(tmp_path,'b')
    for run,repeats in ((a,1),(b,2)):
        path=run/'telemetry-events/verl-steps.jsonl'; record=json.loads(path.read_text())
        with path.open('a') as stream:
            for i in range(repeats):
                stream.write(json.dumps({**record,'record_id':f'other-{i}','workload':{'perf/total_num_tokens':200}})+'\n')
    a,b=runs.catalog(tmp_path)['runs']
    assert runs.compare(a,b)['metrics'][0]['delta_percent'] is None


def test_malformed_saved_records_and_user_catalog_destination_are_preserved(tmp_path):
    run=saved(tmp_path,'a')
    (run/'telemetry-events/verl-steps.jsonl').write_text('{malformed\n')
    row=runs.catalog(tmp_path)['runs'][0]
    assert row['steps']==0 and row['average_step'] is None and row['quality']=='partial'
    target=tmp_path/'dashboards';target.mkdir();(target/'xlayer-run-catalog.json').write_text('user data')
    import pytest
    with pytest.raises(ValueError):runs.publish(tmp_path,target)
    assert (target/'xlayer-run-catalog.json').read_text()=='user data'


def test_window_extrema_require_equal_recorded_exposure_and_population(tmp_path):
    saved(tmp_path,'a');saved(tmp_path,'b')
    a,b=runs.catalog(tmp_path)['runs']
    metric={'metric':'storage_p99','value':10,'unit':'ms','scope':'shared_service','entity':{'host':'ds'},
        'quality':'complete','statistic':'maximum_reported_p99','exposure':{'interval_seconds':10,'evaluation_count':10}}
    a['metrics'].append(metric);b['metrics'].append({**metric,'value':20,'exposure':{'interval_seconds':20,'evaluation_count':20}})
    assert runs.compare(a,b)['metrics'][-1]['delta_percent'] is None


def test_python_and_frontend_share_the_recorded_comparison_contract():
    fixture=json.loads((Path(__file__).parents[1]/'grafana/xlayer-app/tests/fixtures/run-comparison.json').read_text())
    result=runs.compare(fixture['a'],fixture['b'])
    result={'comparability':result['comparability'],'reasons':result['reasons'],
            'metrics':[{key:row.get(key) for key in ('metric','a','b','delta','delta_percent','reasons')} for row in result['metrics']]}
    assert result==fixture['expected']


@pytest.mark.parametrize('code,bridge,expected', [(0, 'ok', 'reported_completed'), (1, 'ok', 'reported_failed'),
    (130, 'interrupted', 'reported_interrupted'), (143, 'interrupted', 'reported_interrupted'),
    (0, 'failed', 'reported_completed')])
def test_actual_health_writer_outcome_is_independent_of_telemetry_completeness(tmp_path, code, bridge, expected):
    from xlayer_telemetry.telemetry_health import finish
    run = saved(tmp_path, 'actual-writer')
    health = finish(run, code, bridge, 'disabled')
    row = runs.summarize(run)
    assert row['status'] == expected
    assert row['workload_exit_code'] == code
    assert row['telemetry_status'] == health['status']
    if bridge != 'ok':
        assert row['quality'] == 'partial'


@pytest.mark.parametrize('change', ['delete', 'replace', 'append'])
def test_changed_history_is_partial_without_interrupting_other_runs(tmp_path, monkeypatch, change):
    broken = saved(tmp_path, 'a'); saved(tmp_path, 'b')
    path = broken/'telemetry-events/verl-steps.jsonl'
    original = runs._CACHE.read
    def read(selected):
        rows = list(original(selected))
        if selected == path:
            if change == 'delete': path.unlink()
            elif change == 'replace':
                replacement = path.with_suffix('.new')
                replacement.write_bytes(path.read_bytes())
                replacement.replace(path)
            else:
                with path.open('a') as stream: stream.write('{partial')
        yield from rows
    monkeypatch.setattr(runs._CACHE, 'read', read)
    rows = runs.catalog(tmp_path)['runs']
    assert rows[0]['quality'] == 'partial'
    assert rows[0]['average_step'] is None
    assert rows[1]['quality'] == 'complete' and rows[1]['average_step'] == 10


def test_one_run_read_error_is_isolated_and_failed_publication_preserves_catalog(tmp_path, monkeypatch):
    root = tmp_path/'runs'; saved(root,'a'); saved(root,'b')
    output = tmp_path/'dashboards'
    runs.publish(root, output)
    destination = output/'xlayer-run-catalog.json'; before = destination.read_bytes()
    original = runs.summarize
    def read(path):
        if path.name == 'a': raise PermissionError('injected artifact unreadable')
        return original(path)
    monkeypatch.setattr(runs,'summarize',read)
    result = runs.catalog(root)
    assert [row['run_id'] for row in result['runs']] == ['b']
    assert result['unavailable_runs'] == [{'run_id':'a','reason':'artifact_unreadable_or_invalid'}]
    monkeypatch.setattr(runs,'atomic_write_text',lambda *args: (_ for _ in ()).throw(OSError('injected publish failure')))
    with pytest.raises(OSError): runs.publish(root,output)
    assert destination.read_bytes() == before


@pytest.mark.parametrize('count,partial', [(4999,False),(5000,False),(5001,True)])
def test_record_limit_preserves_partial_semantics(tmp_path, count, partial):
    run=saved(tmp_path,'a'); path=run/'telemetry-events/verl-steps.jsonl'
    row=json.loads(path.read_text())
    with path.open('w') as stream:
        for i in range(count): stream.write(json.dumps({**row,'record_id':str(i)})+'\n')
    result=runs.summarize(run)
    assert result['steps'] == min(count,5000)
    assert result['quality'] == ('partial' if partial else 'complete')
    assert result['average_step'] == 10


@pytest.mark.parametrize('size', [runs.MAX_BYTES-1, runs.MAX_BYTES, runs.MAX_BYTES+1])
def test_byte_limit_never_generates_zero_or_complete_empty_history(tmp_path,size):
    run=saved(tmp_path,'a'); path=run/'telemetry-events/verl-steps.jsonl'
    with path.open('wb') as stream: stream.write(b' '*size)
    result=runs.summarize(run)
    assert result['steps'] == 0 and result['average_step'] is None and result['quality']=='partial'


@pytest.mark.parametrize('count,truncated', [(99,False),(100,False),(101,True)])
def test_run_limit_is_exposed_without_fabricating_missing_averages(tmp_path,count,truncated):
    for i in range(count): saved(tmp_path,f'run-{i:03}')
    result=runs.catalog(tmp_path)
    assert len(result['runs']) == min(count,100) and result['truncated'] is truncated
