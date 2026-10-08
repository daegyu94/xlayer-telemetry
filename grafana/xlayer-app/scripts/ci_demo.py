#!/usr/bin/env python3
"""Run the actual Grafana browser journey against an owned, bounded live demo."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = Path(__file__).resolve().parent


def datasource_error_result(queries, message='Synthetic browser boundary datasource error'):
    """Grafana query-result error fixture; not a real backend outage."""
    return {'results': {str(query.get('refId', 'A')): {
        'status': 503, 'error': message, 'errorSource': 'downstream', 'frames': []
    } for query in queries}}


def bounded_tail(path, max_bytes=65536):
    with path.open('rb') as source:
        source.seek(0, 2)
        source.seek(max(0, source.tell() - max_bytes))
        return source.read(max_bytes).decode('utf-8', errors='replace')


def wait_for_fixtures(process, log, state, deadline, comparisons=2):
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Owned live demo exited before completing fixtures')
        if (state / 'connection.json').is_file() and bounded_tail(log).count('COMPLETED Step ') >= comparisons:
            return json.loads((state / 'connection.json').read_text())
        # Bounded process wait, rather than a busy-poll or fixed sleep loop.
        try:
            process.wait(timeout=min(1, max(.01, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass
    raise TimeoutError('Live demo did not produce the requested completed comparisons before deadline')


def stop_owned(process, whole_group=False):
    if process.poll() is not None:
        return
    if whole_group:
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.wait(timeout=40)
    except subprocess.TimeoutExpired:
        # Created with start_new_session: the group contains only this demo's children.
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def run(args):
    args.output = args.output.resolve()
    args.state = args.state.resolve()
    if args.state.exists():
        raise ValueError('--state must be a new disposable directory')
    args.output.mkdir(parents=True, exist_ok=True)
    launcher_log = args.output / 'live-demo.log'
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    process = None
    try:
        with launcher_log.open('w') as dest:
            demo_command = [sys.executable, str(SCRIPTS / 'live_demo.py'), '--tools', str(args.tools.resolve()),
                            '--output', str(args.state), '--grafana-port', str(port), '--storage-series']
            if args.multi_worker:
                demo_command.append('--multi-worker')
            process = subprocess.Popen(demo_command,
                                       cwd=ROOT, stdout=dest, stderr=subprocess.STDOUT, start_new_session=True)
            connection = wait_for_fixtures(process, launcher_log, args.state, time.monotonic() + args.ready_timeout, args.completed_comparisons)
            command = [sys.executable, str(SCRIPTS / 'browser_validate.py'), '--url', connection['grafana'],
                       '--output', str(args.output), '--label', 'ci-multi' if args.multi_worker else 'ci']
            if args.multi_worker:
                command.append('--multi-worker')
            if args.browser:
                command.extend(['--browser', args.browser])
            contracts = [sys.executable, str(SCRIPTS / 'metric_contract_validate.py'),
                         '--url', connection['grafana'], '--output', str(args.output / 'metric-contracts')]
            if args.browser:
                contracts.extend(['--browser', args.browser])
            series = [sys.executable, str(SCRIPTS / 'storage_series_validate.py'),
                      '--url', connection['grafana'], '--output', str(args.output / 'storage-series')]
            if args.browser:
                series.extend(['--browser', args.browser])
            clocks = [sys.executable, str(SCRIPTS / 'clock_quality_validate.py'),
                      '--url', connection['grafana'], '--output', str(args.output / 'clock-quality')]
            if args.browser:
                clocks.extend(['--browser', args.browser])
            versions = [sys.executable, str(SCRIPTS / 'versions_validate.py'),
                        '--url', connection['grafana'], '--output', str(args.output / 'versions')]
            if args.browser:
                versions.extend(['--browser', args.browser])
            design = [sys.executable, str(SCRIPTS / 'workspace_design_validate.py'),
                      '--url', connection['grafana'], '--output', str(args.output / 'workspace-design')]
            if args.browser:
                design.extend(['--browser', args.browser])
            for validation_command in (command, contracts, series, clocks, versions, design):
                validator = subprocess.Popen(validation_command, cwd=ROOT, start_new_session=True)
                try:
                    code = validator.wait(timeout=args.browser_timeout)
                    if code:
                        raise subprocess.CalledProcessError(code, validation_command)
                finally:
                    # A timed-out browser must not leave owned Chromium processes behind.
                    stop_owned(validator, whole_group=True)
    finally:
        if process is not None:
            stop_owned(process)
        # Keep only bounded synthetic service tails in the upload directory.
        for name in ('exporter', 'prometheus', 'loki', 'grafana'):
            source = args.state / (name + '.log')
            if source.is_file():
                (args.output / (name + '-tail.log')).write_text('\n'.join(bounded_tail(source).splitlines()[-150:]) + '\n')
    print('CI journey passed; owned Grafana/Prometheus/Loki/exporter stopped.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser', default=None)
    parser.add_argument('--multi-worker', action='store_true', help='Explicit opt-in four-worker SDK/demo browser journey')
    parser.add_argument('--completed-comparisons', type=int, choices=(1,2), default=2, help='Actual completed baseline/current pairs to await; default validates both scenarios')
    parser.add_argument('--ready-timeout', type=int, default=240)
    parser.add_argument('--browser-timeout', type=int, default=180)
    args = parser.parse_args()
    if not 1 <= args.ready_timeout <= 360 or not 1 <= args.browser_timeout <= 300:
        parser.error('Readiness deadline must be 1..360s; browser deadline 1..300s')
    run(args)


if __name__ == '__main__':
    main()
