"""Compose optional reading layouts from canonical dashboard panels.

Canonical panels own queries, units and evidence scope. Summary projections
select existing series; ranking keeps entity labels and the original rate window.
"""
from copy import deepcopy
import re
from urllib.parse import urlencode

VIEWS = (
    ('Run Overview', 'telemetry-overview'),
    ('Cross-Layer Signals', 'xlayer-workspace-overview'),
)


def expose_active_filters(dashboard):
    """Keep navigation-only context hidden; expose controls that change data."""
    queries = '\n'.join(target.get('expr', '')
                        for panel in _panels(dashboard['panels'])
                        for target in panel.get('targets', []))
    used = set(re.findall(r'\$([a-z][a-z0-9_]*)\b', queries))
    for variable in dashboard['templating']['list']:
        if variable['name'] in used:
            variable['hide'] = 0


def add_reset_link(dashboard):
    """Clear restrictions without discarding the selected run, cluster or time."""
    preserved = {'cluster', 'run_id', 'telemetry_run_id', 'log_run_id'}
    context = []
    cleared = {}
    for variable in dashboard['templating']['list']:
        name = variable['name']
        if name in preserved:
            context.append('${' + name + ':queryparam}')
        elif variable.get('includeAll'):
            cleared['var-' + name] = '$__all'
        elif variable.get('type') == 'textbox':
            cleared['var-' + name] = variable.get('query', '.*')
    query = '&'.join(context + [urlencode(cleared)])
    dashboard['links'].append(dict(
        title='Reset filters', type='link', url=f'/d/{dashboard["uid"]}?{query}',
        tooltip='Run·Cluster·시간을 유지하고 Step·Trace·resource 필터를 초기화합니다.',
        keepTime=True, includeVars=False, targetBlank=False,
    ))


def _panels(items):
    for panel in items:
        yield panel
        yield from _panels(panel.get('panels', []))


def add_view_links(dashboard):
    """Reuse the existing Run Overview link's context, including log run mapping."""
    reference = next((link for link in dashboard.get('links', [])
                      if link.get('title') in ('Run Overview', 'Start Here')), None)
    if reference is None:
        raise ValueError(f'Missing investigation navigation: {dashboard["uid"]}')
    suffix = reference['url'].split('?', 1)[1]
    # Preserve view-specific filters on the new views without inventing variables
    # in canonical dashboards that do not use those filters.
    present = {v['name'] for v in dashboard['templating']['list']}
    for name in ('phase', 'role', 'worker', 'device', 'mount', 'storage_system', 'storage_node'):
        parameter = '${' + name + ':queryparam}'
        if name in present and parameter not in suffix:
            suffix += '&' + parameter
    dashboard['links'] = [dict(title=name, type='link',
                               url=f'/d/{uid}?{suffix}', keepTime=True,
                               includeVars=False, targetBlank=False)
                          for name, uid in VIEWS if uid != dashboard['uid']] + [
                              link for link in dashboard.get('links', [])
                              if link['title'] != 'Run Overview']


def build_views(dashboards):
    """A single cross-layer workspace; detailed rows replace the former Focus view."""
    by_name = {path.stem: dashboard for path, dashboard in dashboards.items()}
    stage = by_name['agent-rl-stages']
    view = deepcopy(stage)
    view.update(uid='xlayer-workspace-overview', title='Cross-Layer Signals',
                id=None, version=1,
                description='Run의 증상과 shared resource를 같은 시간으로 비교합니다. 실제 dependency·traffic map이나 Run attribution이 아닙니다.')
    variables = {v['name']: deepcopy(v) for v in stage['templating']['list']}
    for source in ('run-overview', 'data-storage', 'compute-communication'):
        for variable in by_name[source]['templating']['list']:
            variables.setdefault(variable['name'], deepcopy(variable))
    for variable in variables.values():
        variable['hide'] = 2
    for name in ('cluster', 'run_id', 'node'):
        variables[name]['hide'] = 0
    order = {'cluster': 0, 'run_id': 1, 'source_node': 2, 'node': 3}
    view['templating']['list'] = sorted(variables.values(), key=lambda v: order.get(v['name'], 4))
    view['links'] = [link for link in view['links'] if link['title'] in {
        'Start Here', 'Run Overview', 'Bottleneck Summary', 'Cross-Layer Timeline', 'Run Logs'}]
    view['annotations'] = {'list': []}
    view['time']['from'] = 'now-30m'
    view['tags'] = [tag for tag in view.get('tags', []) if tag != 'xlayer-subsystem']
    view['panels'] = [{
        'id': 100, 'type': 'text', 'title': 'Cross-layer investigation',
        'transparent': True, 'gridPos': {'x': 0, 'y': 0, 'w': 24, 'h': 3},
        'options': {'mode': 'markdown', 'content':
            '**${run_id:text}** · 같은 시간의 workload와 shared resource를 비교합니다.\n\n'
            'Graph 클릭 → subsystem. No data는 0이 아닙니다. Shared signal은 Run attribution이 아니며 3FS는 기존 ClickHouse evidence에서 확인합니다.'},
    }]

    def clone(source, panel_id, title, new_id):
        original = by_name[source]
        panel = deepcopy(next(p for p in _panels(original['panels']) if p['id'] == panel_id))
        panel.update(id=new_id, title=title)
        reference = next(link for link in by_name['run-overview']['links']
                         if link['url'].startswith('/d/' + original['uid'] + '?')) if source != 'run-overview' else dict(
                             title='Run Overview', url=next(link['url'] for link in stage['links']
                                                          if link['title'] == 'Run Overview'))
        link = deepcopy(reference)
        link.update(title='Open details', targetBlank=False)
        for name in ('phase', 'role', 'worker', 'device', 'gpu'):
            param = '${' + name + ':queryparam}'
            if param not in link['url']:
                link['url'] += '&' + param
        panel['links'] = [link]
        # Keep entity-specific links (GPU/device) ahead of the generic pivot.
        panel['fieldConfig']['defaults'].setdefault('links', []).append(
            dict(link, title='Open subsystem in this time range'))
        panel['options']['legend'] = {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True}
        return panel

    def row(id, title, y, children=()):
        return {'id': id, 'type': 'row', 'title': title,
                'description': '같은 시각의 signal을 비교합니다. No data는 source 미설정·범위에 표본 없음일 수 있으며 0이나 정상 상태가 아닙니다.',
                'collapsed': bool(children), 'gridPos': {'x': 0, 'y': y, 'w': 24, 'h': 1},
                'panels': list(children)}

    application = clone('run-overview', 7, 'Workload · Step duration / worker', 1)
    queue = clone('agent-rl-stages', 9, 'Serving · Waiting & running requests', 2)
    queue['targets'] = [t for t in queue['targets'] if t['refId'] in {'A', 'D'}]
    queue['fieldConfig']['overrides'] = []
    cache = clone('agent-rl-stages', 9, 'Serving · KV cache usage / engine', 3)
    cache['targets'] = [t for t in cache['targets'] if t['refId'] == 'B']
    cache['fieldConfig']['defaults'].update(unit='percentunit', min=0, max=1)
    cache['fieldConfig']['overrides'] = []
    ray = clone('agent-rl-stages', 22, 'Orchestration · Ray task states / session', 4)
    gpu = clone('compute-communication', 2, 'Shared device · GPU utilization', 5)
    storage = clone('data-storage', 3, 'Shared device · Top 8 local I/O busy', 6)
    # topk keeps the entire original entity label set; no cross-device summation.
    storage['targets'] = [dict(t, expr='topk(8, ' + t['expr'] + ')', instant=True)
                          for t in storage['targets']]
    storage['type'] = 'bargauge'
    storage['fieldConfig']['defaults']['links'].insert(0, {
        'title': 'Inspect this node / device', 'targetBlank': False,
        'url': '/d/xlayer-data-storage?var-cluster=${__field.labels.cluster}'
               '&var-node=${__field.labels.nodename}&var-device=${__field.labels.device}'
               '&${run_id:queryparam}&${source_node:queryparam}&${record_id:queryparam}'
               '&${trace_id:queryparam}&${diagnosis_method:queryparam}&${__url_time_range}'
               '&${log_run_id:queryparam}&${gpu:queryparam}&${engine:queryparam}'
               '&${sandbox_node:queryparam}',
    })
    storage['fieldConfig']['defaults']['color'] = {'mode': 'fixed', 'fixedColor': 'blue'}
    storage['fieldConfig']['defaults']['max'] = 1
    storage['options'] = {'orientation': 'horizontal', 'displayMode': 'basic',
                          'showUnfilled': False, 'valueMode': 'text', 'reduceOptions': {'values': True, 'fields': '', 'calcs': ['last']}}
    storage['description'] += ' 선택한 시간 구간 끝의 rate 상위 8개이며 p99 latency나 포화 판정이 아닙니다. Node·device identity를 유지합니다.'
    sandbox = clone('agent-rl-stages', 12, 'Sandbox · Worker pressure & local device', 7)
    view['panels'].append(row(200, 'Workload & serving · reported steps / shared endpoints', 3))
    for index, panel in enumerate((application, queue, cache, ray)):
        panel['gridPos'] = {'x': index % 2 * 12, 'y': 4 + index // 2 * 8, 'w': 12, 'h': 8}
        view['panels'].append(panel)
    view['panels'].append(row(201, 'Shared resources · sampled node / device signals', 20))
    for index, panel in enumerate((gpu, storage)):
        panel['gridPos'] = {'x': index * 12, 'y': 21, 'w': 12, 'h': 8}
        view['panels'].append(panel)
    sandbox['gridPos'] = {'x': 0, 'y': 30, 'w': 24, 'h': 10}
    view['panels'].append(row(202, 'Sandbox · cgroup and local device (optional)', 29, [sandbox]))
    return {'workspace-overview.json': view}


def add_event_annotations(dashboard):
    """Only recorded EventRecorder points/starts; never infer phase boundaries."""
    if dashboard['uid'] not in {'telemetry-overview', 'agent-rl-stage-correlation',
                                'xlayer-workspace-overview', 'xlayer-cross-layer-timeline'}:
        return
    dashboard.setdefault('annotations', {}).setdefault('list', []).append({
        'name': 'Recorded events / operation starts',
        'datasource': {'type': 'loki', 'uid': 'telemetry-loki'},
        'enable': True, 'hide': False, 'iconColor': '#5794f2', 'maxLines': 100,
        'expr': '{signal="xlayer_event",cluster=~"$cluster",node=~"$source_node"} '
                '| json | run_id=~"$run_id" '
                '| record_type=~"event|span" | boundary_accuracy!="unknown" '
                '| event_time_unix_nano!="" | __error__="" '
                '| line_format "{{.name}} · step {{.step}} · {{.record_type}} '
                '{{if .boundary_accuracy}}{{.boundary_accuracy}}{{else}}node clock{{end}} '
                '{{if .time_uncertainty_seconds}}±{{.time_uncertainty_seconds}}s{{end}} '
                '· {{.node}}/{{.worker_id}} · {{.attributes}}"',
    })
    if dashboard['uid'] == 'xlayer-cross-layer-timeline':
        annotation = dashboard['annotations']['list'][-1]
        annotation['expr'] = annotation['expr'].replace(
            '| record_type=', '| trace_id=~"$trace_id" | record_type=')


def compact_navigation(dashboard):
    """Native dashboard dropdown keeps detailed destinations out of the main path."""
    subsystem_titles = {'Agent RL Stage Correlation', 'Compute & Communication', 'Data & Storage'}
    if not any(link['title'] in subsystem_titles for link in dashboard['links']):
        return
    dashboard['links'] = [link for link in dashboard['links'] if link['title'] not in subsystem_titles]
    dashboard['links'].insert(-1, {
        'title': 'Subsystems', 'type': 'dashboards', 'tags': ['xlayer-subsystem'],
        'asDropdown': True, 'includeVars': True, 'keepTime': True,
        'targetBlank': False, 'tooltip': 'Run·observer/resource·record·시간을 유지해 subsystem 상세로 이동합니다.',
    })


def build_run_investigation(dashboards):
    """Put recorded spans and scoped serving/device KPIs on the run landing page."""
    by_name = {path.stem: dashboard for path, dashboard in dashboards.items()}
    run = by_name['run-overview']
    stage = by_name['agent-rl-stages']
    compute = by_name['compute-communication']
    timeline = by_name['cross-layer-timeline']
    originals = list(run['panels'])
    # Make space after the workload KPIs. No shared value becomes a run metric.
    for panel in originals:
        if panel['gridPos']['y'] >= 7:
            panel['gridPos']['y'] += 10
            for child in panel.get('panels', []):
                child['gridPos']['y'] += 10
    section = {
        'id': 94, 'type': 'row', 'title': 'Related serving & devices · shared snapshots',
        'description': '선택한 시간 끝의 shared endpoint/device 값입니다. Run의 소유량·원인이나 workload health가 아닙니다.',
        'collapsed': False, 'gridPos': {'x': 0, 'y': 7, 'w': 24, 'h': 1}, 'panels': [],
    }
    cards = []
    for index, (dashboard, panel_id, ref_id, title, unit) in enumerate((
        (stage, 21, 'A', 'Serving · highest TTFT p95 / engine', 's'),
        (compute, 2, 'A', 'GPU · busiest sampled device', 'percent'),
        (stage, 9, 'B', 'Serving · highest KV use / engine', 'percentunit'),
    )):
        source = next(p for p in _panels(dashboard['panels']) if p['id'] == panel_id)
        card = deepcopy(source)
        card.update(id=32 + index, type='stat', title=title,
                    gridPos={'x': index * 8, 'y': 8, 'w': 8, 'h': 4})
        card['targets'] = [dict(t, instant=True) for t in source['targets'] if t['refId'] == ref_id]
        if dashboard is compute:
            card['targets'][0]['expr'] = 'topk(1, ' + card['targets'][0]['expr'] + ')'
            card['description'] += ' 높은 값을 가진 fresh device 한 개의 identity를 보존합니다. 모든 GPU의 평균이나 Run utilization이 아닙니다.'
            card['fieldConfig']['defaults']['displayName'] = '${__field.labels.nodename} / GPU ${__field.labels.gpu}'
        else:
            card['targets'][0]['expr'] = 'topk(1, ' + card['targets'][0]['expr'] + ')'
            card['description'] += ' 선택한 engine 중 높은 값 한 개를 identity와 함께 표시합니다. Run별 사용량이나 전체 engine 평균이 아닙니다.'
            card['fieldConfig']['defaults']['displayName'] = '${__field.labels.node} / ${__field.labels.instance} / engine ${__field.labels.engine}'
        card['fieldConfig']['defaults'].update(unit=unit, noValue='N/A')
        card['fieldConfig']['defaults'].setdefault('mappings', []).append({
            'type': 'special', 'options': {'match': 'nan', 'result': {'text': 'N/A', 'color': 'gray'}},
        })
        card['fieldConfig']['defaults'].pop('custom', None)
        card['fieldConfig']['overrides'] = []
        card['options'] = {
            'reduceOptions': {'calcs': ['last'], 'fields': '', 'values': False},
            'colorMode': 'none', 'graphMode': 'none', 'textMode': 'value_and_name',
            'text': {'titleSize': 14, 'valueSize': 36}, 'wideLayout': True,
        }
        link = deepcopy(next(link for link in run['links'] if link['url'].startswith('/d/' + dashboard['uid'] + '?')))
        link['title'] = 'Inspect serving / device'
        card['links'] = [link]
        if dashboard is stage:
            entity_link = deepcopy(link)
            entity_link['title'] = 'Inspect this engine'
            entity_link['url'] = (f'/d/{stage["uid"]}?var-cluster=${{__field.labels.cluster}}'
                                  '&var-node=${__field.labels.node}&var-engine=${__field.labels.instance}'
                                  '&${run_id:queryparam}&${source_node:queryparam}&${record_id:queryparam}'
                                  '&${trace_id:queryparam}&${diagnosis_method:queryparam}&${__url_time_range}'
                                  '&${log_run_id:queryparam}&${gpu:queryparam}&${sandbox_node:queryparam}')
            card['fieldConfig']['defaults'].setdefault('links', []).append(entity_link)
        else:
            card['fieldConfig']['defaults'].setdefault('links', []).append(link)
        cards.append(card)
    spans = deepcopy(next(p for p in timeline['panels'] if p['id'] == 2))
    spans.update(id=35, title='Agent RL timeline · recorded exact / calibrated spans',
                 gridPos={'x': 0, 'y': 12, 'w': 24, 'h': 5})
    spans['description'] += ' 완료 stage duration을 실행 순서로 변환하지 않습니다. Span 미계측이면 No data이며 Step·Stage 링크에서 approximate boundary를 확인합니다.'
    link = deepcopy(next(link for link in run['links'] if link['title'] == 'Cross-Layer Timeline'))
    link['title'] = 'Open timeline, event records and related metrics'
    spans['links'] = [link]
    steps = next(p for p in originals if p['id'] == 20)
    steps['gridPos'] = {'x': 0, 'y': 17, 'w': 24, 'h': 8}
    trends = [p for p in originals if p['id'] in {6, 7}]
    for panel in trends:
        panel['gridPos']['y'] = 25
    rest = [p for p in originals[5:] if p['id'] not in {6, 7, 20}]
    run['panels'] = originals[:5] + [section] + cards + [spans, steps] + trends + rest
    # Query-derived controls stay visible, including filters received in URLs.
    for variable in stage['templating']['list']:
        if variable['name'] == 'engine':
            for current in run['templating']['list']:
                if current['name'] == 'engine':
                    current.update(deepcopy(variable))
                    break
