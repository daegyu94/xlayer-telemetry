"""Public launch and shutdown regressions with fixture-owned processes only."""
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

from tests._process_helpers import process_running, read_process

ROOT = Path(__file__).parents[1]


def wait_file(path, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(.02)
    raise AssertionError(f"fixture did not start: {path}")


def kill(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_advanced_bash_run_forwards_term_to_workload(tmp_path):
    marker = tmp_path / 'workload.pid'
    workload = tmp_path / 'workload.py'
    workload.write_text('import os,time\nfrom pathlib import Path\n'
                        f'Path({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n')
    config = tmp_path / 'config'
    config.write_text(f'TELEMETRY_HOME={shlex.quote(str(tmp_path / "state"))}\n'
                      f'RUN_ROOT={shlex.quote(str(tmp_path / "run"))}\nRUN_ID=signal-test\n'
                      f'VERL_COMMAND=({shlex.quote(sys.executable)} {shlex.quote(str(workload))})\n')
    unrelated = subprocess.Popen(['sleep', '60'])
    launch = subprocess.Popen(['bash', str(ROOT / 'scripts/verl_local.sh'), '--config', str(config), 'run'],
        env=os.environ | {'TELEMETRY_PYTHON': sys.executable}, start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid = None
    try:
        pid = wait_file(marker)
        identity = read_process(pid)
        launch.terminate()  # Only the externally visible Bash PID, not its group.
        launch.wait(timeout=9)
        assert not process_running(identity), 'workload survived TERM to the advanced Bash entrypoint'
        assert unrelated.poll() is None
    finally:
        if pid is not None: kill(pid)
        try: os.killpg(launch.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        if launch.poll() is None: launch.kill()
        launch.wait(timeout=5)
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_public_down_reaps_inflight_gpu_query(tmp_path):
    import platform
    from tests.test_cli import invoke
    arch = 'arm64' if platform.machine() in {'aarch64', 'arm64'} else 'amd64'
    tools = tmp_path / 'tools'
    exporter = tools / f'node_exporter-1.9.1.linux-{arch}/node_exporter'
    exporter.parent.mkdir(parents=True)
    exporter.write_text('#!/bin/sh\nexec sleep 60\n')
    exporter.chmod(0o755)
    fake = tmp_path / 'bin'
    fake.mkdir()
    marker = tmp_path / 'smi.pid'
    smi = fake / 'nvidia-smi'
    smi.write_text(f'#!{sys.executable}\nimport os,signal,time\n'
                   'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
                   f'open({str(marker)!r},"w").write(str(os.getpid()))\ntime.sleep(60)\n')
    smi.chmod(0o755)
    curl = fake / 'curl'
    curl.write_text('#!/bin/sh\nexit 0\n')
    curl.chmod(0o755)
    config = tmp_path / 'config'
    config.write_text(f'TELEMETRY_HOME={str(tmp_path / "state")!r}\nTOOLS_DIR={str(tools)!r}\nENABLE_GPU_METRICS=1\nENABLE_LOGS=0\n')
    env = os.environ | {'PATH': str(fake) + ':' + os.environ['PATH']}
    unrelated = subprocess.Popen(['sleep', '60'])
    identity = None
    try:
        up = invoke(config, 'up', '--role', 'node', env=env)
        assert up.returncode == 0, up.stderr
        identity = read_process(wait_file(marker))
        down = invoke(config, 'down', '--role', 'node', env=env)
        assert down.returncode == 0, down.stderr
        assert not process_running(identity), 'nvidia-smi survived managed collector shutdown'
        assert unrelated.poll() is None
    finally:
        invoke(config, 'down', '--role', 'node', env=env)
        if identity is not None:
            from tests._process_helpers import signal_process
            signal_process(identity, signal.SIGKILL)
        unrelated.terminate()
        unrelated.wait(timeout=5)


import pytest


@pytest.mark.parametrize('failure', ['term', 'node-failure', 'validator-failure'])
def test_startup_validation_is_interruptible_and_preserves_failure(tmp_path, failure):
    import platform
    from xlayer_telemetry.grafana_release import release
    arch = 'arm64' if platform.machine() in {'aarch64', 'arm64'} else 'amd64'
    tools = tmp_path / 'tools'
    marker = tmp_path / 'validator.pid'
    dispatcher = tmp_path / 'python'
    dispatcher.write_text(f'#!{sys.executable}\n' + '''import os,sys,signal,time
if 'xlayer_telemetry.stack' in sys.argv and 'validate-stack' in sys.argv:
 signal.signal(signal.SIGTERM,signal.SIG_IGN)
 open(os.environ['VALIDATOR_PID'],'w').write(str(os.getpid()))
 if os.environ['FAILURE_MODE']=='validator-failure': raise SystemExit(17)
 time.sleep(60)
os.execv(os.environ['REAL_PYTHON'],[os.environ['REAL_PYTHON'],*sys.argv[1:]])
''')
    dispatcher.chmod(0o755)
    children = []
    grafana_version = release(arch=arch)['version']
    for name, binary in [('prometheus', tools / f'prometheus-3.5.0.linux-{arch}/prometheus'),
                         ('grafana', tools / f'grafana-{grafana_version}/bin/grafana'),
                         ('node', tools / f'node_exporter-1.9.1.linux-{arch}/node_exporter')]:
        binary.parent.mkdir(parents=True)
        pidfile = tmp_path / f'{name}.pid'
        children.append(pidfile)
        body = f'open({str(pidfile)!r},"w").write(str(os.getpid()))\n'
        if name == 'node' and failure == 'node-failure':
            body += f'from pathlib import Path\nwhile not Path({str(marker)!r}).exists(): time.sleep(.02)\nraise SystemExit(19)\n'
        else: body += 'time.sleep(60)\n'
        binary.write_text(f'#!{sys.executable}\nimport os,time\n' + body)
        binary.chmod(0o755)
    fake = tmp_path / 'bin'; fake.mkdir()
    (fake / 'curl').write_text('#!/bin/sh\nexit 0\n'); (fake / 'curl').chmod(0o755)
    config = tmp_path / 'config'
    config.write_text(f'TELEMETRY_HOME={str(tmp_path / "state")!r}\nTOOLS_DIR={str(tools)!r}\n'
                      f'TELEMETRY_PYTHON={str(dispatcher)!r}\nENABLE_GPU_METRICS=0\nENABLE_LOGS=0\n')
    env = os.environ | {'PATH': str(fake)+':'+os.environ['PATH'], 'REAL_PYTHON': sys.executable,
                        'VALIDATOR_PID': str(marker), 'FAILURE_MODE': failure}
    unrelated = subprocess.Popen(['sleep', '60'])
    with (tmp_path / 'up.log').open('w') as log:
        up = subprocess.Popen([sys.executable, '-m', 'xlayer_telemetry.cli', '--config', str(config), 'up',
                               '--role', 'all' if failure == 'node-failure' else 'server'],
                              env=env, stdout=log, stderr=log)
        try:
            wait_file(marker)
            identities = [read_process(int(path.read_text())) for path in [marker, *children] if path.exists()]
            if failure == 'term': up.terminate()
            up.wait(timeout=14)
            assert up.returncode != 0
            assert not any(process_running(identity) for identity in identities if identity is not None), (tmp_path / 'up.log').read_text()
            assert unrelated.poll() is None
            server_log = (tmp_path / 'state/state/verl-local/server.log').read_text()
            assert 'Monitoring server ready:' not in server_log
        finally:
            # Fixture process identities are recorded before signaling; never
            # kill by binary name or a previously allocated port.
            from tests._process_helpers import signal_process
            for path in [marker, *children]:
                if path.exists():
                    identity = read_process(int(path.read_text()))
                    if identity is not None: signal_process(identity, signal.SIGKILL)
            if up.poll() is None: up.kill()
            up.wait(timeout=5)
            from tests.test_cli import invoke
            invoke(config, 'down', env=env)
            unrelated.terminate(); unrelated.wait(timeout=5)
