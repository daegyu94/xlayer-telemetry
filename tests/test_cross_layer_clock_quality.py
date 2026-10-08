"""Clock quality is an observed correlation precondition, not a service flag."""
import pytest

from xlayer_telemetry.analysis.clock_quality import assess_clocks, assess_interval
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine


def stats(value, count=3):
    return dict(min=value, max=value, mean=value, last=value, sample_count=count, series_count=1)


class ClockBackend:
    def __init__(self, *, overrides=None, bad_node=None):
        self.overrides, self.bad_node, self.calls = overrides or {}, bad_node, []

    def query_range(self, expression, start, end, step):
        self.calls.append(expression)
        for name, default in [('node_timex_maxerror_seconds', .001), ('node_timex_offset_seconds', .001),
                              ('node_timex_sync_status', 1)]:
            if name in expression:
                value = self.overrides.get(name, default)
                if self.bad_node and f'instance="{self.bad_node}"' in expression:
                    value = 0 if name == 'node_timex_sync_status' else default
                return value if isinstance(value, dict) else stats(value) if value is not None else None
        if 'node_time_seconds' in expression:
            value=self.overrides.get('sample_age' if expression.startswith('time()') else 'scrape_offset',
                                     .01 if expression.startswith('time()') else .001)
            return value if isinstance(value,dict) else stats(value)
        if 'kv_cache' in expression:
            return stats(.99)
        if 'preemptions' in expression:
            return stats(5) | {'max_series_delta':2}
        if 'waiting' in expression:
            return stats(5)
        return None


@pytest.mark.parametrize('overrides, expected', [
    ({'node_timex_maxerror_seconds':2}, 'unsafe'),
    ({'node_timex_maxerror_seconds':None}, 'unknown'),
    ({'node_timex_offset_seconds':2}, 'unsafe'),
    ({'node_timex_maxerror_seconds':stats(.001, count=1)}, 'unknown'),
    ({}, 'aligned'),
])
def test_sync_flag_and_small_scrape_offset_do_not_replace_uncertainty(overrides, expected):
    result = assess_clocks(ClockBackend(overrides=overrides).query_range,
        cluster='lab', nodes=['trainer'], start=100, end=120)
    assert result['status'] == expected
    assert 'uncertainty_seconds' in result['nodes']['trainer']


def test_mapped_application_clock_does_not_bypass_remote_ntp_failure(tmp_path):
    import json
    from xlayer_telemetry.time_alignment import CalibrationCache
    payload = dict(schema_version=1, method='four_timestamp', node='trainer', boot_id='boot',
                   reference_id='monitor', offset_seconds=-12, round_trip_seconds=.04,
                   uncertainty_seconds=.02, local_anchor=112., monotonic_anchor=50.,
                   valid_from=111.96, valid_until=172., drift_ppm=100.)
    path = tmp_path/'calibration.json'
    path.write_text(json.dumps(payload))
    mapping = CalibrationCache(path, node='trainer', wall_clock=lambda:122,
                               monotonic=lambda:60, boot_id=lambda:'boot')
    window = mapping.project(120,122)
    backend = ClockBackend(bad_node='storage')
    result = assess_interval(backend.query_range, cluster='lab', nodes=['trainer','storage','monitor'],
        window=window, producer_node='trainer', config={'calibration_reference':'monitor','monitoring_node':'monitor'})
    assert result['status'] == 'unsafe'
    assert result['system_clock_screening']['nodes']['storage']['status'] == 'unsafe'


def test_explicit_master_and_monitor_are_required_even_when_checks_disabled():
    backend = ClockBackend(bad_node='cache-master')
    config = {'cluster':'lab', 'prometheus':{'url':'unused', 'metric_profiles':['mooncake_storage'],
               'mooncake_master_node':'cache-master'}, 'clock':{'enabled':False, 'monitoring_node':'monitor'}}
    record = {'run_id':'r', 'node':'trainer', 'worker_id':'driver', 'record_id':'step-1', 'step':1,
              'analysis_window':{'start':100, 'end':120, 'accuracy':'approximate'}}
    report = DiagnosticEngine(config, prometheus=backend).analyze(record, [])
    assert {'trainer','cache-master','monitor'} <= report['clock_quality']['nodes'].keys()
    assert report['verdict'] == 'insufficient_data'
    assert not report['candidates'] and not report['comparison']['signals']
    assert report['evidence']  # Raw resource observations remain inspectable.


def test_multinode_without_monitor_identity_cannot_claim_verified_correlation():
    config = {'cluster':'lab', 'prometheus':{'url':'unused'}, 'compute_node':'gpu', 'rollout_node':'rollout'}
    record = {'run_id':'r', 'node':'trainer', 'analysis_window':{'start':100, 'end':120}}
    report = DiagnosticEngine(config, prometheus=ClockBackend()).analyze(record, [])
    assert report['clock_quality']['status'] == 'unknown'
    assert 'clock:monitoring_node:not_configured' in report['missing_sources']
    assert not report['candidates']


def test_preflight_reuses_configured_inventory_and_reports_unknown_without_installing(tmp_path):
    import json
    from xlayer_telemetry.operations.health import correlation_preflight
    path=tmp_path/'diagnostics.json'
    path.write_text(json.dumps({'schema_version':1,'cluster':'lab','compute_node':'gpu','prometheus':{'url':'http://unused'},
        'clock':{'monitoring_node':'monitor','nodes':['service']}}))
    backend=ClockBackend(bad_node='service')
    result=correlation_preflight({'DIAGNOSTICS_CONFIG':str(path),'NODE_NAME':'trainer'},
                                client=backend,now=120)
    assert result['status']=='blocked'
    assert set(result['inventory']['nodes'])=={'trainer','gpu','monitor','service'}
    assert result['clock_quality']['nodes']['service']['status']=='unsafe'
    assert result['system_time_changed'] is False
    assert correlation_preflight({'NODE_NAME':'trainer'},client=backend,now=120)['status']=='not_configured'


def test_resolution_limit_is_missing_evidence_and_prevents_strong_candidate():
    backend=ClockBackend()
    record={'run_id':'r','node':'trainer','worker_id':'driver','record_id':'one','step':1,
            'analysis_window':{'start':100,'end':120,'accuracy':'approximate'}}
    report=DiagnosticEngine({'prometheus':{'url':'unused','query_step_seconds':30}},prometheus=backend).analyze(record,[])
    candidate=next(c for c in report['candidates'] if c['id']=='kv_cache_pressure')
    assert candidate['state']=='supporting_signal'
    assert any('query_step_exceeds_interval' in missing for missing in candidate['missing_evidence'])


def test_known_source_before_window_cannot_count_as_fresh_interval_evidence():
    from xlayer_telemetry.analysis.evidence_quality import quality
    observed=quality('node_memory_MemAvailable_bytes{instance="trainer"}',100,120,5,stats(1),
                     source={'last_source_timestamp':50,'observed_source_samples':0})
    assert 'source_sample_before_interval' in observed['warnings']


def test_observed_remote_tool_host_requires_inventory_clock_evidence(tmp_path):
    from xlayer_telemetry.events import CorrelationContext,EventRecorder
    ticks=iter([101000000000,109000000000]);mono=iter([1000000000,9000000000])
    recorder=EventRecorder(tmp_path,CorrelationContext('r','agent','rollout','other','unregistered-tool'),
                          clock_ns=lambda:next(ticks),monotonic_ns=lambda:next(mono))
    with recorder.span('tool.call',phase='environment',attributes={'tool':'pytest'}):pass
    config={'cluster':'lab','prometheus':{'url':'unused'},'clock':{'monitoring_node':'monitor'},
            'sandbox':{'enabled':True,'node':'trainer','events_dir':str(tmp_path)}}
    record={'run_id':'r','node':'trainer','worker_id':'driver','step':1,'record_id':'one',
            'analysis_window':{'start':100,'end':120,'accuracy':'approximate'}}
    report=DiagnosticEngine(config,prometheus=ClockBackend()).analyze(record,[])
    assert report['clock_quality']['status']=='unknown'
    assert 'clock:unregistered-tool:unregistered_observation_node' in report['missing_sources']
    assert report['evidence']['tool_duration_seconds']['max']==8
    assert not report['candidates']


@pytest.mark.parametrize('overrides, issue', [
    ({'sample_age':5},'clock_sample_older_than_interval'),
    ({'scrape_offset':dict(min=-.15,max=.15,mean=0,last=.15,sample_count=3)},'clock_offset_variation'),
])
def test_short_window_cannot_hide_old_clock_samples_or_clock_variation(overrides,issue):
    result=assess_clocks(ClockBackend(overrides=overrides).query_range,cluster='lab',nodes=['trainer'],start=100,end=102)
    assert result['status']=='unsafe'
    assert issue in result['nodes']['trainer']['issues']


def test_summary_projects_clock_status_without_copying_whole_inventory():
    from xlayer_telemetry.analysis.diagnostics import _investigation_rows
    report=DiagnosticEngine({'prometheus':{'url':'unused'},'cluster':'lab'},prometheus=ClockBackend()).analyze(
        {'record_id':'one','node':'trainer','run_id':'r','analysis_window':{'start':100,'end':120}},[])
    row=_investigation_rows(report)[0]
    assert row['correlation_clock_status']=='aligned'
    assert row['correlation_clock_scope']=='single-resource-node'
    assert row['clock_required_nodes']==['trainer']
    assert 'clock_quality' not in row


def test_doctor_correlation_option_preserves_the_existing_command():
    from xlayer_telemetry.cli import parser
    normal=parser().parse_args(['doctor'])
    assert normal.correlation is False and normal.diagnostics_config is None
    requested=parser().parse_args(['doctor','--correlation','--diagnostics-config','diagnostics.json','--json'])
    assert requested.correlation and requested.json


def test_multinode_sync_requirement_cannot_be_disabled_for_precise_correlation():
    result=assess_interval(ClockBackend(bad_node='storage').query_range,cluster='lab',nodes=['storage'],
        producer_node='trainer',window={'start':100,'end':120},
        config={'require_sync':False,'monitoring_node':'monitor'})
    assert result['status']=='unsafe'
    assert {'trainer','storage','monitor'} <= result['nodes'].keys()
