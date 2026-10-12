"""Unsupported or unknown live Grafana cannot certify XLayer readiness."""
from argparse import Namespace
import json

import pytest

from xlayer_telemetry.grafana_release import release
from xlayer_telemetry.operations import app, health
from xlayer_telemetry.operations.config import load_config
from xlayer_telemetry import stack


@pytest.mark.parametrize('version', ['12.1.0', '13.0.0', '13.1.6', '13.1.8', '13.2.0', '14.0.0', '13.1.7-rc1'])
def test_only_pinned_release_and_runtime_are_supported(version):
    with pytest.raises(ValueError, match='13.1.7'):
        release(version)
    assert not app.grafana_compatible(version, '>=12.1.0')


@pytest.mark.parametrize('version', ['12.1.0', '13.1.8', None, '13.1.7'])
def test_live_server_status_verifies_actual_version_even_when_database_is_ok(tmp_path, monkeypatch, version):
    config_file=tmp_path/'config.toml'
    config_file.write_text('[telemetry]\nNODE_NAME="n"\nNODE_ADDR="127.0.0.1"\nTELEMETRY_TARGETS="n=127.0.0.1"\nENABLE_GPU_METRICS=false\n')
    config,_=load_config(config_file)
    monkeypatch.setattr(health,'process_identity',lambda _: {'process':'running'})
    targets=[{'labels':{'job':'telemetry','cluster':config['CLUSTER_NAME'],'nodename':'n'},
              'health':'up','scrapeUrl':'http://127.0.0.1:19100/metrics'}]
    def probe(url, **kwargs):
        data={'database':'ok','version':version} if url.endswith('/api/health') else {'status':'success','data':{'activeTargets':targets}}
        return {'health':'healthy','data':data}
    monkeypatch.setattr(health,'probe',probe)
    result=health.status(config,role='server')
    assert result['status']==('healthy' if version=='13.1.7' else 'degraded')


@pytest.mark.parametrize('version', ['12.1.0', '13.1.8', None, '13.1.7'])
def test_stack_startup_never_reports_ready_for_wrong_or_unknown_grafana(tmp_path, monkeypatch, version):
    monkeypatch.setattr(stack,'_wait_until_ready',lambda *_: None)
    monkeypatch.setattr(stack,'_read_json',lambda url: {'database':'ok','version':version}
        if url.endswith('/api/health') else {'status':'success','data':{'activeTargets':[]}})
    args=Namespace(target_dir=None,prometheus_url='http://prom',grafana_url='http://grafana',
                   output=tmp_path/'summary.json',timeout=1,require_targets_up=False,loki_url=None)
    code=stack.validate_stack(args)
    assert code==(0 if version=='13.1.7' else 1)
    summary=json.loads(args.output.read_text())['validation']
    assert summary['grafana_version_ok']==(version=='13.1.7')


@pytest.mark.parametrize('version', ['12.1.0', None, '13.1.7'])
def test_idempotent_up_still_checks_observed_server_version(tmp_path, monkeypatch, version):
    from xlayer_telemetry import cli
    from xlayer_telemetry.operations.config import ConfigError
    path=tmp_path/'config.toml';path.write_text('[telemetry]\n')
    config,_=load_config(path)
    marker=tmp_path/'existing-server-state';marker.write_text('preserve')
    class Child:
        def wait(self):return 0
    monkeypatch.setattr(cli.subprocess,'Popen',lambda *args,**kwargs:Child())
    monkeypatch.setattr(cli,'snapshot',lambda _:tmp_path/'snapshot.conf')
    monkeypatch.setattr(health,'probe',lambda *args,**kwargs:{'health':'healthy','data':{'version':version}})
    if version=='13.1.7':assert cli._launch(config,'up','server')==0
    else:
        with pytest.raises(ConfigError,match='13.1.7'):cli._launch(config,'up','server')
    assert marker.read_text()=='preserve'


def test_direct_server_legacy_version_is_rejected_before_replacing_config(tmp_path):
    import os, subprocess, sys
    output=tmp_path/'server';output.mkdir()
    config=output/'prometheus.yml';config.write_text('existing')
    root=__import__('pathlib').Path(__file__).parents[1]
    result=subprocess.run(['bash',str(root/'scripts/run_telemetry.sh'),'server'],
        env=os.environ|{'PYTHON':sys.executable,'SERVER_CONFIG_ONLY':'1','OUTPUT_DIR':str(output),
                       'GRAFANA_VERSION':'12.1.0','TELEMETRY_TARGETS':'n=127.0.0.1'},capture_output=True,text=True,timeout=5)
    assert result.returncode==2 and '13.1.7' in result.stderr
    assert config.read_text()=='existing'
