"""Bounded tail visibility and health are independent of workload outcome."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from xlayer_telemetry import logger_settle
from xlayer_telemetry.operations.config import ConfigError, defaults, load_config, validate
from xlayer_telemetry.telemetry_health import finish

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize('quiet,maximum', [('0', '2'), ('nan', '2'), ('601', '700'),
                                         ('1', '.5'), ('1', 'inf'), ('1', '3601')])
def test_invalid_policy_rejected_by_config_and_wrapper_before_workload(tmp_path, quiet, maximum):
    config = defaults() | {'TELEMETRY_LOGGER_SETTLE_SECONDS': quiet,
                           'TELEMETRY_LOGGER_MAX_WAIT_SECONDS': maximum}
    with pytest.raises(ConfigError, match='TELEMETRY_LOGGER_'):
        validate(config)
    marker = tmp_path / 'workload-started'
    result = subprocess.run(['bash', str(ROOT / 'scripts/run_verl_with_telemetry.sh'),
        '--output', str(tmp_path / 'run'), '--', 'touch', str(marker)],
        env=os.environ | config, capture_output=True, text=True, timeout=5)
    assert result.returncode == 2 and not marker.exists()
    assert 'TELEMETRY_LOGGER_' in result.stderr


def test_settle_config_defaults_and_numeric_toml(tmp_path, monkeypatch):
    for key in ('TELEMETRY_LOGGER_SETTLE_SECONDS', 'TELEMETRY_LOGGER_MAX_WAIT_SECONDS'):
        monkeypatch.delenv(key)
    path = tmp_path / 'config.toml'
    path.write_text('[telemetry]\nTELEMETRY_LOGGER_SETTLE_SECONDS = 0.25\n'
                    'TELEMETRY_LOGGER_MAX_WAIT_SECONDS = 2\n')
    config, _ = load_config(path)
    assert float(config['TELEMETRY_LOGGER_SETTLE_SECONDS']) == .25
    assert float(config['TELEMETRY_LOGGER_MAX_WAIT_SECONDS']) == 2
    assert defaults()['TELEMETRY_LOGGER_SETTLE_SECONDS'] == '20'
    assert defaults()['TELEMETRY_LOGGER_MAX_WAIT_SECONDS'] == '120'


@pytest.mark.parametrize('content,status,reason', [(None, 'timeout', 'logger_missing'),
    (b'', 'incomplete', 'empty_or_unterminated_logger'),
    (b'{"step":1}', 'incomplete', 'empty_or_unterminated_logger'),
    (b'{"step":1}\n', 'settled', 'quiet_complete_line')])
def test_missing_and_partial_logger_have_bounded_explicit_outcomes(tmp_path, monkeypatch, content, status, reason):
    path = tmp_path / 'logger.jsonl'
    if content is not None:
        path.write_bytes(content)
    now = [0.0]
    monkeypatch.setattr(logger_settle.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(logger_settle.time, 'sleep', lambda duration: now.__setitem__(0, now[0] + duration))
    result = logger_settle.wait_for_logger(path, quiet_seconds=.5, max_wait_seconds=1)
    assert result['status'] == status and result['reason'] == reason
    assert result['waited_seconds'] <= 1


def test_tail_changes_reset_quiet_even_with_same_size_and_mtime(tmp_path, monkeypatch):
    path = tmp_path / 'logger.jsonl'
    path.write_bytes(b'{"step":1}\n')
    original = path.stat()
    now = [0.0]
    monkeypatch.setattr(logger_settle.time, 'monotonic', lambda: now[0])
    def change(duration):
        now[0] += duration
        if now[0] < .6:
            path.write_bytes(b'{"step":2}\n' if now[0] < .4 else b'{"step":3}\n')
            os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    monkeypatch.setattr(logger_settle.time, 'sleep', change)
    result = logger_settle.wait_for_logger(path, quiet_seconds=.5, max_wait_seconds=1.5)
    assert result['status'] == 'settled' and result['waited_seconds'] >= .9


@pytest.mark.parametrize('content,status', [(b'{"step":1}', 'incomplete'), (b'', 'incomplete')])
def test_wrapper_reports_partial_logger_without_changing_failed_workload_exit(tmp_path, content, status):
    workload = tmp_path / 'driver.py'
    workload.write_text('import os,pathlib,sys\n'
        f'pathlib.Path(os.environ["VERL_FILE_LOGGER_PATH"]).write_bytes({content!r})\nsys.exit(7)\n')
    output = tmp_path / 'run'
    result = subprocess.run(['bash', str(ROOT / 'scripts/run_verl_with_telemetry.sh'),
        '--output', str(output), '--', sys.executable, str(workload)],
        env=os.environ | {'TELEMETRY_PYTHON': sys.executable},
        capture_output=True, text=True, timeout=8)
    assert result.returncode == 7, result.stderr
    health = json.loads((output / 'telemetry-health.json').read_text())
    assert health['status'] == 'partial' and health['workload']['exit_code'] == 7
    assert health['logger_settle']['status'] == status
    assert f'bridge:logger_settle:{status}' in health['issues']


def test_health_does_not_accept_settled_without_matching_report(tmp_path):
    (tmp_path / 'logger-settle.json').write_text('{"status":"timeout"}')
    result = finish(tmp_path, 0, 'ok', 'disabled', 'settled')
    assert result['status'] == 'partial'
    assert 'bridge:logger_settle:unreadable_report' in result['issues']


def test_old_health_api_remains_readable_without_settle_report(tmp_path):
    result = finish(tmp_path, 0, 'ok', 'disabled')
    assert result['status'] == 'complete' and 'logger_settle' not in result


def test_changing_logger_hits_monotonic_deadline(tmp_path, monkeypatch):
    path = tmp_path / 'logger.jsonl'
    path.write_bytes(b'{"step":1}\n')
    now = [0.0]
    monkeypatch.setattr(logger_settle.time, 'monotonic', lambda: now[0])
    def append(duration):
        now[0] += duration
        with path.open('ab') as stream:
            stream.write(b'{"step":2}\n')
    monkeypatch.setattr(logger_settle.time, 'sleep', append)
    result = logger_settle.wait_for_logger(path, quiet_seconds=.5, max_wait_seconds=1)
    assert result['status'] == 'timeout' and result['waited_seconds'] == 1
    assert result['reason'] == 'logger_not_quiet'


def test_public_cli_carries_settle_config_into_saved_partial_run(tmp_path, monkeypatch):
    monkeypatch.delenv('TELEMETRY_LOGGER_SETTLE_SECONDS')
    monkeypatch.delenv('TELEMETRY_LOGGER_MAX_WAIT_SECONDS')
    config = tmp_path / 'config.toml'
    run = tmp_path / 'run'
    config.write_text('[telemetry]\nRUN_ID="partial-cli"\n'
        f'TELEMETRY_HOME={json.dumps(str(tmp_path / "state"))}\nRUN_ROOT={json.dumps(str(run))}\n'
        'TELEMETRY_LOGGER_SETTLE_SECONDS=0.08\nTELEMETRY_LOGGER_MAX_WAIT_SECONDS=0.4\n')
    command = 'import os,pathlib,sys;pathlib.Path(os.environ["VERL_FILE_LOGGER_PATH"]).write_text("{}");sys.exit(7)'
    result = subprocess.run([sys.executable, '-m', 'xlayer_telemetry.cli', '--config', str(config),
        'run', '--', sys.executable, '-c', command], capture_output=True, text=True, timeout=10)
    assert result.returncode == 7, result.stderr
    health = json.loads((run / 'telemetry-health.json').read_text())
    assert health['status'] == 'partial' and health['logger_settle']['status'] == 'incomplete'
    assert health['logger_settle']['quiet_seconds'] == .08
    assert health['logger_settle']['max_wait_seconds'] == .4


def test_signal_during_settle_cleans_sidecars_and_keeps_peer_alive(tmp_path):
    import signal
    import time

    run = tmp_path / 'run'
    workload = 'import os,pathlib;pathlib.Path(os.environ["VERL_FILE_LOGGER_PATH"]).write_text(\'{"step":1,"data":{"timing_s/gen":1}}\\n\')'
    peer = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
    wrapper = subprocess.Popen(['bash', str(ROOT / 'scripts/run_verl_with_telemetry.sh'),
        '--output', str(run), '--', sys.executable, '-c', workload],
        env=os.environ | {'TELEMETRY_PYTHON': sys.executable,
            'TELEMETRY_LOGGER_SETTLE_SECONDS': '20', 'TELEMETRY_LOGGER_MAX_WAIT_SECONDS': '30'},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    owned = []
    try:
        deadline = time.monotonic() + 8
        while not (run / 'logs/telemetry-logger-settle.log').exists():
            assert wrapper.poll() is None
            assert time.monotonic() < deadline, 'wrapper did not reach logger settle'
            time.sleep(.02)
        children = Path(f'/proc/{wrapper.pid}/task/{wrapper.pid}/children').read_text().split()
        for child in children:
            stat = Path(f'/proc/{child}/stat')
            if stat.exists():
                owned.append((stat, stat.read_text().rsplit(') ', 1)[1].split()[19]))
        wrapper.send_signal(signal.SIGTERM)
        assert wrapper.wait(timeout=10) == 143
        assert peer.poll() is None
        health = json.loads((run / 'telemetry-health.json').read_text())
        assert health['status'] == 'partial' and health['logger_settle']['status'] == 'interrupted'
        for stat, start in owned:
            if stat.exists():
                fields = stat.read_text().rsplit(') ', 1)[1].split()
                assert fields[19] != start or fields[0] == 'Z', f'owned sidecar remains: {stat}'
    finally:
        if wrapper.poll() is None:
            wrapper.send_signal(signal.SIGTERM)
            wrapper.wait(timeout=10)
        peer.terminate(); peer.wait(timeout=5)


def test_wrapper_timeout_preserves_workload_exit_and_exports_visible_rows(tmp_path):
    run = tmp_path / 'run'
    ready = tmp_path / 'ready'
    peer_code = """import pathlib,sys,time
ready=pathlib.Path(sys.argv[1]); logger=pathlib.Path(sys.argv[2]); end=time.monotonic()+8
while not ready.exists():
 if time.monotonic()>end:sys.exit(2)
 time.sleep(.01)
for step in range(2,32):
 time.sleep(.05)
 with logger.open('a') as stream:stream.write('{"step":'+str(step)+',"data":{"timing_s/gen":1}}\\n')
"""
    peer = subprocess.Popen([sys.executable, '-c', peer_code, str(ready), str(run / 'logs/verl-metrics.jsonl')])
    workload = tmp_path / 'driver.py'
    workload.write_text('import pathlib,os,sys\n'
        'pathlib.Path(os.environ["VERL_FILE_LOGGER_PATH"]).write_text(\'{"step":1,"data":{"timing_s/gen":1}}\\n\')\n'
        f'pathlib.Path({str(ready)!r}).touch()\nsys.exit(7)\n')
    try:
        result = subprocess.run(['bash', str(ROOT / 'scripts/run_verl_with_telemetry.sh'),
            '--output', str(run), '--', sys.executable, str(workload)],
            env=os.environ | {'TELEMETRY_PYTHON': sys.executable,
                'TELEMETRY_LOGGER_SETTLE_SECONDS': '.2', 'TELEMETRY_LOGGER_MAX_WAIT_SECONDS': '.5'},
            capture_output=True, text=True, timeout=10)
        assert result.returncode == 7, result.stderr
        health = json.loads((run / 'telemetry-health.json').read_text())
        assert health['logger_settle']['status'] == 'timeout' and health['status'] == 'partial'
        assert health['final_export']['bridge'] == 'ok'
        assert 'bridge:logger_settle:timeout' in health['issues']
        assert (run / 'telemetry-events/verl-steps.jsonl').read_text().splitlines()
    finally:
        if peer.poll() is None:
            peer.terminate()
        peer.wait(timeout=5)


def test_watchdog_bounds_a_stuck_helper_without_killing_existing_peer(tmp_path):
    import time

    interpreter = tmp_path / 'telemetry-python'
    helper_pid = tmp_path / 'helper.pid'
    interpreter.write_text('#!' + sys.executable + '\nimport os,sys,signal,time\n'
        'if "xlayer_telemetry.logger_settle" in sys.argv and "--check-config" not in sys.argv:\n'
        ' signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
        ' open(os.environ["HELPER_PID_FILE"], "w").write(str(os.getpid()))\n'
        ' time.sleep(30)\n'
        f'os.execv({sys.executable!r}, [{sys.executable!r}]+sys.argv[1:])\n')
    interpreter.chmod(0o755)
    run = tmp_path / 'run'
    workload = 'import os,pathlib;pathlib.Path(os.environ["VERL_FILE_LOGGER_PATH"]).write_text(\'{"step":1,"data":{"timing_s/gen":1}}\\n\')'
    peer = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
    began = time.monotonic()
    try:
        result = subprocess.run(['bash', str(ROOT / 'scripts/run_verl_with_telemetry.sh'),
            '--output', str(run), '--', sys.executable, '-c', workload],
            env=os.environ | {'TELEMETRY_PYTHON': str(interpreter),
                'HELPER_PID_FILE': str(helper_pid), 'TELEMETRY_LOGGER_SETTLE_SECONDS': '.1',
                'TELEMETRY_LOGGER_MAX_WAIT_SECONDS': '.2'},
            capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert time.monotonic() - began < 9
        assert peer.poll() is None
        health = json.loads((run / 'telemetry-health.json').read_text())
        assert health['status'] == 'partial' and health['logger_settle']['status'] == 'timeout'
        assert health['final_export']['bridge'] == 'ok'
        stat = Path(f'/proc/{helper_pid.read_text()}/stat')
        assert not stat.exists() or stat.read_text().rsplit(') ', 1)[1].split()[0] == 'Z'
    finally:
        peer.terminate(); peer.wait(timeout=5)
        if helper_pid.exists():
            try:
                os.kill(int(helper_pid.read_text()), 9)
            except ProcessLookupError:
                pass
