"""Presentation regressions keep categorical signals and source identities intact."""
import json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
def panels(items):
    for item in items:
        yield item
        yield from panels(item.get('panels', []))
def panel(name, number):
    return next(p for p in panels(json.loads((ROOT/'examples/dashboards'/f'{name}.json').read_text())['panels']) if p['id']==number)

@pytest.mark.parametrize('name,number', [('agent-rl-stages',40),('compute-communication',35),('compute-communication',56),('data-storage',33),('cross-layer-timeline',13)])
def test_binary_states_have_discrete_history_and_unknown_mapping(name,number):
    p=panel(name,number)
    assert p['type']=='state-timeline'
    assert p['fieldConfig']['defaults']['custom']['spanNulls'] is False
    mappings=p['fieldConfig']['defaults']['mappings']
    assert mappings[0]['options']['0']['text']!=mappings[0]['options']['1']['text']
    assert mappings[-1]['options']['result']['text']=='Unknown'
    assert 'Run health' in p['description']

@pytest.mark.parametrize('name,number', [('agent-rl-stages',3),('run-overview',42),('run-overview',44),('compute-communication',52)])
def test_multi_entity_tables_retain_native_timeseries_identity_for_other_consumers(name,number):
    p=panel(name,number)
    assert p['type']=='table'
    assert all(t.get('instant') for t in p['targets'])
    assert all(t.get('format','time_series')=='time_series' for t in p['targets'])
    assert p['transformations'][0]['id']=='labelsToFields'
    assert p['gridPos']['h']>=7
    assert any(o['matcher']['options']=='Time' and any(prop['value']=='Query evaluated at' for prop in o['properties']) for o in p['fieldConfig']['overrides'])

def test_counts_points_and_mixed_units_keep_their_graph_contracts():
    assert panel('agent-rl-stages',22)['type']=='timeseries'  # Ray counts, not a binary state.
    assert panel('data-storage',102)['fieldConfig']['defaults']['custom']['drawStyle']=='points'
    assert panel('data-storage',102)['fieldConfig']['defaults']['custom']['lineWidth']==0
    mixed=panel('agent-rl-stages',7)
    assert any(any(prop['id']=='unit' and prop['value']=='suffix: versions' for prop in o['properties']) for o in mixed['fieldConfig']['overrides'])
    assert panel('agent-rl-stages',16)['fieldConfig']['defaults']['custom']['axisSoftMin']==0
    assert panel('data-storage',32)['fieldConfig']['defaults']['custom']['axisSoftMax']==1
