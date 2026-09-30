"""Run discovery, measurement quality and explicit investigation contracts."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from xlayer_telemetry.analysis.diagnosis_analysis import select_baseline, validate_baseline_policy
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, _investigation_rows
from xlayer_telemetry.analysis.evidence_quality import quality, timestamp_query, check_source
from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.analysis.llm_diagnosis import packet_from_report, model_view, validate_packet
from xlayer_telemetry.analysis.llm_investigation import selected_report, project_result
from xlayer_telemetry.metrics import Metric, MetricEmitter
from xlayer_telemetry.metrics.textfile import collect_snapshots, build_metrics
from xlayer_telemetry.sandbox import SandboxRecorder, device_window
from xlayer_telemetry.collectors.sandbox_sampler import parse_io_devices
from xlayer_telemetry.show_run import summarize
from xlayer_telemetry.step_history import StepHistoryWriter
from xlayer_telemetry.telemetry_health import observe, finish

ROOT = Path(__file__).parents[1]


def emit(root, run, node, stamp=100):
    MetricEmitter(root / run / 'telemetry-metrics', run_id=run, producer='app',
                  role='trainer', worker_id='0', node=node, clock=lambda: stamp).emit(
                      step=1, samples=[Metric('training_loss', 1)])


def test_run_discovery_concurrent_nodes_expiry_and_finished(tmp_path):
    with ThreadPoolExecutor() as pool:
        list(pool.map(lambda item: emit(tmp_path, *item), [('a', 'n1'), ('b', 'n2')]))
    assert {s['run_id'] for s in collect_snapshots([], [tmp_path], now=110, max_age_seconds=30)} == {'a', 'b'}
    assert len(collect_snapshots([], [tmp_path], node='n1', now=110, max_age_seconds=30)) == 1
    assert not collect_snapshots([], [tmp_path], now=140, max_age_seconds=30)
    emit(tmp_path, 'later', 'n1', 150)
    finish(tmp_path / 'later', 0, 'ok', 'disabled')
    assert not collect_snapshots([], [tmp_path], now=160, max_age_seconds=30)
    # Legacy explicit directories remain readable, including completed runs.
    assert len(collect_snapshots([tmp_path / 'later/telemetry-metrics'], [])) == 1
    assert len(collect_snapshots([tmp_path / 'a/telemetry-metrics']*2, [])) == 1


def test_health_records_sidecar_loss_final_failure_and_workload_exit(tmp_path):
    process = subprocess.Popen(['sleep', '30'])
    try:
        initial = observe(tmp_path, {'bridge': process.pid}, now=100)
        assert initial['sidecars']['bridge']['status'] == 'pending'
        process.terminate(); process.wait()
        lost = observe(tmp_path, {'bridge': process.pid}, now=101)
        assert lost['issues'] == ['bridge:exited_during_workload']
        final = finish(tmp_path, 7, 'failed', 'disabled')
        assert final['workload']['exit_code'] == 7 and final['status'] == 'partial'
        assert 'bridge:final_export:failed' in final['issues']
        assert 'telemetry completeness' in summarize(tmp_path)
    finally:
        if process.poll() is None: process.kill(); process.wait()


def test_health_marks_backend_insufficient_data_partial(tmp_path):
    (tmp_path / 'diagnostics').mkdir()
    (tmp_path / 'diagnostics/latest.json').write_text(json.dumps({'verdict': 'insufficient_data', 'missing_sources':['clock:n:unknown']}))
    final = finish(tmp_path, 0, 'ok', 'ok')
    assert final['status'] == 'partial'
    assert final['diagnostics']['missing_sources'] == ['clock:n:unknown']


def record(stamp, tokens):
    return dict(run_id='r', node='n', worker_id='driver', boundary_scope='rl_step', observed_at=stamp,
                step_duration_seconds=10, analysis_window=dict(start=stamp-10,end=stamp,accuracy='approximate'),
                workload={'perf/total_num_tokens':tokens,'has_evaluation':False})


def test_baseline_filters_workload_and_missing_fields():
    current = record(100,1000)
    history = [record(50,100),record(70,950)]
    assert select_baseline(current,history) is not None
    policy={'match_fields':['perf/total_num_tokens','has_evaluation'],'relative_tolerance':.1}
    assert select_baseline(current,history,policy=policy)['observed_at']==70
    assert select_baseline(current,[record(50,100)],policy=policy) is None
    assert select_baseline(current,history,policy={'match_fields':['missing']}) is None
    with pytest.raises(ValueError): validate_baseline_policy({'relative_tolerance':float('nan')})
    with pytest.raises(ValueError): validate_baseline_policy({'normalize_by':'reward'})


def test_history_preserves_logger_workload_without_prometheus_labels(tmp_path):
    writer=StepHistoryWriter(tmp_path/'steps',run_id='r',node='n',worker_id='driver',clock=lambda:100)
    item=writer.append({'step':1,'data':{'perf/total_num_tokens':100,'timing_s/step':10,'timing_s/testing':1}})
    assert item['workload']['perf/total_num_tokens']==100
    assert item['workload']['has_evaluation'] is True


def test_sampling_distinguishes_evaluations_unknown_freshness_and_wide_rate():
    expression='rate(bytes{node="n"}[1m])'
    result=quality(expression,100,106,2,{'sample_count':4})
    assert result['range_window_seconds']==60
    assert result['evaluation_count']==4 and result['observed_source_samples'] is None
    assert result['freshness']=='unknown'
    assert 'range_window_exceeds_interval' in result['warnings']
    assert timestamp_query(expression)=='timestamp(bytes{node="n"})'
    assert timestamp_query('a{x="n"}/b{x="n"}') is None
    class Client:
        def query_range_detail(self,*args):
            assert args[0]=='timestamp(bytes{node="n"})'
            return {'series':[{'stats':{'max':104},'source_timestamps':[100,100,104]}]}
    observed=check_source(Client(),expression,100,106,2)
    assert observed['observed_source_samples']==2
    assert quality(expression,100,106,2,{},source=observed)['source_age_seconds']==2


def test_quality_and_workload_reach_grafana_and_llm():
    class Client:
        def query_range(self,*args): return {'mean':40,'max':40,'sample_count':2}
    report=DiagnosticEngine({'prometheus':{'url':'http://unused'},'baseline':{'normalize_by':'perf/total_num_tokens'}},
                            prometheus=Client()).analyze(record(100,1000),[record(70,950)])
    assert report['comparison']['workload_comparability']=='unverified'
    assert report['comparison']['normalization']['unit']=='seconds/token'
    assert report['sampling_quality']['disk_busy_ratio']['current']['range_window_seconds']==60
    rows=_investigation_rows(report)
    assert all(row['diagnosis_method']=='rule' for row in rows)
    assert any('quality_warnings' in row for row in rows if row['row_kind']=='comparison')
    packet=packet_from_report(report); validate_packet(packet)
    view=model_view(packet)
    assert view['baseline_comparability']=='unverified'
    assert 'sampling_quality' in view['observation_columns'] or 'sampling_quality' in view.get('common_observation_fields',{})


def test_device_events_preserve_major_minor_and_mapping(tmp_path):
    cgroup=tmp_path/'cgroup'; cgroup.mkdir()
    (cgroup/'io.stat').write_text('259:0 rbytes=100 wbytes=0 rios=1 wios=0\n259:1 rbytes=900 wbytes=0 rios=9 wios=0\n')
    assert parse_io_devices((cgroup/'io.stat').read_text())['259:1']['rbytes']==900
    recorder=SandboxRecorder(EventRecorder(tmp_path/'events',CorrelationContext('r','sandbox','sandbox','worker','n')),
                             runtime='docker',filesystem='overlayfs',deployment='dedicated',sandbox_node='n')
    before=time.time()
    with recorder.span('exec',cgroup=cgroup,device_major_minor='259:0'):
        (cgroup/'io.stat').write_text('259:0 rbytes=150 wbytes=0 rios=2 wios=0\n259:1 rbytes=1900 wbytes=0 rios=19 wios=0\n')
    mapping=device_window(tmp_path/'events','r','n',before,time.time(),'259:0')
    assert mapping['status']=='observed'
    assert mapping['observations'][0]['io_devices']['259:0']['rbytes']==50
    assert mapping['observations'][0]['io_devices']['259:1']['rbytes']==1000
    assert device_window(tmp_path/'events','r','n',before,time.time(),'8:0')['status']=='unmatched'
    assert not device_window(tmp_path/'events','other','n',before,time.time(),'259:0')['observations']


def test_selected_llm_report_revision_and_projection(tmp_path):
    folder=tmp_path/'diagnostics'; folder.mkdir()
    a={'trigger_record_id':'id','run_id':'r','node':'n','revision':1,'analysis_status':'provisional'}
    (folder/'diagnostics.jsonl').write_text(json.dumps(a)+'\n')
    with pytest.raises(ValueError,match='provisional'): selected_report(tmp_path,'id')
    b=a|{'revision':2,'analysis_status':'final'}
    with (folder/'diagnostics.jsonl').open('a') as stream: stream.write(json.dumps(b)+'\n')
    assert selected_report(tmp_path,'id')['revision']==2
    result={'record_type':'llm_diagnosis','semantic_review':{'decision':'accept'},'generated_at':'2026-09-30T00:00:00Z',
            'context':{'run_id':'r','node':'n','step':2,'trigger_record_id':'id'},'current_interval':{'start':10,'end':20,'accuracy':'exact'},
            'observation_packet':{'observations':[{'id':'m1','signal':'gpu','current':40,'baseline':90,'observation_scope':'device'}]},
            'diagnosis':{'assessment':'bottleneck_suspected','summary':'관측 구간을 조사합니다.','candidates':[
                {'title':'Possible GPU wait','explanation':'GPU utilization이 감소했습니다.','evidence_ids':['m1'],
                 'counter_evidence_ids':[],'missing_evidence':['process attribution'],'observation_scope':'device'}]}}
    projected=project_result(tmp_path,result)
    rows=[json.loads(line) for line in projected.read_text().splitlines()]
    assert rows[0]['diagnosis_method']=='llm' and rows[0]['strong_candidate_count']==0
    assert rows[1]['state']=='hypothesis'
    assert rows[2]['observation_id']=='m1'
    with pytest.raises(ValueError): project_result(tmp_path,result|{'record_type':'llm_diagnosis_failure'})


def test_wrapper_detects_sidecar_loss_and_recovers_final_export(tmp_path):
    workload=tmp_path/'workload.py'
    workload.write_text('''import json, os, signal, time
from pathlib import Path
root=Path(os.environ['TELEMETRY_METRICS_DIR']).parent
health=json.loads((root/'telemetry-health.json').read_text())
os.kill(health['sidecars']['bridge']['pid'], signal.SIGKILL)
time.sleep(2.5)
Path(os.environ['VERL_FILE_LOGGER_PATH']).write_text(json.dumps({'step':1,'data':{'timing_s/step':1}})+'\\n')
''')
    output=tmp_path/'run'
    result=subprocess.run(['bash',str(ROOT/'scripts/run_verl_with_telemetry.sh'),'--output',str(output),
                           '--',sys.executable,str(workload)],env=os.environ|{'TELEMETRY_PYTHON':sys.executable},
                          text=True,capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr
    health=json.loads((output/'telemetry-health.json').read_text())
    assert health['status']=='partial' and health['final_export']['bridge']=='ok'
    assert 'bridge:exited_during_workload' in health['issues']


def test_bad_quality_cannot_enter_model_packet():
    from xlayer_telemetry.analysis.llm_diagnosis import validate_packet
    packet={'schema_version':1,'record_type':'llm_observation_packet','observations':[
        {'id':'m1','signal':'cpu','current':1,'observation_scope':'node',
         'sampling_quality':{'current':{'range_window_seconds':float('nan')}}}]}
    with pytest.raises(ValueError,match='quality'): validate_packet(packet)


def test_sampling_query_failure_stays_unknown():
    class Broken:
        def query_range_detail(self,*_args): raise TimeoutError('temporary source outage')
    assert check_source(Broken(),'metric{node="n"}',0,10,2)=={}


def test_compound_and_unknown_range_windows():
    assert quality('rate(bytes{x="a"}[1h30m])',0,10,2,{})['range_window_seconds']==5400
    assert quality('rate(bytes{x="a"}[$interval])',0,10,2,{})['range_window_seconds'] is None


def test_health_does_not_hide_unresolved_earlier_steps(tmp_path):
    (tmp_path/'diagnostics').mkdir()
    (tmp_path/'telemetry-events').mkdir()
    (tmp_path/'telemetry-events/verl-steps.jsonl').write_text('{"record_id":"a"}\n{"record_id":"b"}\n')
    report={'trigger_record_id':'b','analysis_status':'final','verdict':'no_anomaly_observed'}
    (tmp_path/'diagnostics/latest.json').write_text(json.dumps(report))
    (tmp_path/'diagnostics/diagnostics.jsonl').write_text(json.dumps(report)+'\n')
    value=finish(tmp_path,0,'ok','ok')
    assert value['status']=='partial'
    assert value['diagnostics']['unresolved_record_ids']==['a']
