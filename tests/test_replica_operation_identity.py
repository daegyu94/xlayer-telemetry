from xlayer_telemetry.analysis.rollout_replicas import observations
from tests.test_rollout_replicas import INVENTORY,stats


def rows(operation,value,status='ok'):
    return {'labels':{'cluster':'lab','node':'rollout-a','instance':'a:8000','engine':'0','operation':operation,'status':status},'stats':stats(value)}


def inspect(current,baseline):
    name='mooncake_connector_rpc_p95_seconds'
    result=observations({'node':'trainer','cluster':'lab','rollout_replicas':INVENTORY,'prometheus':{}},
        {name:current},{name:baseline},{},{name:'rpc'}, {}, {'start':100,'end':120},{'start':60,'end':80},{},{})
    return result[0]['entities'][0]


def test_same_engine_get_and_put_are_preserved_separately_without_aggregating_p95():
    result=inspect([rows('GET',1),rows('PUT',2)],[rows('PUT',.2),rows('GET',.1)])
    values=result['metric_observations']
    assert len(values)==2
    assert {row['labels']['operation']:row['baseline'] for row in values}=={'GET':.1,'PUT':.2}
    assert all(row['delta'] is None for row in values)
    assert not result['signals']


def test_different_operation_or_status_does_not_supply_raw_baseline():
    for before in (rows('PUT',2),rows('GET',2,status='error')):
        result=inspect([rows('GET',1)],[before])
        assert result['signals']['mooncake_connector_rpc_p95_seconds']['baseline'] is None
