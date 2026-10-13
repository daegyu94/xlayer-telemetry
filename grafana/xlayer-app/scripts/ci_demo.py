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


def verify_loki_all_filters(page, origin):
    """Exercise actual Loki label lookup before selecting any concrete node."""
    from urllib.parse import urlparse
    with page.expect_response(lambda response: urlparse(response.url).path.endswith('/label/workload/values'), timeout=30000) as observed:
        page.goto(origin+'/a/xlayer-telemetry-app/logs?var-cluster=$__all&var-node=$__all')
        page.locator('.xlt').wait_for(state='visible', timeout=30000)
    assert observed.value.status == 200, f'All-filter Loki workload lookup returned HTTP {observed.value.status}'


def verify_workspace_navigation(page, origin, context, capture=None):
    """Check workspace ownership and context through the actual Scenes router."""
    from urllib.parse import urlencode, urlparse, parse_qs
    from playwright.sync_api import expect
    from design_validate import CONTEXT_KEYS, context_changes
    base=origin+'/a/xlayer-telemetry-app/'
    overview=base+'overview?'+urlencode(context,doseq=True)
    page.goto(overview)
    run_tabs=page.get_by_role('navigation',name='Run views')
    expect(run_tabs).to_be_visible(timeout=30000)
    before=parse_qs(urlparse(page.url).query)
    preserved={key:before[key] for key in context if key in before and (key.startswith('var-') or key in {'from','to','timezone','theme'})}
    sidebar=page.locator('.xlt-sidebar')

    def check(area):
        expect(sidebar.locator('a[aria-current=page]')).to_have_count(1)
        expect(sidebar.get_by_role('link',name=area,exact=True)).to_have_attribute('aria-current','page')
        current=parse_qs(urlparse(page.url).query)
        changes=context_changes(preserved,current)
        changes.update({key:{'before':value,'after':current.get(key)} for key,value in preserved.items() if key not in CONTEXT_KEYS and current.get(key)!=value})
        assert not changes, changes
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')

    assert sidebar.get_by_role('navigation').locator('a span').all_text_contents()==['Runs','Infrastructure','Dashboards']
    check('Runs')
    demo=sidebar.locator('.xlt-demo-link')
    expect(demo).to_be_visible()
    assert demo.evaluate('(element)=>element.getBoundingClientRect().bottom<innerHeight*.7'), 'Scenario Explorer is too far below the primary navigation'
    assert run_tabs.locator('a span').all_text_contents()==['Overview','Investigation']
    if capture:capture('workspace-overview')
    run_tabs.get_by_role('link',name='Investigation',exact=True).click()
    page.wait_for_url('**/investigate?**')
    investigation=page.get_by_role('navigation',name='Investigation views')
    expect(investigation).to_be_visible()
    check('Runs')
    investigation.get_by_role('link',name='Timeline',exact=True).click()
    page.wait_for_url('**/timeline?**')
    check('Runs')
    page.go_back();page.wait_for_url('**/investigate?**');check('Runs')
    page.go_forward();page.wait_for_url('**/timeline?**');check('Runs')
    page.reload();expect(run_tabs).to_be_visible(timeout=30000);check('Runs')
    page.locator('.xlt-run-navigation').get_by_role('link',name='← Runs',exact=True).click()
    page.wait_for_url('**/runs?**');check('Runs')
    sidebar.get_by_role('link',name='Dashboards',exact=True).click()
    page.wait_for_url('**/dashboards?**')
    directory=page.get_by_role('region',name='Dashboard library')
    expect(directory.locator('a')).to_have_count(6)
    check('Dashboards')
    if capture:capture('workspace-dashboards')
    for title in ['Collection Status','Stage & Serving Signals','Bottleneck Summary','Compute & Network','Storage & I/O','Cross-Layer Signals']:
        directory.get_by_role('link').filter(has=page.get_by_role('heading',name=title,exact=True)).click()
        expect(page.get_by_role('heading',name=title,exact=True).first).to_be_visible(timeout=30000)
        check('Dashboards')
        sidebar.get_by_role('link',name='Dashboards',exact=True).click()
        page.wait_for_url('**/dashboards?**')
    page.reload();expect(directory.locator('a')).to_have_count(6);check('Dashboards')

    def no_topology(route):
        queries=route.request.post_data_json.get('queries',[])
        route.fulfill(json={'results':{query['refId']:{'status':200,'frames':[]} for query in queries}})
    page.route('**/api/ds/query',no_topology)
    try:
        sidebar.get_by_role('link',name='Infrastructure',exact=True).click()
        page.wait_for_url('**/infrastructure?**')
        expect(page.get_by_text('이 시간 범위에 topology inventory가 없습니다.',exact=False)).to_be_visible(timeout=30000)
        check('Infrastructure')
        if capture:capture('workspace-infrastructure-no-topology')
        page.get_by_role('link',name='Collection Status →',exact=True).click()
        page.wait_for_url('**/start-here?**');check('Dashboards')
    finally:
        page.unroute('**/api/ds/query',no_topology)
    page.goto(overview)
    expect(run_tabs).to_be_visible(timeout=30000)


def verify_run_sources(page, origin, context):
    """Landing panels and topology drill-down use native queries and URL context."""
    from urllib.parse import urlencode, urlparse, parse_qs
    from playwright.sync_api import expect
    def training_request(request):
        return '/api/ds/query' in request.url and any('training_step_time_seconds' in query.get('expr','') for query in (request.post_data_json or {}).get('queries',[]))
    with page.expect_request(training_request,timeout=30000) as requested:
        page.goto(origin+'/a/xlayer-telemetry-app/runs?'+urlencode(context,doseq=True))
    training_queries=[query['expr'] for query in requested.value.post_data_json['queries'] if 'training_step_time_seconds' in query.get('expr','')]
    assert all(expr.startswith('avg by (cluster, run_id, node, producer, role) (') for expr in training_queries),training_queries
    cards=page.locator('.xlt-run-source')
    expect(cards).to_have_count(5)
    assert cards.locator('header h4').all_text_contents()==['VERL','vLLM','Ray','Mooncake','3FS']
    assert page.get_by_label('GPU signals').count()==0
    assert page.get_by_label('VERL signals').locator('.xlt-panel').count()==2
    assert page.get_by_label('vLLM signals').locator('.xlt-panel').count()==2
    assert page.get_by_label('Mooncake signals').locator('.xlt-panel').count()==2
    expect(page.get_by_role('heading',name='Cluster Infrastructure',exact=True)).to_be_visible()
    expect(page.get_by_role('button',name='Expanded',exact=True)).to_have_count(0)
    expect(page.get_by_role('button',name='Compact',exact=True)).to_have_count(0)
    expect(page.get_by_role('heading',name='Run inventory',exact=True)).to_be_visible()
    node=page.locator('[aria-label="Cluster infrastructure"] .xlt-cluster-map-node').first
    expect(node).to_be_visible(timeout=30000)
    viewport=page.viewport_size
    try:
        for width in (1440,1920):
            page.set_viewport_size({'width':width,'height':1000})
            geometry=page.locator('.xlt-infra-preview').evaluate('''el => ({
                workspace: el.clientWidth,
                map: el.querySelector('.xlt-cluster-map').getBoundingClientRect().width,
                body: el.querySelector('.xlt-cluster-map-body').getBoundingClientRect().width
            })''')
            assert abs(geometry['workspace']-geometry['map'])<=2,geometry
            assert 30<=geometry['map']-geometry['body']<=45,geometry
    finally:
        if viewport:page.set_viewport_size(viewport)
    identity=node.get_attribute('data-resource-key')
    node.click()
    page.wait_for_url('**/infrastructure?**')
    current=parse_qs(urlparse(page.url).query)
    assert current['var-infra_component']==[identity]
    for key in ('var-run_id','var-source_node','from','to','timezone','theme'):
        if key in context:assert current.get(key)==context[key],key
    expect(page.get_by_text('Resource details',exact=True)).to_be_visible()
    more=page.locator('button.xlt-cluster-map-more')
    if more.count():
        before=page.locator('.xlt-cluster-map-node').count()
        more.first.click()
        search=page.get_by_role('textbox',name='Search infrastructure inventory')
        expect(search).to_be_focused()
        expect(search).to_have_value('')
        expect(page.locator('.xlt-cluster-map-node')).to_have_count(before)
    page.go_back()
    page.wait_for_url('**/runs?**')
    expect(page.locator('.xlt-run-source')).to_have_count(5)


def assert_policy_lifecycle_text(text):
    """Check disclosures without treating capitalization/wrapping as semantics."""
    normalized = ' '.join(text.casefold().split())
    for term in ('producer reported', 'causality'):
        assert term in normalized, f'Policy / KV Lifecycle missing disclosure: {term}'


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
            if args.app_package:
                demo_command.extend(['--app-package',str(args.app_package.resolve())])
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
                      '--url', connection['grafana'], '--output', str(args.output / 'storage-series'),
                      '--delay-storage-ms', '10000']
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
            validators += ([sys.executable,str(SCRIPTS/'redesign_validate.py'),'--url',connection['grafana'],
                '--output',str(args.output/'product-journey'),*(['--browser',args.browser] if args.browser else [])],)
            validators += ([sys.executable,str(SCRIPTS/'cached_deep_dive_validate.py'),'--url',connection['grafana'],
                '--output',str(args.output/'cached-deep-dive'),*(['--browser',args.browser] if args.browser else [])],)
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
                validators += ([sys.executable,str(SCRIPTS/'legend_filter_validate.py'),'--url',connection['grafana'],
                    '--output',str(args.output/'legends'),*(['--browser',args.browser] if args.browser else [])],)
                validators += ([sys.executable,str(SCRIPTS/'cluster_steps_validate.py'),'--state',str(args.state),
                    '--output',str(args.output/'cluster-steps'),*(['--browser',args.browser] if args.browser else [])],)
                # Reuse this stack for saved-artifact/retention comparisons.
                explorer=ROOT/'examples/investigation/validate_app_ux.py'
                subprocess.run([sys.executable,str(explorer),'--fixture-root',str(args.state/'saved-run-fixtures'),
                    '--dashboard-output',str(args.state/'dashboards')],cwd=ROOT,check=True,timeout=20)
                validators += ([sys.executable,str(explorer),'--url',connection['grafana'],'--output',str(args.output/'runs'),
                    *(['--browser',args.browser] if args.browser else [])],)
                validators += ([sys.executable,str(SCRIPTS/'release_review_validate.py'),'--url',connection['grafana'],
                    '--output',str(args.output/'release-review'),*(['--browser',args.browser] if args.browser else [])],)
                validators += ([sys.executable,str(SCRIPTS/'context_recovery_validate.py'),'--url',connection['grafana'],
                    '--output',str(args.output/'context-recovery'),*(['--browser',args.browser] if args.browser else [])],)
                validators += ([sys.executable,str(SCRIPTS/'quality_validate.py'),'--url',connection['grafana'],
                    '--context',str(args.output/'ci-validation.json'),'--output',str(args.output/'quality'),
                    *(['--browser',args.browser] if args.browser else [])],)
                validators += ([sys.executable,str(SCRIPTS/'llm_invocation_validate.py'),'--url',connection['grafana'],
                    '--state',str(args.state),'--context',str(args.output/'ci-validation.json'),
                    '--output',str(args.output/'llm-invocations'),*(['--browser',args.browser] if args.browser else [])],)
            if args.multi_job:
                validators = ([sys.executable, str(SCRIPTS / 'multi_job_validate.py'), '--url', connection['grafana'],
                    '--state', str(args.state), '--output', str(args.output / 'multi-job'),
                    *(['--browser', args.browser] if args.browser else [])],)
            if not args.multi_job:
                # Publish the isolated network fixture last so it cannot alter
                # earlier run-catalog/count assertions in this owned stack.
                validators += ([sys.executable,str(SCRIPTS/'user_workflows_validate.py'),'--url',connection['grafana'],
                    '--state',str(args.state),'--output',str(args.output/'user-workflows'),
                    *(['--browser',args.browser] if args.browser else [])],)
            if not args.multi_job and not args.multi_worker:
                validators += ([sys.executable,str(SCRIPTS/'matrix_context_validate.py'),'--url',connection['grafana'],
                    '--state',str(args.state),'--output',str(args.output/'matrix-context'),
                    *(['--browser',args.browser] if args.browser else [])],)
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
    parser.add_argument('--app-package',type=Path,help='Verified package for an isolated build without modifying checkout dist')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--multi-job', action='store_true', help='Actual three-job Collect/Query/Diagnose browser journey')
    modes.add_argument('--multi-worker', action='store_true', help='Explicit opt-in four-worker SDK/demo browser journey')
    parser.add_argument('--completed-comparisons', type=int, choices=(1,2), default=2, help='Actual completed baseline/current pairs to await; default validates both scenarios')
    parser.add_argument('--ready-timeout', type=int, default=300)
    parser.add_argument('--browser-timeout', type=int, default=180)
    args = parser.parse_args()
    if not 1 <= args.ready_timeout <= 360 or not 1 <= args.browser_timeout <= 300:
        parser.error('Readiness deadline must be 1..360s; browser deadline 1..300s')
    run(args)


if __name__ == '__main__':
    main()
