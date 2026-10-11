"""Presentation changes retain source identity and measurement semantics."""
from tests.test_dashboard_bottlenecks import panels


def test_worker_and_reported_identity_tables_keep_raw_values_and_labels():
    for name,ids in [('agent-rl-stages',[3]),('run-overview',[42,44])]:
        for panel in panels(name):
            if panel['id'] not in ids:
                continue
            assert panel['type']=='table'
            assert panel['targets'][0]['instant'] is True
            assert not panel['targets'][0]['expr'].startswith(('sum(', 'avg('))
            assert panel['transformations'][0]['id']=='labelsToFields'
            assert panel['transformations'][1]['id']=='merge'
            assert not panel['transformations'][2]['options']['excludeByName']
            assert panel['gridPos']['h']>=8
            assert 'process' in panel['description']


def test_discrete_resource_states_preserve_gaps_zero_unknown_and_query_scope():
    for name,ids in [('agent-rl-stages',[40]),('compute-communication',[35,56]),
                     ('cross-layer-timeline',[13]),('data-storage',[33])]:
        for panel in panels(name):
            if panel['id'] not in ids:
                continue
            assert panel['type']=='status-history'
            assert panel['fieldConfig']['defaults']['custom']['spanNulls'] is False
            mappings=panel['fieldConfig']['defaults']['mappings']
            assert set(mappings[0]['options'])=={'0','1'}
            assert mappings[1]['options']['result']['text']=='Unknown'
            assert mappings[2]['options']['result']['text']=='Unknown / invalid'
            assert all('or vector(0)' not in target['expr'] for target in panel['targets'])
    xid=next(panel for panel in panels('compute-communication') if panel['id']==52)
    assert xid['type']=='timeseries'  # XID is an error code, not binary health.


def test_worker_age_preserves_the_existing_warning_thresholds_on_the_age_field_only():
    age=next(panel for panel in panels('agent-rl-stages') if panel['id']==3)
    field=next(override for override in age['fieldConfig']['overrides'] if override['matcher']['options']=='Sample age')
    properties={item['id']:item['value'] for item in field['properties']}
    assert [step['value'] for step in properties['thresholds']['steps']]==[None,0,300,900]
    assert properties['custom.cellOptions']['type']=='auto'
    assert 'thresholds' not in age['fieldConfig']['defaults']


def test_saved_evidence_tables_use_full_width_without_changing_saved_query_identity():
    for panel in panels('bottleneck-summary'):
        if panel['type']=='table':
            assert panel['gridPos']['w']==24
            assert all('record_id' in target['expr'] for target in panel['targets'])
