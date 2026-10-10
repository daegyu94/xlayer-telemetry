"""Compile operator inventory into existing XLayer configuration assets.

Inventory describes configured resources and hosting, not discovery, serving
state, request routing, run placement, resource ownership or clock validation.
Compilation performs no network requests and launches no processes.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any, Mapping
from urllib.parse import urlsplit

from .source_discovery import build_file_discovery


MAX_CONFIG_BYTES = 1024 * 1024
MAX_NODES = 256
MAX_SERVICES = 1024
MAX_COMPONENTS = 4096  # Per existing compute/storage namespace.
MAX_EDGES = 8192
_ID = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
_RESOURCE = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')
_COMPONENT = re.compile(r'^[A-Za-z0-9_.:/-]{1,128}$')
_STORAGE_ROLES = {'storage', 'storage-node', 'mds', 'metadata', 'ds', 'data'}
_DYNAMIC_LABELS = {
    'runid', 'job', 'jobid', 'model', 'modelid', 'modelname', 'replica',
    'replicaid', 'replicarank', 'workload', 'worker', 'workerid', 'rank',
    'step', 'phase', 'policyversion', 'generation', 'generationid',
    'rolloutid', 'sandboxid', 'containerid', 'trajectoryid', 'requestid',
    'promptid', 'traceid', 'spanid', 'swebenchinstanceid',
    'password', 'token', 'secret', 'apikey', 'authorization',
}


class InventoryError(ValueError):
    """Actionable validation message containing no unvalidated input values."""


def _object(value: Any, allowed: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise InventoryError(f'{where} must be an object')
    extra = set(value) - allowed
    if extra:
        raise InventoryError(f'{where} contains unsupported fields; check the inventory contract')
    return dict(value)


def _identifier(value: Any, where: str, pattern=_ID) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value) or value in ('.', '..'):
        raise InventoryError(f'{where} must be a bounded stable identifier')
    return value


def _list(value: Any, where: str, limit: int, *, required=False) -> list[Any]:
    if not isinstance(value, list) or len(value) > limit or (required and not value):
        raise InventoryError(f'{where} must be a list with ' + (f'1..{limit}' if required else f'at most {limit}') + ' entries')
    return value


def _address(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 253:
        raise InventoryError('node address must be an IPv4 address or hostname without credentials or port')
    if re.fullmatch(r'[0-9.]+', value):
        try:
            ipaddress.IPv4Address(value)
            return value
        except ipaddress.AddressValueError as error:
            raise InventoryError('node address is not a valid IPv4 address') from error
    if not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?', label)
               for label in value.split('.')):
        raise InventoryError('node address must be an IPv4 address or hostname without credentials or port')
    return value


def _resources(value: Any, where: str, limit: int, *, gpu=False) -> list[str]:
    normalized = []
    for item in _list(value, where, limit):
        if gpu and type(item) is int and 0 <= item <= 4095:
            item = str(item)
        elif gpu and isinstance(item, str) and item.isdigit():
            if not 0 <= int(item) <= 4095:
                raise InventoryError(f'{where} GPU index is outside 0..4095')
            item = str(int(item))
        item = _identifier(item, where, _RESOURCE)
        if item in normalized:
            raise InventoryError(f'{where} contains duplicate resource IDs')
        normalized.append(item)
    return sorted(normalized)


def _home(value: Any) -> str:
    if (not isinstance(value, str) or len(value) > 1024 or any(ord(char) < 32 for char in value)
            or not (value.startswith('~/') or Path(value).is_absolute())):
        raise InventoryError('node telemetry_home must be absolute or ~/relative-to-the-receiving-host')
    return value


def _normalize_inventory(document: Mapping[str, Any]) -> dict[str, Any]:
    data = _object(document, {'schema_version', 'cluster', 'nodes', 'services', 'components', 'edges', 'storage'}, 'inventory')
    if type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise InventoryError('Unsupported inventory schema_version')
    cluster = _object(data.get('cluster'), {'name', 'monitoring_node'}, 'cluster')
    cluster['name'] = _identifier(cluster.get('name'), 'cluster.name')
    nodes, names, addresses = [], set(), set()
    for raw in _list(data.get('nodes'), 'nodes', MAX_NODES, required=True):
        node = _object(raw, {'name', 'address', 'roles', 'gpus', 'interfaces', 'devices', 'collector', 'telemetry_home'}, 'node')
        name = _identifier(node.get('name'), 'node.name')
        address = _address(node.get('address'))
        if name in names or address.lower() in addresses:
            raise InventoryError('Duplicate node name or address; register each collector once')
        names.add(name)
        addresses.add(address.lower())
        roles = [_identifier(role, f'node {name} roles') for role in _list(node.get('roles'), 'node.roles', 16, required=True)]
        if len(set(roles)) != len(roles):
            raise InventoryError('node.roles contains duplicates')
        if type(node.get('collector', True)) is not bool:
            raise InventoryError('node.collector must be boolean')
        normalized = {'name': name, 'address': address, 'roles': sorted(roles),
            'collector': node.get('collector', True),
            'gpus': _resources(node.get('gpus', []), f'node {name} gpus', 64, gpu=True),
            'interfaces': _resources(node.get('interfaces', []), f'node {name} interfaces', 64),
            'devices': _resources(node.get('devices', []), f'node {name} devices', 256)}
        if 'telemetry_home' in node:
            normalized['telemetry_home'] = _home(node['telemetry_home'])
        nodes.append(normalized)
    if not any(node['collector'] for node in nodes):
        raise InventoryError('Existing server configuration requires at least one collector-enabled node')
    if 'monitoring_node' in cluster:
        cluster['monitoring_node'] = _identifier(cluster['monitoring_node'], 'cluster.monitoring_node')
        if cluster['monitoring_node'] not in names:
            raise InventoryError('cluster.monitoring_node must reference a registered node')

    services = []
    for raw in _list(data.get('services', []), 'services', MAX_SERVICES):
        service = _object(raw, {'name', 'kind', 'node', 'target', 'scheme', 'path', 'metrics_path', 'labels'}, 'service')
        name = _identifier(service.get('name'), 'service.name')
        _identifier(service.get('kind'), 'service.kind')
        node = service.get('node')
        if node not in names:
            raise InventoryError('service.node must reference a registered inventory node')
        if 'path' in service and 'metrics_path' in service and service['path'] != service['metrics_path']:
            raise InventoryError('service.path and metrics_path conflict')
        target = service.get('target')
        if not isinstance(target, str) or '@' in target or '://' in target or any(char.isspace() for char in target):
            raise InventoryError('service.target must be host:port without embedded credentials')
        path = service.get('metrics_path', service.get('path', '/metrics'))
        if not isinstance(path, str) or any(char in path for char in '?#'):
            raise InventoryError('service metrics_path must be a path without query or fragment')
        labels = service.get('labels', {})
        if not isinstance(labels, dict):
            raise InventoryError('service.labels must be an object')
        if any(not isinstance(key, str) or re.sub(r'[_-]', '', key).lower() in _DYNAMIC_LABELS for key in labels):
            raise InventoryError('Dynamic execution/model or credential labels do not belong in cluster inventory')
        if 'node' in labels and labels['node'] != node:
            raise InventoryError('service label node conflicts with its declared host')
        if 'nodename' in labels and labels['nodename'] != node:
            raise InventoryError('service label nodename conflicts with its declared host')
        if 'cluster' in labels and labels['cluster'] != cluster['name']:
            raise InventoryError('service label cluster conflicts with its inventory')
        services.append({'name': name, 'kind': service['kind'], 'target': target,
            'scheme': service.get('scheme', 'http'), 'metrics_path': path,
            'node': node, 'labels': {**labels, 'node': node}})
    # Reuse native endpoint, duplicate exporter and label-budget checks.
    if services:
        try:
            build_file_discovery({'schema_version': 1, 'sources': services})
        except ValueError as error:
            raise InventoryError('Invalid native source contract; check scheme, port, path, labels and duplicate scrape endpoints') from error

    components = []
    for raw in _list(data.get('components', []), 'components', MAX_COMPONENTS * 2):
        component = _object(raw, {'kind', 'id', 'role', 'resource_node', 'gpu', 'interface', 'device', 'storage_system'}, 'component')
        if component.get('kind') not in ('compute', 'storage'):
            raise InventoryError('component.kind must be compute or storage')
        component['id'] = _identifier(component.get('id'), 'component.id', _COMPONENT)
        component['role'] = _identifier(component.get('role'), 'component.role', _RESOURCE)
        for key in ('resource_node', 'gpu', 'interface', 'device', 'storage_system'):
            if key in component:
                component[key] = _identifier(component[key], f'component.{key}', _RESOURCE)
        if component.get('resource_node') and component['resource_node'] not in names:
            raise InventoryError('component.resource_node must reference a registered node')
        if sum(key in component for key in ('gpu', 'interface', 'device')) > 1:
            raise InventoryError('component must identify one GPU, interface or device')
        if any(key in component for key in ('gpu', 'interface', 'device')) and 'resource_node' not in component:
            raise InventoryError('device components require an explicit resource_node')
        components.append(component)
    edges = []
    for raw in _list(data.get('edges', []), 'edges', MAX_EDGES * 2):
        edge = _object(raw, {'kind', 'source', 'destination', 'relation'}, 'edge')
        if edge.get('kind') not in ('compute', 'storage'):
            raise InventoryError('edge.kind must be compute or storage')
        for key in ('source', 'destination'):
            edge[key] = _identifier(edge.get(key), f'edge.{key}', _COMPONENT)
        edge['relation'] = _identifier(edge.get('relation'), 'edge.relation', _RESOURCE)
        edges.append(edge)
    storage = _object(data.get('storage', {}), {'backend', 'filesystem'}, 'storage')
    if 'backend' in storage:
        storage['backend'] = _identifier(storage['backend'], 'storage.backend')
    if 'filesystem' in storage:
        value = storage['filesystem']
        if not isinstance(value, str) or not value or len(value) > 1024 or any(ord(char) < 32 for char in value):
            raise InventoryError('storage.filesystem must be a bounded descriptive string')
    return {'schema_version': 1, 'cluster': cluster, 'nodes': sorted(nodes, key=lambda value: value['name']),
        'services': sorted(services, key=lambda value: value['name']),
        'components': sorted(components, key=lambda value: (value['kind'], value['id'])),
        'edges': sorted(edges, key=lambda value: (value['kind'], value['source'], value['destination'], value['relation'])),
        'storage': storage}


def _unique_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InventoryError('Inventory JSON contains a duplicate field')
        result[key] = value
    return result


def load_inventory(path: Path) -> dict[str, Any]:
    path = Path(path)
    if path.suffix.lower() not in ('.toml', '.json'):
        raise InventoryError('Inventory supports TOML or JSON, without YAML dependencies')
    if not stat.S_ISREG(path.stat().st_mode):
        raise InventoryError('Inventory must be a regular TOML/JSON file')
    with path.open('rb') as stream:
        body = stream.read(MAX_CONFIG_BYTES + 1)
    if len(body) > MAX_CONFIG_BYTES:
        raise InventoryError('Cluster inventory exceeds the 1 MiB limit')
    try:
        if path.suffix.lower() == '.json':
            document = json.loads(body, object_pairs_hook=_unique_json_pairs)
        else:
            try:
                import tomllib
            except ImportError:  # Existing conditional runtime dependency on Python 3.10.
                import tomli as tomllib
            document = tomllib.loads(body.decode('utf-8'))
    except InventoryError:
        raise
    except (ValueError, UnicodeError, RecursionError) as error:
        raise InventoryError('Inventory is not valid TOML/JSON; check syntax and nesting') from error
    return _normalize_inventory(document)


def compile_inventory(document: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalize_inventory(document)
    topology = {kind: {'components': [], 'edges': []} for kind in ('compute', 'storage')}
    by_kind: dict[str, dict[str, dict[str, str]]] = {'compute': {}, 'storage': {}}
    edge_sets = {'compute': set(), 'storage': set()}
    unmonitored = {node['name'] for node in normalized['nodes'] if not node['collector']}
    def component(kind, value):
        # Retain the configured component, but do not drive metric drill-down
        # through an owner whose collector is explicitly excluded. The source
        # inventory and node manifest retain the original declaration.
        if value.get('resource_node') in unmonitored:
            value = {key: item for key, item in value.items()
                     if key not in ('resource_node', 'gpu', 'interface', 'device', 'storage_system')}
        if value.get('role') == 'fabric':
            value = {**value, 'role': 'network'}
        _identifier(value['id'], 'generated component.id', _COMPONENT)
        previous = by_kind[kind].get(value['id'])
        if previous is not None and previous != value:
            raise InventoryError('Conflicting component identity or resource mapping in one namespace')
        by_kind[kind][value['id']] = value
        if len(by_kind[kind]) > MAX_COMPONENTS:
            raise InventoryError(f'Generated {kind} topology exceeds component limits; split inventory')
    def edge(kind, source, destination, relation):
        edge_sets[kind].add((source, destination, relation))
        if len(edge_sets[kind]) > MAX_EDGES:
            raise InventoryError(f'Generated {kind} topology exceeds edge limits; split inventory')
    for node in normalized['nodes']:
        storage_roles = set(node['roles']) & _STORAGE_ROLES
        compute_roles = set(node['roles']) - _STORAGE_ROLES
        kinds = (['storage'] if storage_roles else []) + (['compute'] if compute_roles or node['gpus'] or not storage_roles else [])
        for kind in kinds:
            if kind == 'storage':
                metadata, data = bool(storage_roles & {'mds', 'metadata'}), bool(storage_roles & {'ds', 'data'})
                role = 'metadata' if metadata and not data else 'data' if data and not metadata else 'storage-node'
            else:
                role = 'gpu-node' if node['gpus'] else 'compute-node'
            component(kind, {'id': node['name'], 'role': role, 'resource_node': node['name']})
        primary = 'compute' if 'compute' in kinds else 'storage'
        for field, role, resources in (('gpu', 'gpu', node['gpus']),
                                       ('interface', 'nic', node['interfaces']), ('device', 'ssd', node['devices'])):
            for resource in resources:
                identifier = f"{node['name']}/{role}-{resource}"
                component(primary, {'id': identifier, 'role': role, 'resource_node': node['name'], field: resource})
                edge(primary, node['name'], identifier, 'attached')
    for raw in normalized['components']:
        component(raw['kind'], {key: value for key, value in raw.items() if key != 'kind'})
    for raw in normalized['edges']:
        edge(raw['kind'], raw['source'], raw['destination'], raw['relation'])
    for kind in topology:
        if len(by_kind[kind]) > MAX_COMPONENTS or len(edge_sets[kind]) > MAX_EDGES:
            raise InventoryError(f'Generated {kind} topology exceeds component/edge limits; split inventory')
        for source, destination, _ in edge_sets[kind]:
            if source not in by_kind[kind] or destination not in by_kind[kind]:
                raise InventoryError('Topology edge endpoint is not declared in its namespace')
        topology[kind] = {'components': [by_kind[kind][key] for key in sorted(by_kind[kind])],
            'edges': [{'source': source, 'destination': destination, 'relation': relation}
                      for source, destination, relation in sorted(edge_sets[kind])]}
    native = [{'name': value['name'], 'kind': value['kind'], 'target': value['target'],
               'scheme': value['scheme'], 'metrics_path': value['metrics_path'], 'labels': value['labels']}
              for value in normalized['services']]
    return {**normalized,
        'telemetry_targets': ','.join(f"{node['name']}={node['address']}" for node in normalized['nodes'] if node['collector']),
        'topology': topology, 'native_sources': {'schema_version': 1, 'sources': native} if native else None}


def _json_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def _toml(values: Mapping[str, Any], note: str) -> bytes:
    lines = ['# ' + note, '[telemetry]']
    for key, value in sorted(values.items()):
        encoded = 'true' if value is True else 'false' if value is False else json.dumps(value, ensure_ascii=False)
        lines.append(f'{key} = {encoded}')
    return ('\n'.join(lines) + '\n').encode('utf-8')


def _write_private(path: Path, body: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(body)


def render_inventory(path: Path, output: Path, prometheus_url: str | None = None) -> dict[str, Any]:
    """Write a new private bundle; never merge with existing user configuration.

    Server paths are local to the machine rendering the bundle. Node configs
    contain only portable identity/toggles and optional host-local home paths.
    Topology publication remains an explicit host-local deployment step.
    """
    normalized = load_inventory(path)
    compiled = compile_inventory(normalized)
    output = Path(os.path.abspath(Path(output).expanduser()))
    for ancestor in (output, *output.parents):
        if ancestor.is_symlink():
            raise InventoryError('Inventory output may not traverse a symlink')
    if output.exists():
        raise InventoryError('Inventory output already exists; choose a fresh directory')
    values: dict[str, Any] = {'CLUSTER_NAME': compiled['cluster']['name'],
        'TELEMETRY_TARGETS': compiled['telemetry_targets'], 'TOPOLOGY_DIR': str(output / 'topology')}
    if 'monitoring_node' in compiled['cluster']:
        monitoring = next(node for node in compiled['nodes'] if node['name'] == compiled['cluster']['monitoring_node'])
        values.update(NODE_NAME=monitoring['name'], NODE_ADDR=monitoring['address'])
    if prometheus_url is not None:
        url = urlsplit(prometheus_url)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise InventoryError('prometheus_url must be HTTP(S) without credentials, query or fragment')
        values['PROMETHEUS_URL'] = prometheus_url
    files = {'server.toml': _toml(values, 'Monitoring-host paths. Server mode does not start a topology publisher.')}
    if compiled['native_sources'] is not None:
        values['TELEMETRY_SOURCES_FILE'] = str(output / 'native-sources.json')
        files['server.toml'] = _toml(values, 'Monitoring-host paths. Server mode does not start a topology publisher.')
        files['native-sources.json'] = _json_bytes(compiled['native_sources'])
    for kind, document in compiled['topology'].items():
        files[f'topology/{kind}-topology.json'] = _json_bytes(document)
    for node in compiled['nodes']:
        node_values: dict[str, Any] = {'CLUSTER_NAME': compiled['cluster']['name'], 'NODE_NAME': node['name'],
            'NODE_ADDR': node['address'], 'ENABLE_GPU_METRICS': bool(node['gpus'])}
        if 'telemetry_home' in node:
            node_values['TELEMETRY_HOME'] = node['telemetry_home']
        files[f"nodes/{node['name']}.toml"] = _toml(node_values,
            'Copy to this node. Home defaults/~/ paths resolve on the receiving host; topology deployment is separate.')
    if any(len(body) > MAX_CONFIG_BYTES for body in files.values()):
        raise InventoryError('Generated inventory asset exceeds the 1 MiB consumer limit; split inventory')
    manifest = {'schema_version': 1, 'scope': 'configured_inventory',
        'server_config': values,
        'cluster': compiled['cluster'], 'storage': compiled['storage'], 'nodes': compiled['nodes'],
        'inventory_sha256': hashlib.sha256(_json_bytes(normalized)).hexdigest(),
        'counts': {'nodes': len(compiled['nodes']), 'collector_targets': sum(node['collector'] for node in compiled['nodes']),
                   'native_sources': len(compiled['services']),
                   'components': {kind: len(document['components']) for kind, document in compiled['topology'].items()},
                   'edges': {kind: len(document['edges']) for kind, document in compiled['topology'].items()}},
        'sha256': {relative: hashlib.sha256(body).hexdigest() for relative, body in sorted(files.items())},
        'diagnosis_generated': False,
        'deployment_notes': [
            'Deploy a topology publisher explicitly: copy topology JSON to one collector host-local directory and set that node config TOPOLOGY_DIR.',
            'server.toml source/topology paths refer to the rendering monitoring host. Server mode alone does not publish topology.',
            'Node configs inherit host-local defaults. Do not copy rendering-host absolute paths to remote hosts.',
            'collector=false excludes a scrape target; it does not establish source down or hardware health.',
            'For collector=false nodes, runtime owner/device mappings are withheld; original node declarations remain in this manifest and inventory.',
            'Storage backend/filesystem metadata is descriptive, not observed replica selection or I/O attribution.',
            'Inventory does not create run/replica placement, diagnosis config, clock verification or physical network hops.']}
    files['manifest.json'] = _json_bytes(manifest)
    if len(files['manifest.json']) > MAX_CONFIG_BYTES:
        raise InventoryError('Generated manifest exceeds the 1 MiB limit; split inventory')
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.mkdir(mode=0o700)
    except FileExistsError as error:
        raise InventoryError('Inventory output already exists; choose a fresh directory') from error
    owned = output.stat()
    try:
        for relative, body in files.items():
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _write_private(destination, body)
    except BaseException:
        # Remove only the fresh directory claimed by this operation. A replaced
        # path or unrelated user output is never treated as owned cleanup.
        current = output.lstat() if output.exists() else None
        if current and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
            shutil.rmtree(output)
        raise
    return manifest
