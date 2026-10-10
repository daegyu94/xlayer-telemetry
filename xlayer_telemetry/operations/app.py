"""Install one Grafana App without changing auth, datasources or user databases."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import zipfile

from .config import ConfigError, assets_root
from ..fileio import atomic_write_text
from .._http_transport import request_bytes

APP_ID = 'xlayer-telemetry-app'
REPOSITORY = 'daegyu94/xlayer-telemetry'
MAX_ARCHIVE = 32 * 1024 * 1024
MAX_FILES = 1000
PROVISIONING = f'apiVersion: 1\napps:\n  - type: {APP_ID}\n    org_id: 1\n    disabled: false\n'


class AppError(ConfigError):
    action = 'Use xltel app status --json to inspect observed checks; preserve the installed plugin before retrying.'
    reference = 'docs/app-deployment-reference.md'


def semver(value):
    match = re.match(r'^(\d+)\.(\d+)\.(\d+)', value or '')
    return tuple(map(int,match.groups())) if match else None


def detected_version(config):
    health=api(config,'/api/health')
    if isinstance(health,dict) and semver(health.get('version')):return health['version']
    binary=Path(config['TOOLS_DIR'])/'grafana-v12.1.0/bin/grafana'
    if binary.is_file():
        try:
            result=subprocess.run([str(binary),'--version'],capture_output=True,timeout=3,text=True)
            match=re.search(r'\b(\d+\.\d+\.\d+)\b',result.stdout)
            if result.returncode==0 and match:return match.group(1)
        except (OSError,subprocess.TimeoutExpired):pass
    return None


def _read(path: Path) -> dict:
    try:
        with path.open('rb') as stream:
            body = stream.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            return {}
        result = json.loads(body)
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def _paths(config, external=False, plugins_dir=None, provisioning_dir=None):
    if external and (plugins_dir is None or provisioning_dir is None):
        raise AppError('External Grafana requires --plugins-dir and --provisioning-dir; existing external config is not inferred.')
    server = Path(config['SERVER_OUTPUT_DIR'])
    plugins = Path(plugins_dir) if external else server / 'grafana-plugins'
    provision = Path(provisioning_dir) if external else server / 'provisioning'
    plugins, provision = plugins.expanduser().absolute(), provision.expanduser().absolute()
    for path in (plugins, provision):
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise AppError('App installation paths must not traverse symlinks; keep the existing installation unchanged.')
    # Backups outside Grafana's recursively scanned plugin directory.
    state = plugins.parent / '.xlayer-app'
    return plugins / APP_ID, provision / 'plugins/xlayer-telemetry.yaml', state


def _hashes(directory):
    files = sorted(directory.rglob('*'))
    if len(files) > MAX_FILES or any(path.is_symlink() for path in files):
        raise AppError('Installed plugin has unsupported files or symlinks.')
    if sum(path.stat().st_size for path in files if path.is_file()) > 100 * 1024 * 1024:
        raise AppError('Installed plugin exceeds the bounded integrity-check size.')
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files if path.is_file()}


def write_receipt(path, receipt):
    atomic_write_text(path, json.dumps(receipt, indent=2) + '\n')
    path.chmod(0o600)


@contextmanager
def _locked(state):
    import fcntl
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if state.is_symlink():
        raise AppError('App state cannot be a symbolic link.')
    descriptor = os.open(state / 'lock', os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'a') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _unpack(archive, destination, digest, allow_unsigned):
    if not re.fullmatch('[a-fA-F0-9]{64}', digest or '') or archive.stat().st_size > MAX_ARCHIVE:
        raise AppError('A bounded ZIP and a valid SHA256 checksum are required.')
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest.lower():
        raise AppError('Package SHA256 mismatch. Obtain the original ZIP/checksum again; installed files were preserved.')
    try:
        opened=zipfile.ZipFile(archive)
    except zipfile.BadZipFile:
        raise AppError('Plugin ZIP is malformed; obtain the original package/checksum again.') from None
    with opened as package:
        entries = package.infolist()
        if len(entries) > MAX_FILES or sum(entry.file_size for entry in entries) > 100 * 1024 * 1024:
            raise AppError('Plugin archive exceeds file/size limits.')
        seen = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (entry.filename in seen or path.is_absolute() or '..' in path.parts or '\\' in entry.filename
                    or not path.parts or path.parts[0] != APP_ID or entry.flag_bits & 1
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise AppError('Unsafe or duplicate plugin archive member; installation was not changed.')
            seen.add(entry.filename)
        try:
            metadata = json.loads(package.read(f'{APP_ID}/plugin.json'))
        except (KeyError,ValueError,UnicodeError,zipfile.BadZipFile):
            raise AppError('Plugin metadata is missing or invalid; installed files were preserved.') from None
        if not isinstance(metadata,dict) or not isinstance(metadata.get('info'),dict) or not isinstance(metadata.get('dependencies'),dict):
            raise AppError('Plugin metadata must contain the existing info/dependency contract.')
        if metadata.get('id') != APP_ID or metadata.get('type') != 'app':
            raise AppError('Package plugin identity does not match XLayer Telemetry App.')
        version = metadata.get('info', {}).get('version')
        dependency = metadata.get('dependencies', {}).get('grafanaDependency', '')
        if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?', version or '') or not re.fullmatch(r'>=\d+\.\d+\.\d+', dependency):
            raise AppError('Package version or Grafana dependency is not supported by this installer.')
        for name in ('module.js', 'img/logo.svg'):
            if f'{APP_ID}/{name}' not in seen:
                raise AppError('Required plugin build files are missing.')
        manifest = f'{APP_ID}/MANIFEST.txt' in seen
        if not manifest and not allow_unsigned:
            raise AppError('Package is unsigned. For an isolated PoC use --allow-unsigned; production requires a Grafana-signed package.')
        package.extractall(destination)
    return metadata, manifest


def install_package(config, archive, sha256, *, allow_unsigned=False, update=False,
                    external=False, plugins_dir=None, provisioning_dir=None):
    target, provision, state = _paths(config, external, plugins_dir, provisioning_dir)
    # Verify/extract before touching existing state, including unsigned policy.
    with tempfile.TemporaryDirectory(prefix='xltel-app-verify-') as temporary:
        unpacked = Path(temporary)
        metadata, manifest = _unpack(Path(archive), unpacked, sha256, allow_unsigned)
        version=detected_version(config)
        if version and semver(version)<semver(metadata['dependencies']['grafanaDependency'][2:]):
            raise AppError('Grafana version does not meet the plugin minimum; existing files were preserved.')
        with _locked(state):
            receipt_path = state / 'installed.json'
            old = _read(receipt_path)
            if target.is_symlink():
                raise AppError('Existing plugin is a symlink. Preserve it and install into an explicit separate plugin directory.')
            if provision.exists() and provision.read_text() != PROVISIONING:
                raise AppError('Existing XLayer provisioning file differs. Preserve/reconcile it before updating.')
            if target.exists() and _read(target / 'plugin.json').get('id') != APP_ID:
                raise AppError('Existing plugin directory has an unknown identity; it was preserved.')
            previous_version=_read(target/'plugin.json').get('info',{}).get('version')
            if previous_version and semver(previous_version) and semver(metadata['info']['version'])<semver(previous_version):
                raise AppError('A version downgrade is not an update. Use the verified rollback copy instead.')
            hashes = _hashes(unpacked / APP_ID)
            if old.get('sha256') == sha256.lower() and target.is_dir() and _hashes(target) == old.get('files'):
                return {**old, 'changed': False, 'status': 'unchanged'}
            if target.exists() and not update:
                raise AppError('A different plugin is already installed. Use xltel app update to preserve a rollback copy.')
            previous = state / 'previous'
            retained = state / 'retained-previous'
            if retained.exists():
                raise AppError('An interrupted update retains a previous backup; preserve App state and recover it before another update.')
            stage = state / 'staged'
            if stage.exists():
                shutil.rmtree(stage)
            old_provision = provision.read_bytes() if provision.exists() else None
            old_receipt = receipt_path.read_bytes() if receipt_path.exists() else None
            previous_receipt = state / 'previous.json'
            old_previous_receipt = previous_receipt.read_bytes() if previous_receipt.exists() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            had_target = target.exists()
            moved_old = placed_new = kept_previous = False
            provision_attempted = previous_receipt_attempted = receipt_attempted = False
            try:
                # Prepare on the destination filesystem before rotating backups.
                shutil.copytree(unpacked / APP_ID, stage)
                if previous.exists():
                    os.replace(previous, retained)
                    kept_previous = True
                if had_target:
                    os.replace(target, previous)
                    moved_old = True
                os.replace(stage, target)
                placed_new = True
                provision_attempted = True
                atomic_write_text(provision, PROVISIONING)
                receipt = {'schema_version': 1, 'plugin_id': APP_ID, 'version': metadata['info']['version'],
                    'dependency': metadata['dependencies']['grafanaDependency'], 'sha256': sha256.lower(), 'files': hashes,
                    'unsigned_allowed': not manifest and allow_unsigned, 'manifest_present': manifest,
                    'deployment': 'external' if external else 'managed', 'changed': True, 'status': 'restart_required'}
                if had_target:
                    previous_receipt_attempted = True
                    write_receipt(state / 'previous.json', {'receipt': old, 'provisioning': old_provision.decode() if old_provision else None})
                receipt_attempted = True
                write_receipt(receipt_path, receipt)
            except BaseException:
                if placed_new and target.exists():
                    shutil.rmtree(target)
                if moved_old and previous.exists():
                    os.replace(previous, target)
                if kept_previous and retained.exists():
                    os.replace(retained, previous)
                for path, body, attempted in ((provision, old_provision, provision_attempted),
                    (previous_receipt, old_previous_receipt, previous_receipt_attempted),
                    (receipt_path, old_receipt, receipt_attempted)):
                    if not attempted:
                        continue
                    if body is None:
                        path.unlink(missing_ok=True)
                    elif not path.exists() or path.read_bytes() != body:
                        atomic_write_text(path, body.decode())
                raise
            finally:
                shutil.rmtree(stage, ignore_errors=True)
            # Only a committed update authorizes disposing of the older backup.
            shutil.rmtree(retained, ignore_errors=True)
            return receipt


def rollback(config, *, external=False, plugins_dir=None, provisioning_dir=None):
    target, provision, state = _paths(config, external, plugins_dir, provisioning_dir)
    with _locked(state):
        previous = state / 'previous'
        record = _read(state / 'previous.json')
        if not previous.is_dir() or previous.is_symlink() or not record:
            raise AppError('No verified previous installation is available for rollback.')
        if not record.get('receipt',{}).get('files') or _hashes(previous)!=record['receipt']['files']:
            raise AppError('Previous plugin integrity cannot be verified; the current installation was preserved.')
        if provision.exists() and provision.read_text() != PROVISIONING:
            raise AppError('User-edited provisioning was preserved; reconcile it before rollback.')
        abandoned = state / 'abandoned'
        if abandoned.exists():
            shutil.rmtree(abandoned)
        current_receipt=_read(state/'installed.json')
        current_provision=provision.read_text() if provision.exists() else None
        moved_current=moved_previous=False
        try:
            if target.exists():
                os.replace(target, abandoned)
                moved_current=True
            os.replace(previous, target)
            moved_previous=True
            if record.get('provisioning') is None:
                provision.unlink(missing_ok=True)
            else:
                atomic_write_text(provision, record['provisioning'])
            write_receipt(state / 'installed.json', record['receipt'])
        except BaseException:
            if moved_previous:os.replace(target,previous)
            if moved_current:os.replace(abandoned,target)
            atomic_write_text(state/'installed.json',json.dumps(current_receipt))
            if current_provision is None:provision.unlink(missing_ok=True)
            else:atomic_write_text(provision,current_provision)
            raise
        shutil.rmtree(abandoned, ignore_errors=True)
    return {'status': 'restart_required', 'changed': True, 'deployment': 'external' if external else 'managed'}


def local_state(config, *, external=False, plugins_dir=None, provisioning_dir=None):
    target, provision, state = _paths(config, external, plugins_dir, provisioning_dir)
    receipt = _read(state / 'installed.json')
    try:
        integrity = 'verified' if receipt.get('files') and _hashes(target) == receipt['files'] else 'modified' if receipt.get('files') and target.exists() else 'unmanaged' if target.exists() else 'not_installed'
    except (OSError, AppError):
        integrity = 'unknown'
    return {'integrity': integrity, 'receipt': receipt, 'provisioned': provision.exists() and provision.read_text() == PROVISIONING}


def unsigned_allowed(server: Path):
    state = local_state({'SERVER_OUTPUT_DIR': str(server)})
    return APP_ID if state['integrity'] == 'verified' and state['receipt'].get('unsigned_allowed') else ''


def api(config, path):
    token = os.environ.get('GRAFANA_SERVICE_ACCOUNT_TOKEN') or config.get('GRAFANA_SERVICE_ACCOUNT_TOKEN')
    try:
        return json.loads(request_bytes(config['GRAFANA_URL'].rstrip('/') + path, None, 3,
            headers={'Authorization': 'Bearer ' + token} if token else None))
    except (OSError, ValueError, RuntimeError):
        return None


def status(config, **paths):
    local = local_state(config, **paths)
    health, loaded, datasources = api(config, '/api/health'), api(config, f'/api/plugins/{APP_ID}/settings'), api(config, '/api/datasources')
    version = health.get('version') if isinstance(health, dict) else None
    minimum = local['receipt'].get('dependency', '>=12.1.0')[2:]
    compatible = semver(version) is not None and semver(version) >= semver(minimum)
    checks = [{'component': 'Grafana', 'status': 'pass' if version else 'unknown', 'detail': version or 'API unavailable or access not verified'},
        {'component': 'Declared compatibility', 'status': 'pass' if compatible else 'unknown' if not version else 'fail', 'detail': f'{minimum}+; browser-validated 12.1.0'},
        {'component': 'Plugin package integrity', 'status': 'pass' if local['integrity'] == 'verified' else 'fail', 'detail': local['integrity']},
        {'component': 'Plugin enabled', 'status': 'pass' if isinstance(loaded, dict) and loaded.get('enabled') else 'unknown', 'detail': 'Grafana API observation'},
        {'component': 'Datasources', 'status': 'pass' if isinstance(datasources, list) and any(row.get('type') == 'prometheus' for row in datasources) else 'unknown', 'detail': 'Prometheus availability; Loki optional'}]
    definitions = [api(config, '/api/dashboards/uid/' + uid) for uid in
                   ('telemetry-overview', 'agent-rl-stage-correlation', 'xlayer-compute-communication', 'xlayer-data-storage')]
    dashboards = all(isinstance(value,dict) and isinstance(value.get('dashboard'),dict) for value in definitions)
    checks.append({'component': 'Canonical dashboards', 'status': 'pass' if dashboards else 'unknown', 'detail': 'Existing query/panel contracts'})
    required = set()
    def visit(value):
        if isinstance(value,dict):
            source=value.get('datasource')
            if isinstance(source,dict) and source.get('uid') and source['uid']!='__expr__': required.add(source['uid'])
            for child in value.values(): visit(child)
        elif isinstance(value,list):
            for child in value: visit(child)
    for definition in definitions: visit(definition)
    available={row.get('uid') for row in datasources} if isinstance(datasources,list) else set()
    checks.append({'component':'Canonical datasource mapping','status':'pass' if dashboards and required and required<=available else 'unknown',
                   'detail':'Referenced UID availability; datasource query health is checked in the existing dashboard'})
    served = False
    if local['integrity']=='verified' and isinstance(loaded,dict) and loaded.get('enabled'):
        try:
            token=os.environ.get('GRAFANA_SERVICE_ACCOUNT_TOKEN') or config.get('GRAFANA_SERVICE_ACCOUNT_TOKEN')
            body=request_bytes(config['GRAFANA_URL'].rstrip('/')+f'/public/plugins/{APP_ID}/module.js',None,3,max_response_bytes=8*1024*1024,
                headers={'Authorization':'Bearer '+token} if token else None)
            served=hashlib.sha256(body).hexdigest()==local['receipt']['files'].get('module.js')
        except (OSError,ValueError,RuntimeError): pass
    checks.append({'component':'Installed bundle served','status':'pass' if served else 'unknown','detail':'Loaded metadata alone cannot identify an updated module'})
    signature=loaded.get('signature') if isinstance(loaded,dict) else None
    signature_ok=signature=='valid' if local['receipt'].get('manifest_present') else signature=='unsigned' and local['receipt'].get('unsigned_allowed',False)
    checks.append({'component':'Grafana signature policy','status':'pass' if signature_ok else 'unknown','detail':signature or 'Signature not observed'})
    return {'status': 'ready' if all(row['status'] == 'pass' for row in checks) else 'needs_attention', 'checks': checks,
        'deployment': 'external' if paths.get('external') else 'managed', 'local': local,
        'signature': 'unsigned_poc' if local['receipt'].get('unsigned_allowed') else 'Grafana_verification_required',
        'dashboard': config['GRAFANA_URL'].rstrip('/') + f'/a/{APP_ID}/overview',
        'action': 'If API checks are unknown, supply GRAFANA_SERVICE_ACCOUNT_TOKEN with read access; after file changes restart Grafana.',
        'reference': 'docs/app-deployment-reference.md'}


def checksum(path, explicit=None):
    if explicit:
        return explicit
    sidecar = Path(str(path) + '.sha256')
    try:
        if not stat.S_ISREG(sidecar.stat().st_mode):raise OSError
        with sidecar.open() as stream:text=stream.read(257)
        if len(text)>256:raise OSError
    except OSError:
        raise AppError('Package checksum is missing. Supply --sha256 or the matching ZIP.sha256 file.') from None
    return text.split()[0] if text.split() else ''


def _command(argv, *, timeout=180, **kwargs):
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, **kwargs)
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except BaseException as error:
        # This session contains only the package command and its descendants.
        # Kill the group even if npm already exited but a compiler holds pipes.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=5)
        if isinstance(error, subprocess.TimeoutExpired):
            raise AppError('App package preparation timed out; owned build processes were stopped and the installed plugin was preserved.') from None
        raise
    if process.returncode:
        raise AppError('App package preparation failed. Check npm/gh access and retry; the installed plugin was preserved.')
    return stdout.decode()


@contextmanager
def package_source(config, *, package=None, sha256=None, build=False):
    if package:
        yield Path(package), checksum(package, sha256), 'local_checked_archive'
        return
    with tempfile.TemporaryDirectory(prefix='xltel-app-package-') as temporary:
        output = Path(temporary)
        if not build and shutil.which('gh'):
            candidate = None
            try:
                runs = json.loads(_command(['gh', 'run', 'list', '--repo', REPOSITORY, '--workflow', 'grafana-app.yml',
                    '--branch', 'main', '--event','push', '--status', 'success', '--limit', '1', '--json', 'databaseId,headSha']))
                if runs:
                    run = runs[0]
                    current = json.loads(_command(['gh','api',f'repos/{REPOSITORY}/commits/main']))
                    if run.get('headSha')!=current.get('sha'):
                        raise AppError('The latest successful CI package is older than main; build the current checkout instead.')
                    artifacts = json.loads(_command(['gh', 'api', f"repos/{REPOSITORY}/actions/runs/{run['databaseId']}/artifacts"]))
                    candidates = [a for a in artifacts.get('artifacts', []) if a['name'] == 'xlayer-grafana-app-poc-single-worker' and not a['expired'] and a['size_in_bytes'] < MAX_ARCHIVE]
                    if candidates:
                        _command(['gh', 'run', 'download', str(run['databaseId']), '--repo', REPOSITORY, '--name', candidates[0]['name'], '--dir', str(output)])
                        archives = list(output.rglob(f'{APP_ID}-*.zip'))
                        if len(archives) == 1:
                            candidate = (archives[0], checksum(archives[0]), 'successful_ci:' + run['headSha'])
            except (AppError, ValueError, OSError, subprocess.TimeoutExpired):
                pass
            if candidate:
                yield candidate
                return
        app = assets_root() / 'grafana/xlayer-app'
        if not (app / 'package-lock.json').is_file() or not shutil.which('npm'):
            raise AppError('No accessible prebuilt CI package or checkout/npm. Use --package ZIP --sha256 HASH, or authenticate gh for CI artifacts.')
        checkout=output/'checkout'
        source=checkout/'grafana/xlayer-app'
        source.mkdir(parents=True)
        for name in ('package.json','package-lock.json','tsconfig.json','webpack.config.cjs'):
            shutil.copyfile(app/name,source/name)
        for name in ('src','tests'):
            shutil.copytree(app/name,source/name,symlinks=False)
        (source/'scripts').mkdir()
        shutil.copyfile(app/'scripts/package_plugin.py',source/'scripts/package_plugin.py')
        # Existing frontend contract tests read the canonical dashboard sources.
        (checkout/'scripts').mkdir()
        for name in ('provision_dashboards.py','dashboard_views.py'):
            shutil.copyfile(assets_root()/'scripts'/name,checkout/'scripts'/name)
        shutil.copytree(assets_root()/'examples/dashboards',checkout/'examples/dashboards',ignore=shutil.ignore_patterns('*.png','*.gif'))
        environment=os.environ|{'PYTHON':config['TELEMETRY_PYTHON']}
        _command(['npm', 'ci', '--no-audit', '--no-fund'], cwd=source,env=environment)
        _command(['npm', 'run', 'typecheck'], cwd=source,env=environment)
        _command(['npm', 'test'], cwd=source,env=environment)
        _command(['npm', 'run', 'build'], cwd=source,env=environment)
        _command([config['TELEMETRY_PYTHON'], str(source/'scripts/package_plugin.py'), '--output', str(output/'package')])
        archive = next((output/'package').glob('*.zip'))
        yield archive, checksum(archive), 'isolated_checkout_build'
