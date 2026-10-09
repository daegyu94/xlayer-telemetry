"""Selected-source sampling and shared storage need their own evidence quality."""
import pytest

from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine
from xlayer_telemetry.analysis.diagnosis_analysis import evaluate_rules
from xlayer_telemetry.analysis.evidence_quality import check_source


def stats(value, count=3):
    return dict(min=value,max=value,mean=value,last=value,sample_count=count)


def records():
    before={'run_id':'r','node':'trainer','worker_id':'driver','record_id':'before','step':1,
            'boundary_scope':'rl_step','observed_at':80,'step_duration_seconds':20,
            'workload':{'perf/total_num_tokens':1000},
            'stage_durations_seconds':{'gen':5},'analysis_window':{'start':60,'end':80,'accuracy':'exact'}}
    now={**before,'record_id':'now','step':2,'observed_at':120,
         'stage_durations_seconds':{'gen':10},'analysis_window':{'start':100,'end':120,'accuracy':'exact'}}
    return now,before


class Queues:
    def __init__(self, *, sparse=False, stale=False):
        self.sparse,self.stale,self.calls=sparse,stale,[]

    def query_range_detail(self,query,start,end,step):
        self.calls.append(query)
        if 'queue_pressure' not in query:return {'aggregate':None,'series':[]}
        current=start==100
        labels=[{'node':'trainer','instance':'hot'},{'node':'trainer','instance':'cold'}]
        if query.startswith('timestamp('):
            times=[end+1,end+2] if self.stale=='future' and current else [50,50] if self.stale and current else [start+2,start+4]
            return {'aggregate':stats(times[-1]),'series':[
                {'labels':label,'stats':stats(times[-1]),'source_timestamps':times} for label in labels]}
        rows=[{'labels':labels[0],'stats':stats(5 if current else 0,1 if self.sparse and current else 3)},
              {'labels':labels[1],'stats':stats(0,10)}]
        return {'aggregate':stats(5 if current else 0,11 if self.sparse and current else 13),'series':rows}


def queue_report(source):
    now,before=records()
    config={'baseline':{'match_fields':['perf/total_num_tokens']},
        'sampling':{'check_source_freshness':True},'prometheus':{'url':'unused','query_step_seconds':2,
        'queries':{'vllm_requests_waiting':'queue_pressure{node="{rollout_node}"}'}}}
    return DiagnosticEngine(config,prometheus=source).analyze(now,[before])


def test_selected_sparse_engine_cannot_borrow_other_engine_evaluations_for_strong_signal():
    report=queue_report(Queues(sparse=True))
    candidate=next(c for c in report['candidates'] if c['id']=='rollout_queue_backlog')
    assert candidate['state']=='supporting_signal'
    assert 'current:vllm_requests_waiting:fewer_than_two_query_evaluations' in candidate['missing_evidence']
    evidence=next(e for e in candidate['evidence'] if e['signal']=='vllm_requests_waiting')
    assert evidence['labels']['instance']=='hot'
    assert evidence['sampling_quality']['current']['evaluation_count']==1
    row=next(r for r in report['comparison']['signals'] if r['signal']=='vllm_requests_waiting')
    assert row['current']==5 and row['delta'] is None


def test_source_sample_count_is_not_pooled_across_distinct_entities():
    class Sources:
        def query_range_detail(self,*args):
            return {'series':[{'labels':{'device':d},'stats':{'max':110},'source_timestamps':[110,110]}
                              for d in ('a','b')]}
    result=check_source(Sources(),'metric{node="n"}',100,120,2)
    assert result['observed_source_samples']==1


@pytest.mark.parametrize('stale',[True,'future'])
def test_known_stale_gauge_cannot_create_legacy_finding_or_rule_pressure(stale):
    report=queue_report(Queues(stale=stale))
    assert report['evidence']['vllm_requests_waiting']['max']==5
    assert not any(f['component']=='vllm' for f in report['findings'])
    candidate=next(c for c in report['candidates'] if c['id']=='rollout_queue_backlog')
    assert candidate['state']=='weak_signal'
    assert 'vllm_requests_waiting' in candidate['missing_evidence']
    assert all(e['signal']!='vllm_requests_waiting' for e in candidate['evidence']+candidate['counter_evidence'])


def test_dense_comparable_engine_retains_strong_observational_signal():
    report=queue_report(Queues())
    candidate=next(c for c in report['candidates'] if c['id']=='rollout_queue_backlog')
    assert candidate['state']=='strong_signal'
    assert candidate['missing_evidence'] == ['run_resource_attribution_unverified']


def distribution(host,value):
    return dict(metricName='storage_client.overall_latency',
                labels=dict(host=host,tag='',mount_name='',instance='batchRead',io='read',uid='',method='read',
                            pod='',thread='',statusCode=''),count=5,weighted_mean=value,max=value,max_observed_p99=value,
                report_count=1,observed_second_count=1)


class Storage:
    def query_window(self,start,end):
        return [distribution('storage-producer',20 if start==100 else 2)]


class Clocks:
    def query_range(self,query,*args):
        if 'node_time_seconds' in query:return stats(.001)
        if 'node_timex' in query:return stats(1 if 'sync_status' in query else .001)
        return None


@pytest.mark.parametrize('registered, verified', [([],False),(['other-host'],False),(['storage-producer'],True)])
def test_legacy_threefs_findings_and_deltas_require_actual_producer_clock(registered,verified):
    now,before=records();now['stage_durations_seconds']={};before['stage_durations_seconds']={}
    config={'cluster':'lab','clock':{'monitoring_node':'monitor'},'prometheus':{'url':'unused'},
            'threefs':{'url':'unused',**({'clock_nodes':registered} if registered else {})}}
    report=DiagnosticEngine(config,prometheus=Clocks(),threefs=Storage()).analyze(now,[before])
    assert bool([f for f in report['findings'] if f['component']=='3fs'])==verified
    row=next(r for r in report['comparison']['signals'] if r['signal']=='threefs_p99_latency')
    assert row['current']==20 and row['baseline']==2
    assert (row['delta'] is not None)==verified
    if not verified:
        assert report['evidence']['threefs_distributions'][0]['max_observed_p99']==20
        assert not any(any(e['signal'].startswith('threefs_') for e in c['evidence']) for c in report['candidates'])


def test_unrelated_service_disk_and_network_cannot_establish_strong_storage_path():
    current={'threefs_p99_latency':20,'storage_device_busy_ratio':.95,'network_utilization_ratio':.1,
             'threefs_throughput_bytes_per_second':100}
    base={'threefs_p99_latency':2,'threefs_throughput_bytes_per_second':100}
    labels={'threefs_p99_latency':{'host':'client-a','metricName':'storage_client.overall_latency'},
            'storage_device_busy_ratio':{'node':'ssd-b','device':'nvme0n1'},
            'network_utilization_ratio':{'node':'gateway-c','device':'eth0'}}
    candidates=evaluate_rules(current,base,thresholds={},context={'signal_labels':labels})
    assert candidates
    assert all(c['state']!='strong_signal' for c in candidates)
    assert all('storage_service_resource_relation_unverified' in c['missing_evidence'] for c in candidates)
    device=next(c for c in candidates if c['id']=='device_limited_storage')
    assert 'network_utilization_ratio:storage_path_unverified' in device['missing_evidence']
    assert not any(e['signal']=='network_utilization_ratio' for e in device['evidence']+device['counter_evidence'])


@pytest.mark.parametrize('signal, metric, unit', [
    ('storage_device_busy_ratio','device_busy','nvme0n1'),
    ('network_utilization_ratio','interface_busy','eth0'),
])
def test_core_storage_and_network_baselines_do_not_subtract_different_devices(signal,metric,unit):
    class Devices(Clocks):
        def query_range_detail(self,query,start,end,step):
            if metric not in query:return {'aggregate':None,'series':[]}
            current=start==100;value=.95 if current else .2
            row={'labels':{'instance':'trainer','device':unit if current else 'other-device'},'stats':stats(value)}
            return {'aggregate':stats(value),'series':[row]}
    now,before=records()
    report=DiagnosticEngine({'prometheus':{'url':'unused','queries':{signal:metric+'{instance="{node}"}'}}},
                            prometheus=Devices()).analyze(now,[before])
    row=next(r for r in report['comparison']['signals'] if r['signal']==signal)
    assert row['current']==.95 and row['baseline'] is None and row['delta'] is None
    assert row['labels']['device']==unit
    assert f'prometheus:{signal}:baseline_entity_match' in report['missing_sources']


def test_raw_producer_clock_does_not_accept_mapped_application_status():
    from xlayer_telemetry.analysis.clock_quality import producer_hosts_verified
    clock={'status':'aligned','scope':'mapped_workload_to_prometheus_scrape_time',
           'nodes':{'producer':{'status':'aligned'}},
           'system_clock_screening':{'nodes':{'producer':{'status':'unsafe'}}}}
    assert not producer_hosts_verified(clock,['producer'])
    assert not producer_hosts_verified({'status':'aligned','nodes':{'one':{'status':'aligned'}}},['one','two'])


def test_explicit_host_alias_preserves_valid_storage_regression_without_more_queries():
    now,before=records();source=Clocks()
    report=DiagnosticEngine({'cluster':'lab','clock':{'monitoring_node':'monitor'},'prometheus':{'url':'unused'},
        'threefs':{'url':'unused','clock_nodes':['exporter-name'],
                   'time_series':{'host_clock_nodes':{'storage-producer':'exporter-name'}}}},
        prometheus=source,threefs=Storage()).analyze(now,[before])
    assert any(f['component']=='3fs' for f in report['findings'])
    assert report['threefs_clock_quality']=={'current':True,'baseline':True}
    assert report['query_execution']['sources']['threefs']['attempted']==2
