"""CPU regressions for the real Agent RL validation entry points."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize('mode,boundary', [('sync', 'rl_step'),
    ('colocate_async', 'trainer_update'), ('separate_async', 'trainer_update')])
def test_smoke_preserves_mode_and_uses_collectable_paths(tmp_path, mode, boundary):
    lab = tmp_path / 'lab'
    python = lab / 'third_party/verl/.venv/bin/python'
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\nexit 0\n')  # Dataset preparation is tested separately.
    python.chmod(0o755)
    for relative in ('artifacts/verl-eval/swebench-agent-train.parquet',
                     'results/swebench-agent-rl/base/network/check.py'):
        path = lab / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    runner = lab / 'scripts/run_verl_v1_benchmark.sh'
    runner.parent.mkdir()
    runner.write_text('''#!/bin/bash
set -eu
printf '%s\\n' "$1" > "$MODE_FILE"
printf '%s\\n' 'actual trainer log' > "$3"
printf '%s\\n' '{"step":1,"data":{"timing_s/step":1,"timing_s/gen":0.5}}' > "$VERL_FILE_LOGGER_PATH"
printf '%s\\n' "$CUDA_VISIBLE_DEVICES" > "$VISIBLE_FILE"
''')
    docker = tmp_path / 'docker'
    docker.write_text('#!/bin/sh\nexit 0\n')
    docker.chmod(0o755)
    run = tmp_path / 'runs/case'
    env = os.environ | {'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
        'VERL_LAB_ROOT': str(lab), 'MODEL_PATH': 'cached-model', 'RUN_ROOT': str(run),
        'TRAINER_MODE': mode, 'TOTAL_TRAINING_STEPS': '3', 'RUN_ID': 'test-smoke',
        'TELEMETRY_PYTHON': sys.executable, 'TELEMETRY_NODE': 'test-node',
        'MODE_FILE': str(tmp_path / 'mode'), 'VISIBLE_FILE': str(tmp_path / 'visible')}
    env.pop('CUDA_VISIBLE_DEVICES', None)
    env.pop('DIAGNOSTICS_CONFIG', None)
    result = subprocess.run(['bash', str(ROOT / 'examples/sandbox/run_verl_lab_smoke.sh')],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'mode').read_text().strip() == mode
    assert (tmp_path / 'visible').read_text().strip() == ('0,1' if mode == 'separate_async' else '0')
    assert (run / 'logs/training.log').read_text().strip() == 'actual trainer log'
    records = [json.loads(line) for line in
               (run / 'telemetry/telemetry-events/verl-steps.jsonl').read_text().splitlines()]
    assert len(records) == 1 and records[0]['boundary_scope'] == boundary
    assert not (run / 'run').exists()


@pytest.mark.parametrize('layout', ['telemetry', 'run'])
def test_smoke_validator_accepts_current_and_legacy_layout(tmp_path, layout):
    events = tmp_path / layout / 'telemetry-events'
    events.mkdir(parents=True)
    (events / 'agent-worker.jsonl').write_text(json.dumps({
        'record_type': 'span', 'name': 'tool.call', 'run_id': 'r',
        'trace_id': 't', 'span_id': 'p'}) + '\n')
    (events / 'sandbox-worker.jsonl').write_text(json.dumps({
        'record_type': 'span', 'name': 'sandbox.exec', 'run_id': 'r',
        'trace_id': 't', 'span_id': 'child', 'parent_span_id': 'p'}) + '\n')
    result = subprocess.run([sys.executable, '-m', 'examples.sandbox.validate_smoke',
        str(tmp_path), '--require-sandbox'], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['linked_sandbox_execs'] == 1


@pytest.fixture
def calculator(monkeypatch, tmp_path):
    tools = ModuleType('verl.tools.function_tool')
    tools.function_tool = lambda name: lambda fn: fn
    monkeypatch.setitem(sys.modules, 'verl.tools.function_tool', tools)
    spec = importlib.util.spec_from_file_location('test_calculator_tools',
                ROOT / 'examples/sandbox/calculator_tools.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('TELEMETRY_EVENTS_DIR', str(tmp_path))
    monkeypatch.setenv('TELEMETRY_RUN_ID', 'calculator-case')
    monkeypatch.setenv('TELEMETRY_NODE', 'test-node')
    return module


def test_calculator_external_program_rejects_code(calculator):
    result = subprocess.run([sys.executable, '-c', calculator.PROGRAM, '(3 + 4) * 2'],
                            capture_output=True, text=True)
    assert result.returncode == 0 and json.loads(result.stdout)['result'] == 14
    rejected = subprocess.run([sys.executable, '-c', calculator.PROGRAM,
                               '__import__("os").getcwd()'], capture_output=True, text=True)
    assert rejected.returncode != 0 and 'Only arithmetic' in rejected.stderr


@pytest.mark.parametrize('failed', [False, True])
def test_calculator_links_success_and_error_spans(calculator, monkeypatch, tmp_path, failed):
    def run(command, **kwargs):
        assert command[:2] == ['docker', 'run']
        assert '--network' in command and 'none' in command
        assert kwargs['check'] is True
        if failed:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout='{"result": 14}')
    monkeypatch.setattr(calculator.subprocess, 'run', run)
    answer = json.loads(calculator.calculate('(3+4)*2'))
    assert ('error' in answer) is failed
    records = [json.loads(line) for f in tmp_path.glob('*.jsonl') for line in f.read_text().splitlines()]
    parent = next(r for r in records if r['name'] == 'tool.call')
    child = next(r for r in records if r['name'] == 'sandbox.exec')
    assert child['trace_id'] == parent['trace_id']
    assert child['parent_span_id'] == parent['span_id']
    assert parent['status'] == child['status'] == ('error' if failed else 'ok')


def test_calculator_invalid_argument_records_error_without_docker(calculator, monkeypatch, tmp_path):
    def unexpected(*args, **kwargs):
        pytest.fail('invalid expression must not launch Docker')
    monkeypatch.setattr(calculator.subprocess, 'run', unexpected)
    assert 'error' in json.loads(calculator.calculate(None))
    records = [json.loads(line) for f in tmp_path.glob('*.jsonl') for line in f.read_text().splitlines()]
    assert len(records) == 1 and records[0]['name'] == 'tool.call'
    assert records[0]['status'] == 'error'


@pytest.mark.parametrize('identity', [None, '', {}])
def test_smoke_validator_rejects_empty_or_unhashable_trace_identity(tmp_path, identity):
    from examples.sandbox.validate_smoke import validate
    (tmp_path / 'agent-invalid.jsonl').write_text(json.dumps({
        'record_type': 'span', 'name': 'tool.call', 'run_id': 'r',
        'trace_id': identity, 'span_id': 'parent'}) + '\n')
    with pytest.raises(ValueError, match='invalid run/trace/span identity'):
        validate(tmp_path)
