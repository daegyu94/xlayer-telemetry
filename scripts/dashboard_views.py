"""Compose optional reading layouts from canonical dashboard panels.

Queries, units and evidence scope remain owned by the original dashboards.
Only presentation and navigation are changed here.
"""
from copy import deepcopy

VIEWS = (
    ('Guided', 'telemetry-overview'),
    ('Overview', 'xlayer-workspace-overview'),
    ('Focus', 'xlayer-workspace-focus'),
)


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
    for name in ('phase', 'role', 'worker', 'device', 'mount', 'storage_system', 'storage_node', 'ssd'):
        parameter = '${' + name + ':queryparam}'
        if name in present and parameter not in suffix:
            suffix += '&' + parameter
    dashboard['links'] = [dict(title='View: ' + name, type='link',
                               url=f'/d/{uid}?{suffix}', keepTime=True,
                               includeVars=False, targetBlank=False)
                          for name, uid in VIEWS] + dashboard.get('links', [])


def build_views(dashboards):
    by_name = {path.stem: dashboard for path, dashboard in dashboards.items()}
    stage = by_name['agent-rl-stages']
    sections = [
        ('VERL', 'agent-rl-stages', 4),
        ('vLLM', 'agent-rl-stages', 9),
        ('Ray', 'agent-rl-stages', 22),
        ('Compute', 'compute-communication', 2),
        ('Local storage', 'data-storage', 3),
        ('Sandbox', 'agent-rl-stages', 12),
    ]
    result = {}
    for style, uid in VIEWS[1:]:
        view = deepcopy(stage)
        view.update(uid=uid, title=f'Workspace · {style}', id=None, version=1,
                    description='선택 가능한 화면 구성입니다. 기존 query와 observation scope를 유지하며 Run·Node·시간 context로 상세 조사에 이동합니다.')
        # Union variables once. Preserve inactive selections while hiding controls
        # that do not affect this workspace's summary panels.
        variables = {v['name']: deepcopy(v) for v in stage['templating']['list']}
        for source in ('data-storage', 'compute-communication'):
            for variable in by_name[source]['templating']['list']:
                variables.setdefault(variable['name'], deepcopy(variable))
        for name, variable in variables.items():
            variable['hide'] = 0 if name in {'cluster', 'node', 'run_id', 'engine', 'sandbox_node', 'device'} else 2
        view['templating']['list'] = list(variables.values())
        view['links'] = [link for link in view['links'] if link['title'] in {
            'Start Here', 'Bottleneck Summary', 'Cross-Layer Timeline', 'Run Logs'}]
        view['annotations'] = {'list': []}
        view['panels'] = [{
            'id': 100, 'type': 'text', 'title': f'{style} workspace',
            'gridPos': {'x': 0, 'y': 0, 'w': 24, 'h': 3},
            'options': {'mode': 'markdown', 'content': (
                '**Overview:** subsystem을 한눈에 비교하고 panel의 **Open details**로 이동합니다.'
                if style == 'Overview' else
                '**Focus:** 원하는 subsystem row를 펼쳐 큰 graph로 확인합니다. Panel의 **Open details**에서 세부 지표를 봅니다.'
            ) + '\n\nView 버튼으로 화면을 바꿉니다. Run·Node·시간은 유지하며 shared metric은 Run에 자동 귀속하지 않습니다. 3FS 서비스 통계는 단독 `threefs` 조회를 사용합니다.'},
        }]
        y = 3
        for index, (title, source, panel_id) in enumerate(sections):
            original = by_name[source]
            panel = deepcopy(next(p for p in _panels(original['panels']) if p['id'] == panel_id))
            panel['id'] = index + 1
            panel['title'] = {
                'VERL': 'VERL · Stage duration', 'vLLM': 'vLLM · Queue & KV',
                'Ray': 'Ray · Task states', 'Compute': 'Compute · GPU utilization',
                'Local storage': 'Local storage · Device busy',
                'Sandbox': 'Sandbox · I/O pressure',
            }[title]
            link = deepcopy(next(link for link in stage['links']
                                 if link['url'].startswith('/d/' + original['uid'] + '?'))
                            if source != 'agent-rl-stages' else
                            next(link for link in by_name['run-overview']['links']
                                 if link['url'].startswith('/d/' + stage['uid'] + '?')))
            link['title'] = 'Open details'
            for name in ('phase', 'role', 'worker', 'device', 'gpu'):
                param = '${' + name + ':queryparam}'
                if param not in link['url']:
                    link['url'] += '&' + param
            panel['links'] = [link]
            if style == 'Overview':
                panel['gridPos'] = {'x': (index % 3) * 8, 'y': 3 + (index // 3) * 9, 'w': 8, 'h': 9}
                panel.setdefault('options', {})['legend'] = {'displayMode': 'list', 'placement': 'bottom'}
                view['panels'].append(panel)
            else:
                panel['gridPos'] = {'x': 0, 'y': y + 1, 'w': 24, 'h': 12}
                view['panels'].append({
                    'id': 200 + index, 'type': 'row', 'title': title,
                    'description': '이 subsystem을 펼쳐 자세히 확인합니다.',
                    'collapsed': index != 0,
                    'gridPos': {'x': 0, 'y': y, 'w': 24, 'h': 1},
                    'panels': [panel] if index != 0 else [],
                })
                if index == 0:
                    view['panels'].append(panel)
                    y += 12
                y += 1
        result[f'workspace-{style.lower()}.json'] = view
    return result
