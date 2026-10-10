"""Actual producer/consumer and ownership boundaries from the operations audit."""
import json
import os
from pathlib import Path
import signal
import sys
import time

import pytest

from xlayer_telemetry import cli
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine
from xlayer_telemetry.analysis.llm_investigation import project_result
from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.operations import app, runs
from xlayer_telemetry.sandbox import SandboxRecorder, device_window
from tests.test_rollout_replicas import Replicas, records, stats
from tests.test_llm_evidence_semantics import report, answer
from xlayer_telemetry.analysis.llm_diagnosis import packet_from_report


def test_frontend_worker_fixture_matches_real_adapter_labels():
    from xlayer_telemetry.adapters.verl import VerlMetricsAdapter
    fixture=json.loads((Path(__file__).parents[1]/'grafana/xlayer-app/tests/fixtures/verl-worker-labels.json').read_text())
    assert fixture['metric_labels']=={s.name:dict(s.labels) for s in VerlMetricsAdapter.translate(fixture['logger'])}


@pytest.mark.parametrize('replacement', [False, True])
def test_default_disk_baseline_uses_selected_device_not_previous_maximum(replacement):
    class Disks(Replicas):
        def query_range_detail(self, query, start, end, step):
            if 'node_disk_io_time_seconds_total' not in query:
                return super().query_range_detail(query,start,end,step) if 'node_time' in query or 'node_timex' in query else {'aggregate':None,'series':[]}
            current=start==100
            values={'sdb':.95,'sda':.1} if current else {'sda':.2,**({} if replacement else {'sdb':.05})}
            rows=[{'labels':{'cluster':'lab','node':'trainer','instance':'trainer','device':device},
                   'stats':stats(end-1 if query.startswith('timestamp(') else value),
                   **({'source_timestamps':[start+2,end-1]} if query.startswith('timestamp(') else {})}
                  for device,value in values.items()]
            return {'aggregate':stats(max(values.values())),'series':rows}
    now,before=records()
    result=DiagnosticEngine({'cluster':'lab','clock':{'monitoring_node':'monitor'},'prometheus':{'url':'unused'},
        'sampling':{'check_source_freshness':True},'baseline':{'match_fields':['perf/total_num_tokens']}},
        prometheus=Disks()).analyze(now,[before])
    row=next(r for r in result['comparison']['signals'] if r['signal']=='disk_busy_ratio')
    assert row['current']==.95 and row['labels']['device']=='sdb'
    assert row['baseline']==(None if replacement else .05)
    assert row['delta']==(None if replacement else pytest.approx(.9))


def test_legacy_findings_preserve_missing_values_and_real_zero():
    engine=DiagnosticEngine({'prometheus':{'url':'unused'}})
    finding=engine._findings({'vllm_requests_waiting':{'max':18}},[],[],1,20,'sync')[0]
    assert finding['signals']['kv_usage_max_ratio'] is None
    assert finding['signals']['preemptions_delta'] is None
    finding=engine._findings({'disk_busy_ratio':{'max':.95}},[],[],1,20,'sync')[0]
    assert finding['signals']['memory_available_min_ratio'] is None
    assert finding['signals']['gpu_utilization_mean_percent'] is None
    finding=engine._findings({'vllm_requests_waiting':{'max':18},'vllm_kv_cache_usage':{'max':0}},[],[],1,20,'sync')[0]
    assert finding['signals']['kv_usage_max_ratio']==0


def test_two_models_project_distinct_invocations_on_every_row(tmp_path):
    packet=packet_from_report(report())
    result={'record_type':'llm_diagnosis','generated_at':'2026-10-10T00:00:00Z','model':'first',
        'semantic_review':{'decision':'accept'},'context':{'run_id':'r','node':'n','trigger_record_id':'step-2'},
        'current_interval':{'start':10,'end':20},'observation_packet':packet,'diagnosis':answer()}
    outputs=[]
    for model in ('first','second'):
        outputs.append([json.loads(line) for line in project_result(tmp_path,{**result,'model':model}).read_text().splitlines()])
    ids=[{row.get('diagnosis_invocation_id') for row in rows} for rows in outputs]
    assert all(len(values)==1 and None not in values for values in ids)
    assert ids[0]!=ids[1]


@pytest.mark.parametrize('size', [1500000,3900000])
def test_owned_large_catalog_can_be_published_twice(tmp_path,monkeypatch,size):
    data={'schema_version':1,'runs':[],'truncated':False,'fixture_padding':'x'*size}
    monkeypatch.setattr(runs,'catalog',lambda _:data)
    runs.publish(tmp_path,tmp_path/'dashboards')
    runs.publish(tmp_path,tmp_path/'dashboards')
    assert json.loads((tmp_path/'dashboards/xlayer-run-catalog.json').read_text())['uid']==runs.CATALOG_UID


def test_custom_producer_and_first_seen_device_remain_observable(tmp_path):
    cgroup=tmp_path/'cgroup';cgroup.mkdir()
    (cgroup/'io.stat').write_text('')
    events=EventRecorder(tmp_path/'events',CorrelationContext(run_id='r',node='worker',producer='custom_tools',role='rollout',worker_id='0'))
    recorder=SandboxRecorder(events,runtime='docker',filesystem='overlayfs',deployment='colocated',sandbox_node='worker')
    with recorder.span('exec',cgroup=cgroup,device_major_minor='259:0'):
        (cgroup/'io.stat').write_text('259:0 rbytes=32 wbytes=0 rios=1 wios=0\n')
    rows=[json.loads(line) for path in (tmp_path/'events').glob('*.jsonl') for line in path.read_text().splitlines()]
    resource=next(row for row in rows if row['name']=='sandbox.resource_sample')
    assert resource['attributes']['device_mapping']['observed_major_minors']==['259:0']
    assert resource['attributes']['io_devices']['259:0']=={}  # No pre-operation counter, no invented delta.
    result=device_window(tmp_path/'events','r','worker',0,time.time()+1,'259:0')
    assert result['status']=='observed' and result['observation_count']==1
    assert device_window(tmp_path/'events','other','worker',0,time.time()+1)['observation_count']==0


def test_custom_event_filename_uses_event_identity_and_interval_not_prefix(tmp_path):
    recorder=EventRecorder(tmp_path,CorrelationContext(run_id='r',node='worker',producer='custom_tools',role='rollout',worker_id='0'),clock_ns=lambda:int(15e9))
    recorder.event('sandbox.resource_sample',phase='environment',attributes={'sandbox_node':'worker','io_devices':{'259:0':{'rbytes':0}}})
    assert device_window(tmp_path,'r','worker',10,20,'259:0')['status']=='observed'
    assert device_window(tmp_path,'r','other',10,20,'259:0')['observation_count']==0
    assert device_window(tmp_path,'r','worker',20,30,'259:0')['observation_count']==0


def test_device_discovery_read_limit_is_unknown_instead_of_unmatched(tmp_path):
    (tmp_path/'custom.jsonl').write_text('{}\n')
    def many(_):
        for _ in range(8193):yield {}
    value=device_window(tmp_path,'r','worker',10,20,'259:0',reader=many)
    assert value['status']=='unknown' and value['read_incomplete'] and value['truncated']


@pytest.mark.parametrize('run_id', ['.','..'])
def test_reserved_run_ids_never_launch_or_write_run_pointer(tmp_path,monkeypatch,run_id):
    config=tmp_path/'config.conf';config.write_text(f'TELEMETRY_HOME={tmp_path}/telemetry\n')
    calls=[];monkeypatch.setattr(cli.os,'execvpe',lambda *args:calls.append(args))
    assert cli.main(['--config',str(config),'run','--run-id',run_id,'--',sys.executable,'-c','pass'])==2
    assert not calls and not (tmp_path/'telemetry/state/last-cli-run.json').exists()


def test_automatic_output_cannot_follow_existing_symlink_outside_runs_root(tmp_path,monkeypatch):
    base=tmp_path/'telemetry/runs';base.mkdir(parents=True)
    (base/'escaped').symlink_to(tmp_path/'outside',target_is_directory=True)
    config=tmp_path/'config.conf';config.write_text(f'TELEMETRY_HOME={tmp_path}/telemetry\n')
    calls=[];monkeypatch.setattr(cli.os,'execvpe',lambda *args:calls.append(args))
    assert cli.main(['--config',str(config),'run','--run-id','escaped','--',sys.executable,'-c','pass'])==2
    assert not calls


@pytest.mark.parametrize('parent_exits',[False,True])
def test_package_timeout_is_actionable_and_cleans_owned_compiler_child(tmp_path,parent_exits):
    script=tmp_path/'launcher.py';pidfile=tmp_path/'child.pid'
    script.write_text("import subprocess,sys,time\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
        "Path(sys.argv[1]).write_text(str(child.pid))\n"+('' if parent_exits else 'time.sleep(60)\n'))
    try:
        with pytest.raises(app.AppError,match='timed out'):
            app._command([sys.executable,str(script),str(pidfile)],timeout=.5)
        assert pidfile.exists()
        pid=int(pidfile.read_text())
        def active():
            try:return Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[0]!='Z'
            except (FileNotFoundError,ProcessLookupError):return False
        deadline=time.monotonic()+2
        while active() and time.monotonic()<deadline:time.sleep(.01)
        assert not active()
    finally:
        if pidfile.exists():
            try:os.kill(int(pidfile.read_text()),signal.SIGKILL)
            except ProcessLookupError:pass
