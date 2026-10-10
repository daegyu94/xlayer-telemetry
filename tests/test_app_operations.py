import hashlib
import json
from pathlib import Path
import zipfile
import pytest

from xlayer_telemetry.operations import app

@pytest.fixture(autouse=True)
def no_existing_grafana(monkeypatch):
    monkeypatch.setattr(app,'api',lambda *args: None)


def bundle(tmp_path, value='one', **metadata):
    path = tmp_path / f'{value}.zip'
    plugin = {'id': app.APP_ID, 'type': 'app', 'info': {'version': '0.1.0'},
              'dependencies': {'grafanaDependency': '>=12.1.0'}, **metadata}
    with zipfile.ZipFile(path, 'w') as archive:
        for name, body in {'plugin.json': json.dumps(plugin), 'module.js': value, 'img/logo.svg': '<svg/>'}.items():
            archive.writestr(f'{app.APP_ID}/{name}', body)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def config(tmp_path):
    return {'SERVER_OUTPUT_DIR': str(tmp_path / 'server'), 'TELEMETRY_HOME': str(tmp_path / 'home'),
            'TOOLS_DIR': str(tmp_path / 'tools'), 'GRAFANA_URL': 'http://127.0.0.1:13000'}


def test_unsigned_install_requires_explicit_poc_opt_in_and_keeps_other_data(tmp_path):
    path, digest = bundle(tmp_path)
    settings = config(tmp_path)
    with pytest.raises(app.AppError, match='unsigned'):
        app.install_package(settings, path, digest)
    assert not (tmp_path / 'server/grafana-plugins').exists()
    marker = tmp_path / 'server/grafana-data/user.db'
    marker.parent.mkdir(parents=True); marker.write_text('preserve')
    result = app.install_package(settings, path, digest, allow_unsigned=True)
    assert result['changed'] and result['status'] == 'restart_required'
    assert marker.read_text() == 'preserve'
    assert app.unsigned_allowed(Path(settings['SERVER_OUTPUT_DIR'])) == app.APP_ID
    assert not app.install_package(settings, path, digest, allow_unsigned=True)['changed']


def test_update_and_explicit_rollback_restore_previous_bytes(tmp_path):
    settings = config(tmp_path)
    first, checksum = bundle(tmp_path)
    app.install_package(settings, first, checksum, allow_unsigned=True)
    second, checksum = bundle(tmp_path, 'two')
    with pytest.raises(app.AppError, match='update'):
        app.install_package(settings, second, checksum, allow_unsigned=True)
    app.install_package(settings, second, checksum, allow_unsigned=True, update=True)
    target = tmp_path / f'server/grafana-plugins/{app.APP_ID}/module.js'
    assert target.read_text() == 'two'
    app.rollback(settings)
    assert target.read_text() == 'one'


@pytest.mark.parametrize('member', ['../escape', '/absolute', f'{app.APP_ID}/../escape', f'{app.APP_ID}/module.js'])
def test_archive_traversal_and_duplicate_members_are_rejected_before_install(tmp_path, member):
    path, _ = bundle(tmp_path)
    with zipfile.ZipFile(path, 'a') as archive:
        archive.writestr(member, 'bad')
    with pytest.raises(app.AppError):
        app.install_package(config(tmp_path), path, hashlib.sha256(path.read_bytes()).hexdigest(), allow_unsigned=True)
    assert not (tmp_path / 'escape').exists()


def test_digest_identity_and_modified_install_are_not_trusted(tmp_path):
    path, digest = bundle(tmp_path)
    with pytest.raises(app.AppError, match='SHA256'):
        app.install_package(config(tmp_path), path, '0' * 64, allow_unsigned=True)
    app.install_package(config(tmp_path), path, digest, allow_unsigned=True)
    (tmp_path / f'server/grafana-plugins/{app.APP_ID}/module.js').write_text('modified')
    assert app.local_state(config(tmp_path))['integrity'] == 'modified'


def test_external_install_requires_explicit_paths_and_never_claims_managed_restart(tmp_path):
    path, digest = bundle(tmp_path)
    with pytest.raises(app.AppError, match='external'):
        app.install_package(config(tmp_path), path, digest, allow_unsigned=True, external=True)
    result = app.install_package(config(tmp_path), path, digest, allow_unsigned=True, external=True,
                                 plugins_dir=tmp_path/'external/plugins', provisioning_dir=tmp_path/'external/provisioning')
    assert result['deployment'] == 'external' and result['status'] == 'restart_required'


def test_failed_update_and_conflicting_provisioning_preserve_working_install(tmp_path, monkeypatch):
    settings = config(tmp_path)
    path, digest = bundle(tmp_path)
    app.install_package(settings, path, digest, allow_unsigned=True)
    second, checksum = bundle(tmp_path, 'two')
    original = app.write_receipt
    monkeypatch.setattr(app, 'write_receipt', lambda *args: (_ for _ in ()).throw(OSError('injected')))
    with pytest.raises(OSError):
        app.install_package(settings, second, checksum, allow_unsigned=True, update=True)
    assert (tmp_path/f'server/grafana-plugins/{app.APP_ID}/module.js').read_text() == 'one'
    monkeypatch.setattr(app, 'write_receipt', original)
    (tmp_path/'server/provisioning/plugins/xlayer-telemetry.yaml').write_text('user-edited')
    with pytest.raises(app.AppError, match='provisioning'):
        app.install_package(settings, second, checksum, allow_unsigned=True, update=True)


def test_unverified_signature_and_missing_api_auth_do_not_report_ready(tmp_path, monkeypatch):
    settings = config(tmp_path)
    path, digest = bundle(tmp_path)
    app.install_package(settings, path, digest, allow_unsigned=True)
    monkeypatch.setattr(app, 'api', lambda *args: None)
    result = app.status(settings)
    assert result['status'] != 'ready'
    assert any(row['status'] == 'unknown' for row in result['checks'])


def test_incompatible_grafana_and_downgrade_preserve_the_existing_plugin(tmp_path, monkeypatch):
    path,digest=bundle(tmp_path)
    monkeypatch.setattr(app,'api',lambda *args:{'version':'11.0.0'})
    with pytest.raises(app.AppError,match='Grafana version'):
        app.install_package(config(tmp_path),path,digest,allow_unsigned=True)
    assert not (tmp_path/'server/grafana-plugins').exists()
    monkeypatch.setattr(app,'api',lambda *args:None)
    app.install_package(config(tmp_path),path,digest,allow_unsigned=True)
    old,digest=bundle(tmp_path,'old',info={'version':'0.0.9'})
    with pytest.raises(app.AppError,match='downgrade'):
        app.install_package(config(tmp_path),old,digest,allow_unsigned=True,update=True)


def test_mismatched_datasource_and_old_served_bundle_do_not_report_ready(tmp_path, monkeypatch):
    path,digest=bundle(tmp_path);settings=config(tmp_path)
    app.install_package(settings,path,digest,allow_unsigned=True)
    def api(config,path):
        if path=='/api/health':return {'version':'12.1.0'}
        if '/plugins/' in path:return {'enabled':True,'signature':'unsigned'}
        if path=='/api/datasources':return [{'type':'prometheus','uid':'other'}]
        return {'dashboard':{'panels':[{'datasource':{'uid':'required','type':'prometheus'}}]}}
    monkeypatch.setattr(app,'api',api)
    monkeypatch.setattr(app,'request_bytes',lambda *args,**kwargs:b'old-module')
    result=app.status(settings)
    assert result['status']=='needs_attention'
    assert {row['component'] for row in result['checks'] if row['status']!='pass'} == {'Canonical datasource mapping','Installed bundle served'}


def test_failed_initial_rename_never_deletes_the_working_plugin(tmp_path,monkeypatch):
    path,digest=bundle(tmp_path);settings=config(tmp_path)
    app.install_package(settings,path,digest,allow_unsigned=True)
    second,digest=bundle(tmp_path,'two')
    replace=app.os.replace
    target=tmp_path/f'server/grafana-plugins/{app.APP_ID}'
    def fail(source,destination):
        if Path(source)==target:raise PermissionError('injected rename failure')
        return replace(source,destination)
    monkeypatch.setattr(app.os,'replace',fail)
    with pytest.raises(PermissionError):app.install_package(settings,second,digest,allow_unsigned=True,update=True)
    assert (target/'module.js').read_text()=='one'


def test_bad_zip_and_tampered_backup_are_actionable_and_preserve_current(tmp_path):
    malformed=tmp_path/'bad.zip';malformed.write_bytes(b'not a zip')
    with pytest.raises(app.AppError):app.install_package(config(tmp_path),malformed,hashlib.sha256(malformed.read_bytes()).hexdigest(),allow_unsigned=True)
    first,digest=bundle(tmp_path);settings=config(tmp_path)
    app.install_package(settings,first,digest,allow_unsigned=True)
    second,digest=bundle(tmp_path,'two');app.install_package(settings,second,digest,allow_unsigned=True,update=True)
    (tmp_path/'server/.xlayer-app/previous/module.js').write_text('tampered')
    with pytest.raises(app.AppError,match='integrity'):app.rollback(settings)
    assert (tmp_path/f'server/grafana-plugins/{app.APP_ID}/module.js').read_text()=='two'


def test_prebuilt_source_accepts_only_successful_main_push_and_preserves_install_errors(tmp_path,monkeypatch):
    import shutil
    path,digest=bundle(tmp_path)
    monkeypatch.setattr(app.shutil,'which',lambda name:'/usr/bin/gh' if name=='gh' else None)
    calls=[]
    def command(argv,**kwargs):
        calls.append(argv)
        if argv[1:3]==['run','list']:
            assert '--event' in argv and argv[argv.index('--event')+1]=='push'
            return json.dumps([{'databaseId':123,'headSha':'a'*40}])
        if argv[1]=='api' and 'commits/main' in argv[2]:return json.dumps({'sha':'a'*40})
        if argv[1]=='api':return json.dumps({'artifacts':[{'name':'xlayer-grafana-app-poc-single-worker','expired':False,'size_in_bytes':1000}]})
        output=Path(argv[argv.index('--dir')+1]);destination=output/f'{app.APP_ID}-0.1.0-unsigned.zip'
        shutil.copyfile(path,destination);Path(str(destination)+'.sha256').write_text(digest+'  '+destination.name)
        return ''
    monkeypatch.setattr(app,'_command',command)
    with pytest.raises(app.AppError,match='unsigned'):
        with app.package_source({'TELEMETRY_HOME':str(tmp_path),'TELEMETRY_PYTHON':'python'}) as (archive,checksum,source):
            assert source=='successful_ci:'+'a'*40
            app.install_package(config(tmp_path),archive,checksum)
    assert len(calls)==4,'Caller installation errors must not restart package acquisition/build'


def test_failed_rollback_swap_restores_the_current_installation(tmp_path,monkeypatch):
    first,digest=bundle(tmp_path);settings=config(tmp_path)
    app.install_package(settings,first,digest,allow_unsigned=True)
    second,digest=bundle(tmp_path,'two');app.install_package(settings,second,digest,allow_unsigned=True,update=True)
    previous=tmp_path/'server/.xlayer-app/previous'
    original=app.os.replace
    def fail(source,target):
        if Path(source)==previous:raise PermissionError('injected')
        return original(source,target)
    monkeypatch.setattr(app.os,'replace',fail)
    with pytest.raises(PermissionError):app.rollback(settings)
    assert (tmp_path/f'server/grafana-plugins/{app.APP_ID}/module.js').read_text()=='two'
