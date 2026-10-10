"""Failure isolation, optional inputs and owned resources from the second audit."""
import io
import json
from pathlib import Path
import tarfile
import sys

import pytest

from xlayer_telemetry.metrics import textfile
from xlayer_telemetry.collectors.topology_textfile import build_gauges
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, load_config
from xlayer_telemetry.operations import app
from tests.test_app_operations import bundle, config
from tests.test_app_metrics_textfile import SNAPSHOT
from tests.test_rollout_replicas import Replicas, records


def test_local_default_target_is_used_by_cluster_admission(tmp_path):
    from xlayer_telemetry.operations.config import defaults
    from xlayer_telemetry.operations.cluster import configuration_report
    value=defaults();value.update(NODE_NAME='local',NODE_ADDR='127.0.0.1',TELEMETRY_TARGETS='',TOPOLOGY_DIR=str(tmp_path))
    (tmp_path/'compute-topology.json').write_text(json.dumps({'components':[{'id':'local','role':'gpu-node','resource_node':'local'}]}))
    result=configuration_report(value,declared_nodes={'local'})
    assert not any(row['code'] in {'configured_node_not_monitored','resource_node_unregistered'} for row in result['issues'])


def test_remote_span_cannot_support_local_cpu_candidate():
    from tests.test_behavior_signature import boundary, span, observation
    from xlayer_telemetry.analysis.behavior_signature import summarize, compare, candidates
    before,now=boundary(step=0),boundary(step=1)
    previous=summarize(before,[span(before,phase='cpu',attributes={'node':'remote'},duration=.1)],
        [observation('host_cpu_pressure_ratio',.1)])
    current=summarize(now,[span(now,phase='cpu',attributes={'node':'remote'},duration=1)],
        [observation('host_cpu_pressure_ratio',.9)])
    row=next(r for r in candidates(current,compare(current,[previous])) if r['candidate']=='cpu_contention')
    assert row['state']!='supported_candidate'


def test_switching_demo_run_does_not_relabel_previous_completed_frame(tmp_path,monkeypatch):
    from tests.test_phase_demo_scenario import scenario_file,ROOT
    from xlayer_telemetry.demos.live import Demo
    from xlayer_telemetry.demos.scenario import make_scenario
    import xlayer_telemetry.demos.live as live
    path,schedule=scenario_file(tmp_path)
    monkeypatch.setattr(live.time,'time',lambda:1200)
    demo=Demo(ROOT/'examples/live-demo',scenario_state=path)
    demo.metrics('gpu-node-0');assert demo.completed_frame is not None
    replacement=make_scenario(start=1300,run_id='other',node='gpu-node-0',step=10)
    path.write_text(json.dumps(replacement))
    rows=demo.metrics('gpu-node-0')
    assert demo.completed_frame is None
    assert not any(s.name=='training_step' and s.value==128 and s.labels.get('run_id')=='other' for s in rows)


@pytest.mark.parametrize('cached',[False,True])
def test_bad_json_files_do_not_stop_normal_application_collection(tmp_path,cached):
    depth=max(20000,sys.getrecursionlimit()+100)
    (tmp_path/'a-deep.json').write_text('['*depth+'0'+']'*depth)
    (tmp_path/'b-large-integer.json').write_text('{"integer":'+'9'*5000+'}')
    (tmp_path/'c-normal.json').write_text(json.dumps(SNAPSHOT))
    counters={}
    values=textfile._iter_snapshots(tmp_path,counters=counters,cache=textfile.SnapshotCache() if cached else None)
    assert values==[SNAPSHOT] and counters['snapshot_rejections']>=1


@pytest.mark.parametrize('source',['threefs','sandbox'])
def test_validated_null_optional_source_reaches_actual_analysis(tmp_path,source):
    path=tmp_path/'diagnosis.json'
    path.write_text(json.dumps({'schema_version':1,'prometheus':{'url':'http://unused'},source:None}))
    settings=load_config(path)
    now,before=records()
    result=DiagnosticEngine(settings,prometheus=Replicas()).analyze(now,[before])
    assert result['record_type']=='bottleneck_diagnosis'


@pytest.mark.parametrize('changed',['no_receipt','modified'])
def test_update_checks_existing_rollback_integrity_before_mutating(tmp_path,monkeypatch,changed):
    monkeypatch.setattr(app,'api',lambda *_:None)
    settings=config(tmp_path);first,digest=bundle(tmp_path)
    app.install_package(settings,first,digest,allow_unsigned=True)
    target=tmp_path/'server/grafana-plugins/xlayer-telemetry-app/module.js'
    # Use the installation's actual receipt directory rather than a fixture.
    _,_,state=app._paths(settings)
    if changed=='no_receipt':(state/'installed.json').unlink()
    else:target.write_text('user modified plugin')
    before=target.read_bytes()
    second,digest=bundle(tmp_path,'two')
    with pytest.raises(app.AppError,match='rollback'):
        app.install_package(settings,second,digest,allow_unsigned=True,update=True)
    assert target.read_bytes()==before


@pytest.mark.parametrize('bad',['x'*129,'bad\ud800'])
def test_invalid_topology_labels_are_isolated_from_normal_samples(tmp_path,bad):
    payload={'components':[{'id':'good','role':'gpu-node'},{'id':'bad','role':bad}],
        'edges':[{'source':'good','destination':'good','relation':'attached'},
                 {'source':'good','destination':'bad','relation':bad}]}
    (tmp_path/'compute-topology.json').write_text(json.dumps(payload))
    rows=build_gauges(tmp_path)
    assert len(rows)==2
    assert all(bad not in row.labels.values() for row in rows)


def test_vm_archive_contains_exporter_bytes_instead_of_host_symlink(tmp_path):
    from examples.multinode.validate_vms import payload
    binary=tmp_path/'binary';binary.write_bytes(b'exporter fixture')
    link=tmp_path/'node_exporter';link.symlink_to(binary)
    with tarfile.open(fileobj=io.BytesIO(payload(link)),mode='r:gz') as archive:
        member=archive.getmember('tools/node_exporter-1.9.1.linux-amd64/node_exporter')
        assert member.isfile() and archive.extractfile(member).read()==b'exporter fixture'
