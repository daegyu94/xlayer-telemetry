"""Read-only admission checks across existing configuration contracts."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from .config import ConfigError, config_path, load_config
from .health import status as observed_status


def configuration_report(config: dict[str, str], *, live: bool = False,
                         correlation: bool = False, declared_nodes: set[str] | None = None) -> dict:
    from ..topology_validation import inspect_topology
    from ..source_discovery import load_file_discovery
    issues: list[dict] = []
    def issue(severity, code, location, message):
        issues.append(dict(severity=severity, code=code, location=location, message=message))

    nodes = {entry.split('=',1)[0] for entry in config.get('TELEMETRY_TARGETS','').split(',') if '=' in entry}
    topology = {'status':'not_configured','counts':{'components':0,'edges':0},'issues':[]}
    if config.get('TOPOLOGY_DIR'):
        topology = inspect_topology(Path(config['TOPOLOGY_DIR']), registered_nodes=declared_nodes or nodes or None,
                                    cluster=config.get('CLUSTER_NAME'))
        issues.extend(topology['issues'])
        if declared_nodes is not None:
            for node in sorted(declared_nodes):
                if node not in nodes:
                    issue('warning', 'configured_node_not_monitored', 'topology',
                          'Inventory에 선언된 resource node의 collector가 등록되지 않았습니다. 실제 관측은 확인되지 않았습니다.')
    groups = []
    if config.get('TELEMETRY_SOURCES_FILE'):
        try:
            groups = load_file_discovery(Path(config['TELEMETRY_SOURCES_FILE']))
        except (OSError, ValueError, TypeError) as error:
            issue('error','invalid_native_sources','TELEMETRY_SOURCES_FILE','Native source 설정을 읽거나 검증할 수 없습니다.')
            issues[-1]['error_type'] = type(error).__name__
        for index, group in enumerate(groups):
            labels = group['labels']; node = labels.get('node') or labels.get('nodename')
            if labels.get('node') and labels.get('nodename') and labels['node'] != labels['nodename']:
                issue('error','source_node_identity_conflict',f'sources[{index}]','node와 nodename이 서로 다릅니다.')
            if not node:
                issue('warning','source_node_not_declared',f'sources[{index}]','Native endpoint의 node 관계가 선언되지 않았습니다. Resource 소유권은 추정하지 않습니다.')
            elif nodes and node not in nodes:
                issue('warning','source_node_not_registered',f'sources[{index}]','Native source node의 collector가 등록되지 않았습니다. Endpoint 수집과 host metric coverage는 별도입니다.')
            if labels.get('cluster') and labels['cluster'] != config.get('CLUSTER_NAME'):
                issue('warning','source_cluster_overridden',f'sources[{index}]','Server의 CLUSTER_NAME이 native source의 cluster label을 덮어씁니다.')
    diagnosis = 'not_configured'
    if config.get('DIAGNOSTICS_CONFIG'):
        try:
            from ..analysis.diagnostics import load_config
            from ..analysis.clock_quality import clock_inventory
            settings = load_config(Path(config['DIAGNOSTICS_CONFIG']))
            diagnosis = 'validated'
            if settings.get('cluster') and settings['cluster'] != config.get('CLUSTER_NAME'):
                issue('error','diagnosis_cluster_mismatch','DIAGNOSTICS_CONFIG.cluster','Diagnosis와 monitoring cluster가 서로 다릅니다.')
            inventory = clock_inventory(settings, settings.get('node') or config['NODE_NAME'])
            for node in inventory['nodes']:
                if nodes and node not in nodes:
                    issue('warning','diagnosis_node_not_registered','DIAGNOSTICS_CONFIG','Diagnosis/clock observation node의 collector가 등록되지 않았습니다. 실제 관측은 확인되지 않았습니다.')
        except (OSError, ValueError, TypeError, KeyError) as error:
            diagnosis = 'invalid'
            issue('error','invalid_diagnosis_config','DIAGNOSTICS_CONFIG','Diagnosis 설정을 읽거나 검증할 수 없습니다.')
            issues[-1]['error_type'] = type(error).__name__
    observations: str | dict = 'not_checked'
    if live:
        observed = observed_status(config, role='server')
        observations = {key:observed.get(key) for key in ('target_discovery','collector_targets','native_sources')}
        observations['metric_coverage'] = 'not_checked'
        observations['device_identity'] = 'not_checked'
        observations['interpretation'] = 'Scrape availability만 확인합니다. Metric 존재·freshness·device mapping·serving 상태·physical connectivity를 증명하지 않습니다.'
        if observed.get('target_discovery',{}).get('status') == 'unavailable':
            issue('warning','live_discovery_unavailable','prometheus','Target 상태를 조회할 수 없습니다. Node Down으로 판정하지 않습니다.')
        for target in observed.get('collector_targets',[]):
            if target.get('health') != 'up':
                issue('warning','collector_observation_incomplete','collector_targets','등록된 collector의 scrape 상태를 확인하세요. 이 결과로 node 제거·hardware 장애를 단정하지 않습니다.')
        for index, source in enumerate(observed.get('native_sources', {}).get('sources', [])):
            if source.get('status') != 'up':
                issue('warning', 'native_observation_incomplete', f'native_sources.sources[{index}]',
                      '등록된 Native endpoint의 scrape 상태를 확인하세요. Source 내부 상태나 누락된 metric을 정상으로 추정하지 않습니다.')
    clock = None
    if correlation:
        from .health import correlation_preflight
        try:
            clock = correlation_preflight(config)
        except (OSError, ValueError, TypeError, KeyError):
            clock = {'status': 'invalid_config', 'system_time_changed': False}
        if clock['status'] != 'pass':
            issue('warning','clock_preflight_incomplete','doctor --correlation','관측한 clock 품질 또는 inventory가 정밀 correlation 조건을 충족하지 않습니다.')
    result = 'invalid' if any(i['severity']=='error' for i in issues) else 'needs_attention' if issues else 'valid'
    return {'status':result,'mode':'live' if live or correlation else 'offline','cluster':config.get('CLUSTER_NAME'),
            'counts':{'collector_nodes':len(nodes),'topology_components':topology.get('counts',{}).get('components',0),
                      'topology_edges':topology.get('counts',{}).get('edges',0),'native_endpoints':len(groups)},
            'topology':topology,'diagnosis_config':diagnosis,'issues':issues,'observations':observations,
            'clock_preflight':clock,'system_time_changed':False,
            'interpretation':'설정의 정합성과 실제 관측 상태를 분리합니다. 정적 구성은 execution relation, resource attribution 또는 causality가 아닙니다.'}


def print_report(report: dict) -> None:
    print(f"Cluster Configuration Validation: {report['status']} · {report['mode']}")
    for name,count in report.get('counts',{}).items():
        print(f"[CONFIGURED] {name}: {count}")
    for item in report.get('issues',[]):
        print(f"[{item['severity'].upper()}] {item['code']} · {item.get('location',item.get('path',''))}\n  {item['message']}")
    print(report.get('interpretation',''))


def execute_cluster(args) -> int:
    from ..cluster_inventory import InventoryError
    try:
        return _execute_cluster(args)
    except InventoryError as error:
        raise ConfigError(f'Invalid cluster inventory: {error}') from error


def _execute_cluster(args) -> int:
    """Keep inventory rendering independent from service installation/lifecycle."""
    from ..cluster_inventory import load_inventory, render_inventory

    if args.cluster_action == 'render':
        manifest = render_inventory(args.inventory, args.output)
        if args.json:
            print(json.dumps(manifest, indent=2, ensure_ascii=False))
        else:
            output = args.output.expanduser().absolute()
            print(f'Created private configuration bundle: {output}')
            print('설정은 생성만 했습니다. Server·node 설정을 각 host에 배치한 뒤 별도로 시작하세요.')
            for note in manifest['deployment_notes']:
                print(f'- {note}')
        return 0

    if args.inventory:
        inventory = load_inventory(args.inventory)
        # The same readers used by an installed deployment validate the bundle.
        # This private temporary output is never activated or copied to user state.
        with TemporaryDirectory(prefix='xltel-inventory-check-') as temporary:
            output = Path(temporary) / 'bundle'
            manifest = render_inventory(args.inventory, output)
            generated, _ = load_config(output / 'server.toml')
            if args.config or os.environ.get('XLAYER_CONFIG'):
                config, _ = load_config(config_path(args.config))
            else:
                config = generated
            # --inventory explicitly selects the static declarations being
            # checked; unrelated exported runtime values must not replace them.
            for key in ('CLUSTER_NAME', 'TELEMETRY_TARGETS', 'TOPOLOGY_DIR', 'TELEMETRY_SOURCES_FILE'):
                config.pop(key, None)
                if key in manifest['server_config']:
                    config[key] = manifest['server_config'][key]
            report = configuration_report(config, live=args.live, correlation=args.correlation,
                                          declared_nodes={node['name'] for node in inventory['nodes']})
            report['inventory'] = {'scope': 'configured_inventory', 'counts': manifest['counts'],
                                   'diagnosis_generated': False}
    else:
        config, _ = load_config(config_path(args.config))
        report = configuration_report(config, live=args.live, correlation=args.correlation)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_report(report)
    return 2 if report['status'] == 'invalid' else 1 if report['status'] == 'needs_attention' else 0
