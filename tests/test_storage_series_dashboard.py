import json
from pathlib import Path


def panels(items):
    for panel in items:
        yield panel
        yield from panels(panel.get('panels',[]))


def test_selected_metric_filters_returned_data_without_interpolating_logql():
    payload=json.loads((Path(__file__).parents[1]/'examples/dashboards/data-storage.json').read_text())
    plot=next(p for p in panels(payload['panels']) if p['id']==102)
    assert 'storage_metric' not in plot['targets'][0]['expr']
    chosen=next(t for t in plot['transformations'] if t['id']=='filterByValue')
    assert chosen['options']['filters'][0]['fieldName']=='metric_name'
    assert chosen['options']['filters'][0]['config']['id']=='equal'
    assert chosen['options']['filters'][0]['config']['options']['value']=='$storage_metric'
    assert plot['fieldConfig']['defaults']['custom']['lineWidth']==0
    assert plot['fieldConfig']['defaults']['custom']['spanNulls'] is False


def test_source_point_time_and_comparison_status_are_preserved():
    payload=json.loads((Path(__file__).parents[1]/'examples/dashboards/data-storage.json').read_text())
    by_id={p['id']:p for p in panels(payload['panels'])}
    conversions=next(t for t in by_id[102]['transformations'] if t['id']=='convertFieldType')['options']['conversions']
    assert {'targetField':'sample_timestamp_ms','destinationType':'time'} in conversions
    assert any(o['matcher'].get('options')=='comparison_status' for o in by_id[103]['fieldConfig']['overrides'])
