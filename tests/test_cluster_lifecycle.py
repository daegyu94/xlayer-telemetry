"""Cluster orchestration preserves local ownership and reports partial failures."""
import fcntl
import json
import os
from pathlib import Path
import sys

import pytest

from xlayer_telemetry import cli
from xlayer_telemetry.cluster_inventory import InventoryError, load_inventory
from xlayer_telemetry.operations import cluster_lifecycle as lifecycle

BOOT = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
NEW = [123, 456, BOOT]


def inventory_file(tmp_path):
    path = tmp_path / 'inventory.json'
    path.write_text(json.dumps({'schema_version': 1, 'cluster': {'name': 'test', 'monitoring_node': 'monitor'},
        'nodes': [
            {'name': 'monitor', 'address': '10.0.0.1', 'roles': ['monitoring'], 'collector': False,
             'management': {'ssh': 'operator@monitor', 'server_config': '/srv/server.toml'}},
            {'name': 'worker', 'address': '10.0.0.2', 'roles': ['trainer'],
             'management': {'ssh': 'operator@worker', 'node_config': '/srv/node.toml'}}]}))
    return path


def reply(action, *, started=None):
    return {'ok': True, 'status': 'stopped' if action == 'down' else 'running',
            'process': 'stopped' if action == 'down' else 'running', 'started_identity': started}


@pytest.mark.parametrize(('key', 'value'), [
    ('ssh', '-oProxyCommand=malicious'), ('ssh', 'user@host@other'),
    ('ssh', 'user;command@host'), ('ssh', 'user@host\ncommand'),
    ('python', '-c'), ('python', 'python3 -c code'), ('python', '/bin/python\ncode'),
    ('node_config', 'relative.toml'), ('node_config', '/config\nsecret'),
    ('password', 'never-echo-secret'),
])
def test_inventory_rejects_unsafe_management_without_echoing_values(tmp_path, key, value):
    path = inventory_file(tmp_path)
    document = json.loads(path.read_text())
    document['nodes'][1]['management'][key] = value
    path.write_text(json.dumps(document))
    with pytest.raises(InventoryError) as error:
        load_inventory(path)
    assert value not in str(error.value)


def test_cli_up_down_status_reuse_inventory_and_do_not_require_local_config(tmp_path, monkeypatch, capsys):
    path = inventory_file(tmp_path)
    calls = []
    def remote(target, action, timeout, **kwargs):
        calls.append((target['node'], target['role'], action))
        return reply(action)
    monkeypatch.setattr(lifecycle, '_ssh', remote)
    for action in ('up', 'down', 'status'):
        assert cli.main(['cluster', action, '--inventory', str(path), '--json']) == 0
        result = json.loads(capsys.readouterr().out)
        assert result['scope'] == 'inventory_managed_roles' and result['status'] == 'ok'
    assert calls == [('monitor', 'server', 'up'), ('worker', 'node', 'up'),
                     ('worker', 'node', 'down'), ('monitor', 'server', 'down'),
                     ('monitor', 'server', 'status'), ('worker', 'node', 'status')]
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('change', ['missing_monitor', 'missing_config', 'invalid_timeout'])
def test_admission_finishes_before_any_ssh_mutation(tmp_path, monkeypatch, capsys, change):
    path = inventory_file(tmp_path)
    document = json.loads(path.read_text())
    if change == 'missing_monitor':
        del document['cluster']['monitoring_node']
    elif change == 'missing_config':
        del document['nodes'][1]['management']['node_config']
    path.write_text(json.dumps(document))
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid inventory must not contact a host')
    monkeypatch.setattr(lifecycle, '_ssh', forbidden)
    args = ['cluster', 'up', '--inventory', str(path)]
    if change == 'invalid_timeout':
        args += ['--timeout', '0']
    assert cli.main(args) == 2
    assert capsys.readouterr().err


def test_partial_up_rolls_back_only_new_identity_and_skips_remaining_hosts(tmp_path, monkeypatch):
    path = inventory_file(tmp_path)
    data = json.loads(path.read_text())
    data['nodes'].append({'name': 'z-last', 'address': '10.0.0.3', 'roles': ['trainer'],
                         'management': {'ssh': 'last', 'node_config': '/last.toml'}})
    path.write_text(json.dumps(data))
    calls = []
    def remote(target, action, timeout, **kwargs):
        calls.append((target['node'], action, kwargs.get('expected_identity')))
        if action == 'down':
            assert kwargs['expected_identity'] == NEW
            return reply(action)
        return reply(action, started=NEW) if target['node'] == 'monitor' else lifecycle._failure('command_failed', status='failed')
    monkeypatch.setattr(lifecycle, '_ssh', remote)
    report, code = lifecycle.run(load_inventory(path), 'up', 1)
    assert code == 1 and report['cleanup'] == 'completed'
    assert report['results'][-1]['status'] == 'not_attempted'
    assert calls == [('monitor', 'up', None), ('worker', 'up', None), ('monitor', 'down', NEW)]


def test_preexisting_stack_is_preserved_and_disconnect_cleanup_is_unknown(tmp_path, monkeypatch):
    calls = []
    def remote(target, action, timeout, **kwargs):
        calls.append(action)
        return reply(action) if target['node'] == 'monitor' else lifecycle._failure('timeout')
    monkeypatch.setattr(lifecycle, '_ssh', remote)
    report, code = lifecycle.run(load_inventory(inventory_file(tmp_path)), 'up', 1)
    assert code == 1 and report['cleanup'] == 'unconfirmed' and report['rollback'] == []
    assert calls == ['up', 'up']


def test_down_continues_after_unreachable_node_and_cancellation_rolls_back_known_starts(tmp_path, monkeypatch):
    def remote(target, action, timeout, **kwargs):
        return lifecycle._failure('transport_error') if target['node'] == 'worker' else reply(action)
    monkeypatch.setattr(lifecycle, '_ssh', remote)
    report, code = lifecycle.run(load_inventory(inventory_file(tmp_path)), 'down', 1)
    assert code == 1 and len(report['results']) == 2 and report['results'][1]['ok']
    def interrupted(target, action, timeout, **kwargs):
        if action == 'up' and target['node'] == 'worker':
            raise lifecycle._Cancelled(15)
        return reply(action, started=NEW if action == 'up' else None)
    monkeypatch.setattr(lifecycle, '_ssh', interrupted)
    report, code = lifecycle.run(load_inventory(tmp_path / 'inventory.json'), 'up', 1)
    assert code == 143 and report['status'] == 'interrupted' and report['rollback'][0]['ok']


def worker_request(tmp_path):
    path = tmp_path / 'node.toml'
    path.write_text('[telemetry]\nCLUSTER_NAME="test"\nNODE_NAME="worker"\nENABLE_GPU_METRICS=false\n'
                    'TELEMETRY_HOME=' + json.dumps(str(tmp_path / 'telemetry')) + '\n')
    return {'action': 'up', 'config': str(path), 'role': 'node', 'node': 'worker', 'cluster': 'test'}


def test_worker_uses_same_lock_and_local_failed_up_cleans_only_its_new_process(tmp_path, monkeypatch):
    request = worker_request(tmp_path)
    identity = {'value': None}
    calls = []
    monkeypatch.setattr(lifecycle, '_identity', lambda *args: identity['value'])
    def launch(config, action, role, *, lock_fd, output):
        lock_path = Path(config['TELEMETRY_HOME']) / 'state/verl-local/lifecycle.lock'
        with lock_path.open() as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        calls.append(action)
        identity['value'] = NEW if action == 'up' else None
        return 1 if action == 'up' else 0
    monkeypatch.setattr(cli, '_launch', launch)
    result = lifecycle.manage_host(request)
    assert not result['ok'] and result['process'] == 'stopped' and result['started_identity'] is None
    assert calls == ['up', 'down']
    # The same failure with a preexisting process must never call down.
    identity['value'] = NEW
    calls.clear()
    result = lifecycle.manage_host(request)
    assert not result['ok'] and result['started_identity'] is None and calls == ['up']


def test_worker_refuses_wrong_cluster_and_changed_rollback_identity(tmp_path, monkeypatch):
    request = worker_request(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('No process may be signalled')
    monkeypatch.setattr(cli, '_launch', forbidden)
    assert lifecycle.manage_host(request | {'cluster': 'other'})['issue'] == 'invalid_config'
    monkeypatch.setattr(lifecycle, '_identity', lambda *args: [999, 456, BOOT])
    result = lifecycle.manage_host(request | {'action': 'down', 'expected_identity': NEW})
    assert result['issue'] == 'identity_changed'


def fake_ssh(tmp_path, monkeypatch, body):
    executable = tmp_path / 'ssh'
    executable.write_text('#!' + sys.executable + '\n' + body)
    executable.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + ':' + os.environ['PATH'])
    return {'node': 'worker', 'role': 'node', 'config': "/tmp/a 'quote' $(touch nope).toml",
            'cluster': 'test', 'management': {'ssh': 'user@host', 'python': "/tmp/python 'quoted'"}}


def test_ssh_quotes_remote_executable_and_never_echoes_raw_output(tmp_path, monkeypatch):
    target = fake_ssh(tmp_path, monkeypatch, '''import json, shlex, sys
request=json.load(sys.stdin)
assert request['config'].endswith("$(touch nope).toml")
assert shlex.split(sys.argv[-1]) == ["/tmp/python 'quoted'", '-m', 'xlayer_telemetry.operations.cluster_lifecycle']
assert sys.argv[-2] == 'user@host'
print(json.dumps({'ok': True, 'status': 'running', 'process': 'running'}))
''')
    assert lifecycle._ssh(target, 'up', 2)['ok']
    target = fake_ssh(tmp_path, monkeypatch, "import sys\nsys.stdin.read()\nprint('never-echo-secret')\n")
    response = lifecycle._ssh(target, 'up', 2)
    assert response == lifecycle._failure('invalid_response')
    assert 'never-echo-secret' not in json.dumps(response)


@pytest.mark.parametrize('mode', ['timeout', 'output_limit'])
def test_ssh_bounds_and_reaps_its_session(tmp_path, monkeypatch, mode):
    marker = tmp_path / 'children.json'
    body = f'''import json, os, subprocess, sys, time
sys.stdin.read()
child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
open({str(marker)!r}, 'w').write(json.dumps([os.getpid(), child.pid]))
'''
    body += "print('x'*70000, flush=True)\n" if mode == 'output_limit' else ''
    body += 'time.sleep(30)\n'
    target = fake_ssh(tmp_path, monkeypatch, body)
    assert lifecycle._ssh(target, 'up', 1)['issue'] == mode
    for pid in json.loads(marker.read_text()):
        path = Path('/proc') / str(pid) / 'stat'
        # Reaped children or init-owned zombies are no longer executing.
        assert not path.exists() or path.read_text().rsplit(') ', 1)[1].startswith('Z ')


@pytest.fixture
def local_stack(tmp_path, monkeypatch):
    """Real launcher/processes in private state, with synthetic readiness/health."""
    import shutil
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    root = tmp_path / 'assets'
    (root / 'scripts').mkdir(parents=True)
    shutil.copy2(Path(__file__).parents[1] / 'scripts/verl_local.sh', root / 'scripts/verl_local.sh')
    (root / 'scripts/run_telemetry.sh').write_text('''#!/usr/bin/env bash
role=$1
if [[ $role == node && ${FAKE_NODE_FAIL:-0} == 1 ]]; then exit 1; fi
touch "$MARKER_DIR/$role.ready"
if [[ $role == server ]]; then echo 'Monitoring server ready:'; fi
trap 'touch "$MARKER_DIR/$role.stopped"; exit 0' TERM
while true; do sleep 0.1; done
''')
    binary = tmp_path / 'bin'
    binary.mkdir()
    (binary / 'curl').write_text('#!/usr/bin/env bash\n[[ -f "$MARKER_DIR/node.ready" ]]\n')
    (binary / 'curl').chmod(0o755)
    monkeypatch.setenv('PATH', str(binary) + ':' + os.environ['PATH'])
    monkeypatch.setenv('MARKER_DIR', str(tmp_path))
    monkeypatch.setenv('TELEMETRY_PYTHON', sys.executable)
    monkeypatch.setattr(cli, 'assets_root', lambda: root)

    class Backend(BaseHTTPRequestHandler):
        def do_GET(self):
            data = {'database': 'ok', 'version': '13.1.7', 'status': 'success', 'data': {'activeTargets': [
                {'labels': {'job': 'telemetry', 'cluster': 'test', 'nodename': 'worker'},
                 'health': 'up', 'scrapeUrl': 'http://127.0.0.1:19100/metrics'}]}}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        def log_message(self, *args):
            pass

    backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{backend.server_port}'
    path = inventory_file(tmp_path)
    data = json.loads(path.read_text())
    for row in data['nodes']:
        role = 'server' if row['name'] == 'monitor' else 'node'
        config = tmp_path / (row['name'] + " 'quoted' $.toml")
        values = {'CLUSTER_NAME': 'test', 'NODE_NAME': row['name'], 'NODE_ADDR': '127.0.0.1',
                  'TELEMETRY_HOME': str(tmp_path / row['name']), 'ENABLE_GPU_METRICS': False,
                  'TELEMETRY_TARGETS': 'worker=127.0.0.1', 'PROMETHEUS_URL': url, 'GRAFANA_URL': url}
        config.write_text('[telemetry]\n' + '\n'.join(f'{key}={json.dumps(value)}' for key, value in values.items()))
        row['management'][role + '_config'] = str(config)
    path.write_text(json.dumps(data))
    inventory = load_inventory(path)

    def remote(target, action, timeout, **kwargs):
        request = {key: target[key] for key in ('config', 'role', 'node', 'cluster')}
        return lifecycle.manage_host(request | {'action': action} | kwargs)
    monkeypatch.setattr(lifecycle, '_ssh', remote)
    try:
        yield path, inventory
    finally:
        # Cleanup only the two private test configs; production state is never selected.
        lifecycle.run(inventory, 'down', 2)
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=2)


def test_public_cluster_lifecycle_starts_checks_and_stops_real_owned_processes(local_stack, capsys):
    path, inventory = local_stack
    assert cli.main(['cluster', 'up', '--inventory', str(path), '--json']) == 0
    assert all(row['started'] for row in json.loads(capsys.readouterr().out)['results'])
    assert cli.main(['cluster', 'up', '--inventory', str(path), '--json']) == 0
    assert not any(row['started'] for row in json.loads(capsys.readouterr().out)['results'])
    assert cli.main(['cluster', 'status', '--inventory', str(path), '--json']) == 0
    assert all(row['process'] == 'running' and row['health'] == 'healthy'
               for row in json.loads(capsys.readouterr().out)['results'])
    assert cli.main(['cluster', 'down', '--inventory', str(path), '--json']) == 0
    assert all(row['process'] == 'stopped' for row in json.loads(capsys.readouterr().out)['results'])
    # No launcher child inherited the lock after up, so down can acquire it.
    assert cli.main(['cluster', 'down', '--inventory', str(path), '--json']) == 0
    capsys.readouterr()
    assert cli.main(['cluster', 'status', '--inventory', str(path), '--json']) == 1
    assert all(row['process'] == 'stopped' for row in json.loads(capsys.readouterr().out)['results'])


@pytest.mark.parametrize('preexisting', [False, True])
def test_public_failed_start_preserves_preexisting_stack_and_rolls_back_new_stack(local_stack, monkeypatch, preexisting):
    path, inventory = local_stack
    target = lifecycle._targets(inventory)[0]
    request = {key: target[key] for key in ('config', 'role', 'node', 'cluster')}
    if preexisting:
        assert lifecycle.manage_host(request | {'action': 'up'})['ok']
    monkeypatch.setenv('FAKE_NODE_FAIL', '1')
    report, code = lifecycle.run(inventory, 'up', 2)
    assert code == 1 and report['results'][1]['issue'] == 'command_failed'
    config, _ = lifecycle.load_config(Path(target['config']))
    assert (lifecycle._identity(config, 'server') is not None) == preexisting
    assert bool(report['rollback']) == (not preexisting)


def test_worker_reports_contention_without_signalling_existing_process(tmp_path, monkeypatch):
    request = worker_request(tmp_path)
    state = tmp_path / 'telemetry/state/verl-local'
    state.mkdir(parents=True)
    with (state / 'lifecycle.lock').open('w') as locked:
        fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def forbidden(*args, **kwargs):
            pytest.fail('Busy lifecycle must not start or stop processes')
        monkeypatch.setattr(cli, '_launch', forbidden)
        assert lifecycle.manage_host(request)['issue'] == 'lock_busy'


def test_ssh_rejects_success_without_process_proof(tmp_path, monkeypatch):
    target = fake_ssh(tmp_path, monkeypatch, '''import json, sys
sys.stdin.read()
print(json.dumps({'ok': True, 'status': 'stopped', 'process': 'running'}))
''')
    assert lifecycle._ssh(target, 'down', 2)['issue'] == 'invalid_response'


def test_new_cluster_commands_are_in_generated_completion(capsys):
    assert cli.main(['completion', 'bash']) == 0
    completion = capsys.readouterr().out
    for action in ('up', 'status', 'down'):
        assert f"cluster|{action}" in completion
        assert f"cluster {action}|--inventory" in completion


def test_additional_explicit_server_is_managed_instead_of_silently_omitted(tmp_path, monkeypatch):
    path = inventory_file(tmp_path)
    data = json.loads(path.read_text())
    data['nodes'][1]['management']['server_config'] = '/srv/second-server.toml'
    path.write_text(json.dumps(data))
    calls = []
    def remote(target, action, timeout, **kwargs):
        calls.append((target['node'], target['role']))
        return reply(action)
    monkeypatch.setattr(lifecycle, '_ssh', remote)
    report, code = lifecycle.run(load_inventory(path), 'down', 1)
    assert code == 0 and len(report['results']) == 3
    assert calls == [('worker', 'node'), ('worker', 'server'), ('monitor', 'server')]


def test_worker_retains_new_identity_when_local_cleanup_cannot_finish(tmp_path, monkeypatch):
    request = worker_request(tmp_path)
    identities = iter([None, NEW, NEW])
    monkeypatch.setattr(lifecycle, '_identity', lambda *args: next(identities))
    def launch(config, action, role, **kwargs):
        if action == 'down':
            raise OSError('never-echo-secret')
        return 1
    monkeypatch.setattr(cli, '_launch', launch)
    result = lifecycle.manage_host(request)
    assert not result['ok'] and result['process'] == 'running' and result['started_identity'] == NEW
    assert 'never-echo-secret' not in json.dumps(result)
