"""Bounded SSH orchestration of the existing host-local monitoring lifecycle."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import stat
import subprocess
import sys
from tempfile import TemporaryFile
import time

from ..cluster_inventory import InventoryError, load_inventory
from .config import ConfigError, load_config
from .health import process_identity, status as observed_status

MAX_REPLY_BYTES = 64 * 1024
_ISSUES = {'command_failed', 'invalid_config', 'lock_busy', 'identity_changed',
           'transport_error', 'timeout', 'output_limit', 'invalid_response',
           'interrupted', 'not_attempted'}
_STATES = {'running', 'stopped', 'healthy', 'degraded', 'failed', 'unknown', 'not_attempted'}


class _Cancelled(KeyboardInterrupt):
    def __init__(self, signum):
        self.code = 128 + signum


def _failure(issue, *, status='unknown'):
    return {'ok': False, 'status': status, 'issue': issue}


def _valid_identity(value):
    return (isinstance(value, list) and len(value) == 3
            and type(value[0]) is int and value[0] > 1
            and type(value[1]) is int and value[1] > 0
            and isinstance(value[2], str)
            and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value[2]) is not None)


def _validate_reply(value, action):
    if (not isinstance(value, dict)
            or set(value) - {'ok', 'status', 'issue', 'process', 'health', 'started_identity'}
            or type(value.get('ok')) is not bool
            or not isinstance(value.get('status'), str) or value['status'] not in _STATES
            or ('issue' in value and value['issue'] not in _ISSUES)
            or ('process' in value and value['process'] not in {'running', 'stopped'})
            or ('health' in value and value['health'] not in {'healthy', 'degraded', 'stopped'})
            or (value.get('started_identity') is not None and not _valid_identity(value['started_identity']))):
        raise ValueError('Invalid remote lifecycle response')
    if (value.get('started_identity') is not None and (action != 'up' or value.get('process') != 'running')
            or value['ok'] and (value.get('process') != ('stopped' if action == 'down' else 'running')
                               or action == 'status' and value.get('health') != 'healthy')):
        raise ValueError('Inconsistent remote lifecycle response')
    return value


def _targets(inventory):
    monitoring = inventory['cluster'].get('monitoring_node')
    if not monitoring:
        raise InventoryError('cluster.monitoring_node is required for lifecycle management')
    targets = []
    for node in inventory['nodes']:
        management = node.get('management', {})
        roles = (['server'] if node['name'] == monitoring or management.get('server_config') else []) + (['node'] if node['collector'] else [])
        for role in roles:
            key = role + '_config'
            if not management.get('ssh') or not management.get(key):
                raise InventoryError(f'node {node["name"]} requires management.ssh and management.{key}')
            targets.append({'node': node['name'], 'role': role, 'management': management,
                            'config': management[key], 'cluster': inventory['cluster']['name']})
    return sorted(targets, key=lambda row: (row['role'] != 'server', row['node']))


def _ssh(target, action, timeout, *, expected_identity=None):
    management = target['management']
    remote = shlex.join([management['python'], '-m', 'xlayer_telemetry.operations.cluster_lifecycle'])
    request = {key: target[key] for key in ('config', 'role', 'node', 'cluster')}
    request['action'] = action
    if expected_identity is not None:
        request['expected_identity'] = expected_identity
    argv = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=10', '-o', 'ConnectionAttempts=1',
            '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=1',
            '--', management['ssh'], remote]
    child = None
    try:
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + timeout
        child.stdin.write(json.dumps(request).encode() + b'\n')
        child.stdin.close()
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return _failure('timeout')
                for key, _ in selector.select(remaining):
                    body = os.read(key.fd, 4096)
                    if not body:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(body)
                        if len(output) > MAX_REPLY_BYTES:
                            return _failure('output_limit')
        code = child.wait(timeout=max(.001, deadline - time.monotonic()))
        if code:
            return _failure('transport_error')
        return _validate_reply(json.loads(output), action)
    except subprocess.TimeoutExpired:
        return _failure('timeout')
    except (OSError, ValueError, TypeError, RecursionError):
        return _failure('invalid_response' if child is not None and child.poll() == 0 else 'transport_error')
    finally:
        if child is not None:
            if child.poll() is None:
                # Reap only this SSH session; remote completion can remain unknown.
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait(timeout=2)
            for stream in (child.stdin, child.stdout):
                if stream is not None:
                    stream.close()


def _identity(config, role):
    path = Path(config['TELEMETRY_HOME']) / 'state/verl-local' / (role + '.pid')
    if process_identity(path)['process'] != 'running':
        return None
    fields = path.read_text().split()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return [int(fields[0]), int(fields[1]), fields[2] if len(fields) == 3 else boot]


def manage_host(request):
    """Guard new-start receipts and rollback with the launcher's same flock."""
    from ..cli import _launch
    from ..cluster_inventory import _home, _identifier

    if (not isinstance(request, dict) or set(request) - {'action', 'config', 'role', 'node', 'cluster', 'expected_identity'}
            or not isinstance(request.get('action'), str) or request['action'] not in {'up', 'down', 'status'}
            or not isinstance(request.get('role'), str) or request['role'] not in {'server', 'node'}
            or ('expected_identity' in request and
                (request['action'] != 'down' or not _valid_identity(request['expected_identity'])))):
        return _failure('invalid_config', status='failed')
    mutation_attempted = False
    try:
        _identifier(request.get('cluster'), 'cluster')
        _identifier(request.get('node'), 'node')
        path = Path(_home(request.get('config'))).expanduser()
        config, _ = load_config(path)
        role, action = request['role'], request['action']
        if config['CLUSTER_NAME'] != request['cluster'] or config['NODE_NAME'] != request['node']:
            return _failure('invalid_config', status='failed')
        if action == 'status':
            observed = observed_status(config, role=role)
            return {'ok': observed['status'] == 'healthy', 'status': observed['status'],
                    'process': observed['services'][role]['process'],
                    'health': observed['status']}
        state = Path(config['TELEMETRY_HOME']) / 'state/verl-local'
        state.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(state / 'lifecycle.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return _failure('invalid_config', status='failed')
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return _failure('lock_busy', status='failed')
            before = _identity(config, role)
            if 'expected_identity' in request and before != request['expected_identity']:
                return _failure('identity_changed', status='failed')
            with TemporaryFile() as output:
                mutation_attempted = True
                try:
                    code = _launch(config, action, role, lock_fd=descriptor, output=output)
                except (OSError, ValueError, RuntimeError):
                    code = 1
                after = _identity(config, role)
                started = after if action == 'up' and before is None else None
                ok = code == 0 and (after is not None if action == 'up' else after is None)
                if not ok and started is not None:
                    try:
                        _launch(config, 'down', role, lock_fd=descriptor, output=output)
                    except (OSError, ValueError, RuntimeError):
                        pass
                    after = _identity(config, role)
                    if after is None:
                        started = None
            reply = {'ok': ok, 'status': 'running' if ok and after else 'stopped' if ok else 'failed',
                     'process': 'running' if after else 'stopped', 'started_identity': started}
            if not ok:
                reply['issue'] = 'command_failed'
            return reply
        finally:
            os.close(descriptor)
    except (OSError, ValueError, KeyError, RuntimeError):
        return _failure('command_failed' if mutation_attempted else 'invalid_config',
                        status='unknown' if mutation_attempted else 'failed')


def run(inventory, action, timeout):
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ConfigError('--timeout must be an integer in 1..600 seconds')
    targets = _targets(inventory)
    if action == 'down':
        targets.reverse()
    results, started = [], []
    failed, cancelled = False, 0
    for index, target in enumerate(targets):
        try:
            response = _ssh(target, action, timeout)
        except _Cancelled as error:
            response, cancelled = _failure('interrupted'), error.code
        row = {key: target[key] for key in ('node', 'role')}
        row.update(response)
        identity = row.pop('started_identity', None)
        row['started'] = identity is not None
        results.append(row)
        if identity is not None:
            started.append((target, identity))
        if not response['ok']:
            failed = True
            if action == 'up' or cancelled:
                for remaining in targets[index + 1:]:
                    results.append({'node': remaining['node'], 'role': remaining['role'],
                                    **_failure('not_attempted', status='not_attempted'), 'started': False})
                break
    rollback = []
    if action == 'up' and failed:
        for target, identity in reversed(started):
            reply = _ssh(target, 'down', timeout, expected_identity=identity)
            reply.pop('started_identity', None)
            rollback.append({'node': target['node'], 'role': target['role'], **reply})
    unknown = any(row['status'] == 'unknown' for row in results) or any(not row['ok'] for row in rollback)
    report = {'schema_version': 1, 'cluster': inventory['cluster']['name'], 'action': action,
              'status': 'interrupted' if cancelled else 'partial_failure' if failed else 'ok',
              'results': results, 'rollback': rollback,
              'cleanup': 'unconfirmed' if action == 'up' and failed and unknown else 'completed' if rollback else 'not_needed',
              'scope': 'inventory_managed_roles'}
    return report, cancelled or int(failed)


def execute(args):
    inventory = load_inventory(args.inventory)
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    def cancel(signum, frame):
        raise _Cancelled(signum)
    try:
        for sig in previous:
            signal.signal(sig, cancel)
        report, code = run(inventory, args.cluster_action, args.timeout)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Cluster {report['cluster']} {report['action']}: {report['status']}")
        for row in report['results']:
            print(f"{row['node']} {row['role']}: {row['status']}" + (f" ({row['issue']})" if row.get('issue') else ''))
        for row in report['rollback']:
            print(f"Rollback {row['node']} {row['role']}: {row['status']}" + (f" ({row['issue']})" if row.get('issue') else ''))
        if report['cleanup'] == 'unconfirmed':
            print('Cleanup is unconfirmed; reconnect and run cluster status/down with this inventory.')
    return code


def main():
    try:
        body = sys.stdin.buffer.read(8193)
        if len(body) > 8192:
            raise ValueError('Request too large')
        reply = manage_host(json.loads(body))
    except (OSError, ValueError, TypeError, RecursionError):
        reply = _failure('invalid_config', status='failed')
    print(json.dumps(reply))


if __name__ == '__main__':
    main()
