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


def native_dashboard_back(page, origin):
    """Exercise a mounted dashboard before traversing its bounded URL history."""
    from urllib.parse import urlparse
    page.wait_for_function("new URLSearchParams(location.search).has('orgId')", timeout=15000)
    for entries in range(1,5):
        page.go_back(wait_until='domcontentloaded')
        current = urlparse(page.url)
        assert current.netloc == urlparse(origin).netloc, 'Back left the owned Grafana origin'
        if current.path.startswith('/a/xlayer-telemetry-app'):
            page.get_by_role('navigation', name='XLayer investigation').wait_for(timeout=30000)
            assert urlparse(page.url).path.startswith('/a/xlayer-telemetry-app'), 'App mount changed the return route'
            return entries
        assert current.path.startswith('/d/'), 'Back left the native dashboard history'
    raise AssertionError('App entry absent from the last four native dashboard history entries')


def bounded_tail(path, max_bytes=65536):
    with path.open('rb') as source:
        source.seek(0, 2)
        source.seek(max(0, source.tell() - max_bytes))
        return source.read(max_bytes).decode('utf-8', errors='replace')


def wait_for_fixtures(process, log, state, deadline, comparisons=2, *, multi_job=False):
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Owned live demo exited before completing fixtures')
        if (state / 'connection.json').is_file() and bounded_tail(log).count('COMPLETED MULTI-JOB ' if multi_job else 'COMPLETED Step ') >= comparisons:
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
                                       '--output', str(args.state), '--grafana-port', str(port)]
            if not args.multi_job:
                demo_command.append('--storage-series')
            if args.multi_worker:
                demo_command.append('--multi-worker')
            if args.multi_job:
                demo_command.append('--multi-job')
            process = subprocess.Popen(demo_command,
                                       cwd=ROOT, stdout=dest, stderr=subprocess.STDOUT, start_new_session=True)
            connection = wait_for_fixtures(process, launcher_log, args.state, time.monotonic() + args.ready_timeout, args.completed_comparisons, multi_job=args.multi_job)
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
            infrastructure = [sys.executable,str(SCRIPTS/'infrastructure_validate.py'),'--url',connection['grafana'],
                '--context',str(args.output/('ci-multi-validation.json' if args.multi_worker else 'ci-validation.json')),
                '--output',str(args.output/'infrastructure'),*(['--browser',args.browser] if args.browser else [])]
            validators = (command, contracts, series, clocks, infrastructure)
            if not args.multi_worker and not args.multi_job:
                # Reuse the same owned stack; cover all routes/themes without
                # adding another demo launch or repeating multi-job diagnosis.
                design = [sys.executable, str(SCRIPTS / 'design_validate.py'), '--url', connection['grafana'],
                    '--context',str(args.output/'ci-validation.json'), '--output',str(args.output/'design'),
                    '--iteration','1','--mockup',str(SCRIPTS.parent/'design/dashboard-preview.html'), *(['--browser',args.browser] if args.browser else [])]
                readability = [sys.executable,str(SCRIPTS/'readability_validate.py'),'--url',connection['grafana'],
                    '--context',str(args.output/'ci-validation.json'),'--output',str(args.output/'readability'),
                    '--iteration','1','--screens','infrastructure,investigate,bottleneck-summary,signals',
                    '--mockup',str(SCRIPTS.parent/'design/topology-compact.html'),'--require-contrast',
                    *(['--browser',args.browser] if args.browser else [])]
                validators += (design,readability)
                # Reuse this stack for saved-artifact/retention comparisons.
                explorer=ROOT/'examples/investigation/validate_app_ux.py'
                subprocess.run([sys.executable,str(explorer),'--fixture-root',str(args.state/'saved-run-fixtures'),
                    '--dashboard-output',str(args.state/'dashboards')],cwd=ROOT,check=True,timeout=20)
                validators += ([sys.executable,str(explorer),'--url',connection['grafana'],'--output',str(args.output/'runs'),
                    *(['--browser',args.browser] if args.browser else [])],)
            if args.multi_job:
                validators = ([sys.executable, str(SCRIPTS / 'multi_job_validate.py'), '--url', connection['grafana'],
                    '--state', str(args.state), '--output', str(args.output / 'multi-job'),
                    *(['--browser', args.browser] if args.browser else [])],)
            for validation_command in validators:
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
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--multi-job', action='store_true', help='Actual three-job Collect/Query/Diagnose browser journey')
    modes.add_argument('--multi-worker', action='store_true', help='Explicit opt-in four-worker SDK/demo browser journey')
    parser.add_argument('--completed-comparisons', type=int, choices=(1,2), default=2, help='Actual completed baseline/current pairs to await; default validates both scenarios')
    parser.add_argument('--ready-timeout', type=int, default=240)
    parser.add_argument('--browser-timeout', type=int, default=180)
    args = parser.parse_args()
    if not 1 <= args.ready_timeout <= 360 or not 1 <= args.browser_timeout <= 300:
        parser.error('Readiness deadline must be 1..360s; browser deadline 1..300s')
    run(args)


if __name__ == '__main__':
    main()
