import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import re

import pytest

ROOT=Path(__file__).parents[1]


def calculator(monkeypatch):
    function=types.ModuleType('verl.tools.function_tool');function.function_tool=lambda _:lambda fn:fn
    for name in ('verl','verl.tools'):monkeypatch.setitem(sys.modules,name,types.ModuleType(name))
    monkeypatch.setitem(sys.modules,'verl.tools.function_tool',function)
    spec=importlib.util.spec_from_file_location('calculator_fixture',ROOT/'examples/sandbox/calculator_tools.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('has_cid',[True,False])
def test_calculator_timeout_cleans_only_owned_container(monkeypatch,has_cid):
    module=calculator(monkeypatch);calls=[]
    def run(argv,**kwargs):
        calls.append(argv)
        if argv[1]=='run':
            if has_cid:Path(argv[argv.index('--cidfile')+1]).write_text('a'*64)
            raise subprocess.TimeoutExpired(argv,kwargs['timeout'])
        return subprocess.CompletedProcess(argv,0,'','')
    monkeypatch.setattr(module.subprocess,'run',run)
    with pytest.raises(subprocess.TimeoutExpired):module._execute_container(['docker','run','--rm','image','command'])
    name=calls[0][calls[0].index('--name')+1]
    assert name.startswith('xltel-calculator-')
    assert calls[1]==['docker','rm','--force','a'*64 if has_cid else name]


def test_ollama_pid_write_failure_cleans_new_process_without_pidfile(tmp_path):
    home=tmp_path/'telemetry';runtime=home/'tools/ollama-v0.34.4';runtime.mkdir(parents=True)
    marker=tmp_path/'spawned.pid'
    executable=runtime/'bin/ollama';executable.parent.mkdir()
    executable.write_text('#!/usr/bin/env bash\nprintf "%s" "$$" > "$TEST_SPAWNED_PID"\nexec sleep 60\n');executable.chmod(0o755)
    state=home/'state/local-llm';state.mkdir(parents=True);(state/'server.pid').mkdir()
    result=subprocess.run(['bash','-x',str(ROOT/'scripts/local_llm.sh'),'up'],
        env=os.environ|{'TELEMETRY_HOME':str(home),'LLM_GPU':'test-fixture','LLM_PORT':'24591','TEST_SPAWNED_PID':str(marker)},
        capture_output=True,text=True,timeout=15)
    assert result.returncode!=0 and (state/'server.pid').is_dir()
    match=re.search(r'\+ launched_pid=([0-9]+)',result.stderr)
    assert match,result.stderr
    pid=int(match.group(1))
    try:assert Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[0]=='Z'
    except (FileNotFoundError,ProcessLookupError):pass


def test_native_file_sd_yaml_round_trips_special_character_output_path(tmp_path):
    output=tmp_path/"server's quoted # path"
    source=tmp_path/'sources.json';source.write_text(json.dumps({'schema_version':1,'sources':[
        {'name':'vllm','kind':'vllm','target':'127.0.0.1:8000','labels':{'node':'local'}}]}))
    result=subprocess.run(['bash',str(ROOT/'scripts/run_telemetry.sh'),'server'],
        env=os.environ|{'OUTPUT_DIR':str(output),'SERVER_CONFIG_ONLY':'1','ENABLE_LOGS':'0','ENABLE_ALERTS':'0',
            'PYTHON':sys.executable,'TELEMETRY_TARGETS':'local=127.0.0.1','TELEMETRY_SOURCES_FILE':str(source)},
        capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    text=(output/'prometheus.yml').read_text()
    scalar=next(line for line in text.splitlines() if 'native-targets.json' in line).split('- ',1)[1]
    assert json.loads(scalar)==str(output/'native-targets.json')
    provision=(output/'provisioning/dashboards/default.yaml')
    files=list((output/'provisioning/dashboards').glob('*.yaml'))
    content=files[0].read_text()
    path_value=next(line for line in content.splitlines() if line.strip().startswith('path:')).split('path:',1)[1].strip()
    assert json.loads(path_value)==str(output/'dashboards')
    if os.environ.get('PROMTOOL'):
        checked=subprocess.run([os.environ['PROMTOOL'],'check','config',str(output/'prometheus.yml')],capture_output=True,text=True)
        assert checked.returncode==0,checked.stdout+checked.stderr
