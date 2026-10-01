import ast
from contextvars import ContextVar
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from xlayer_telemetry.events import EventRecorder
from xlayer_telemetry.sandbox import SandboxRecorder
from xlayer_telemetry.metrics.textfile import collect_snapshots, build_metrics


def test_collector_counts_repeated_invalid_input_and_preserves_good_worker(tmp_path):
    good = {'schema_version':2,'run_id':'r','producer':'a','role':'worker','worker_id':'0','node':'n',
            'samples':[{'name':'good','value':2},{'name':'bad','value':None}]}
    (tmp_path/'good.json').write_text(json.dumps(good))
    (tmp_path/'bad.json').write_bytes(b'\xff')
    (tmp_path/'invalid.json').write_text('[]')
    counts = {}
    for _ in range(2):
        rows = build_metrics(collect_snapshots([tmp_path],[],counters=counts),counters=counts)
        assert any(row.name=='good' for row in rows)
    assert counts == {'snapshot_reads':6,'snapshot_rejections':4,'sample_rejections':2}
    result = subprocess.run([sys.executable,'-m','xlayer_telemetry.metrics.textfile',
        '--metrics-dir',str(tmp_path),'--textfile-dir',str(tmp_path/'textfile'),'--once'],
        capture_output=True,text=True,timeout=5)
    assert result.returncode == 0, result.stderr
    text=(tmp_path/'textfile/application.prom').read_text()
    assert 'telemetry_application_snapshot_rejections_total 2' in text
    assert 'telemetry_application_sample_rejections_total 1' in text
    assert '# TYPE telemetry_application_snapshot_reads_total counter' in text


@pytest.mark.parametrize('case,expected', [('ok','completed'),('nonzero','nonzero_exit'),
    ('check','nonzero_exit'),('timeout','timeout'),('launch','launch_error')])
def test_docker_adapter_emits_result_without_changing_subprocess_behavior(tmp_path,monkeypatch,case,expected):
    # Compile the actual adapter class without requiring a VERL installation.
    source=Path('examples/sandbox/verl_lab_swebench_tools.py').read_text()
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='_ObservedSubprocess')
    monkeypatch.setenv('TELEMETRY_EVENTS_DIR',str(tmp_path))
    monkeypatch.setenv('TELEMETRY_RUN_ID','r')
    monkeypatch.setenv('TELEMETRY_NODE','n')
    monkeypatch.delenv('XLAYER_SANDBOX_CGROUP_PARENT',raising=False)
    def run(*args,**kwargs):
        if case=='timeout':raise subprocess.TimeoutExpired('docker',1)
        if case=='launch':raise FileNotFoundError('docker')
        if case=='check':raise subprocess.CalledProcessError(2,'docker')
        return subprocess.CompletedProcess('docker',2 if case=='nonzero' else 0)
    namespace={'os':os,'EventRecorder':EventRecorder,'SandboxRecorder':SandboxRecorder,
        '_ACTIVE_TOOL':ContextVar('tool',default=None),'_ACTIVE_TOOL_NAME':ContextVar('name',default=None),
        'subprocess':SimpleNamespace(run=run,TimeoutExpired=subprocess.TimeoutExpired,CalledProcessError=subprocess.CalledProcessError)}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'adapter','exec'),namespace)
    adapter=namespace['_ObservedSubprocess']()
    if case in {'timeout','launch','check'}:
        with pytest.raises((subprocess.TimeoutExpired,subprocess.CalledProcessError,OSError)):
            adapter.run(['docker','run','image'])
    else:
        assert adapter.run(['docker','run','image']).returncode == (2 if case=='nonzero' else 0)
    events=[json.loads(line) for path in tmp_path.glob('*.jsonl') for line in path.read_text().splitlines()]
    result=next(row for row in events if row['name']=='sandbox.exec_result')
    span=next(row for row in events if row['name']=='sandbox.exec')
    assert result['attributes']['outcome']==expected
    assert result['trace_id']==span['trace_id'] and result['span_id']==span['span_id']
    assert span['status']==('error' if case in {'timeout','launch','check'} else 'ok')
    assert 'stdout' not in result['attributes'] and 'stderr' not in result['attributes']


def test_producer_cannot_override_collector_health():
    counters={}
    result=build_metrics([{'samples':[{'name':'telemetry_application_snapshot_reads_total','value':999}]}],counters=counters)
    assert not result
    assert counters['sample_rejections']==1
