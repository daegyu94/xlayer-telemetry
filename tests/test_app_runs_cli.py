import json
from pathlib import Path
import subprocess
import sys
from xlayer_telemetry import cli
from xlayer_telemetry.operations import app
from tests.test_app_operations import bundle
from tests.test_run_explorer import saved


def configuration(tmp_path):
    path=tmp_path/'config.toml'
    path.write_text('[telemetry]\nENABLE_GPU_METRICS=false\nTELEMETRY_HOME='+json.dumps(str(tmp_path/'state'))+'\nTELEMETRY_RUNS_ROOT='+json.dumps(str(tmp_path/'runs'))+'\n')
    return path


def test_cli_install_status_update_preserve_state_and_do_not_restart_without_flag(tmp_path,monkeypatch,capsys):
    config=configuration(tmp_path);path,digest=bundle(tmp_path)
    monkeypatch.setattr(app,'api',lambda *args:None)
    monkeypatch.setattr(cli,'_launch',lambda *args: (_ for _ in ()).throw(AssertionError('unexpected restart')))
    assert cli.main(['--config',str(config),'app','install','--package',str(path),'--sha256',digest,'--allow-unsigned','--json'])==0
    assert json.loads(capsys.readouterr().out)['status']=='restart_required'
    assert cli.main(['--config',str(config),'app','status','--json'])==1
    assert json.loads(capsys.readouterr().out)['status']=='needs_attention'
    assert cli.main(['--config',str(config),'app','update','--package',str(path),'--sha256',digest,'--allow-unsigned','--restart','--json'])==0
    assert json.loads(capsys.readouterr().out)['changed'] is False
    assert cli.main(['--config',str(config),'app','install','--external','--restart','--package',str(path),'--sha256',digest,'--json'])==2


def test_runs_search_time_filter_comparison_and_publication_use_actual_artifacts(tmp_path,capsys):
    config=configuration(tmp_path)
    saved(tmp_path/'runs','a');saved(tmp_path/'runs','b',15)
    assert cli.main(['--config',str(config),'runs','list','--search','a','--json'])==0
    assert [row['run_id'] for row in json.loads(capsys.readouterr().out)['runs']]==['a']
    assert cli.main(['--config',str(config),'runs','compare','a','b','--json'])==0
    assert json.loads(capsys.readouterr().out)['metrics'][0]['delta_percent']==50
    assert cli.main(['--config',str(config),'runs','list','--after','2030-01-01T00:00:00Z','--json'])==0
    assert json.loads(capsys.readouterr().out)['runs']==[]
    assert cli.main(['--config',str(config),'runs','publish','--json'])==0
    assert json.loads(capsys.readouterr().out)['run_count']==2


def test_run_metadata_flags_preserve_workload_argv_and_keep_model_out_of_metric_labels(tmp_path):
    config=configuration(tmp_path)
    command=[sys.executable,'-m','xlayer_telemetry.cli','--config',str(config),'run','--run-id','metadata',
        '--model','Qwen3','--workload-fingerprint','batch64-context2k-v1','--',sys.executable,'-c','import sys; assert sys.argv[1:] == ["literal;argument"]','literal;argument']
    result=subprocess.run(command,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    manifest=json.loads((tmp_path/'runs/metadata/telemetry-manifest.json').read_text())
    assert manifest['configuration']['model_identifier']=='Qwen3'
    assert manifest['configuration']['workload_fingerprint']=='batch64-context2k-v1'
