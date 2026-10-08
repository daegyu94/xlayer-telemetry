"""CPU-only regression for App packaging, trusted assets and owned process readiness."""
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import time
import zipfile

import pytest

SCRIPTS = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOLS = load('install_ci_tools')
CI = load('ci_demo')
PACKAGE = load('package_plugin')


def distribution(root):
    root.mkdir()
    (root / 'img').mkdir()
    (root / 'plugin.json').write_text(json.dumps({'id': PACKAGE.PLUGIN_ID, 'type': 'app', 'info': {'version': '0.1.0'}}))
    (root / 'module.js').write_text('export default {};')
    (root / 'img/logo.svg').write_text('<svg/>')
    return root


def test_asset_digests_are_fixed_and_cache_corruption_is_rejected(tmp_path):
    assert len(TOOLS.ASSETS) == 3
    assert all(len(row[2]) == 64 and row[1].startswith('https://') for row in TOOLS.ASSETS)
    file = tmp_path / 'download'
    file.write_bytes(b'changed payload')
    assert not TOOLS.verified(file, TOOLS.ASSETS[0][2])
    assert not TOOLS.verified(tmp_path / 'missing', TOOLS.ASSETS[0][2])


@pytest.mark.parametrize('name', ['../outside', '/outside', 'dir/../../outside'])
def test_release_archive_path_traversal_is_rejected(name):
    with pytest.raises(ValueError, match='Unsafe'):
        TOOLS.safe_name(name)


def test_release_archive_link_is_rejected_before_extracting(tmp_path):
    archive = tmp_path / 'archive.tar.gz'
    with tarfile.open(archive, 'w:gz') as package:
        member = tarfile.TarInfo('tool/link')
        member.type = tarfile.SYMTYPE
        member.linkname = '/tmp/outside'
        package.addfile(member)
    with pytest.raises(ValueError, match='unsupported'):
        TOOLS.unpack(archive, tmp_path / 'tools')


def test_unsigned_package_has_expected_top_level_and_repeatable_digest(tmp_path):
    dist = distribution(tmp_path / 'dist')
    first = PACKAGE.package(dist, tmp_path / 'a')
    second = PACKAGE.package(dist, tmp_path / 'b')
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert sorted(archive.namelist()) == ['xlayer-telemetry-app/img/logo.svg', 'xlayer-telemetry-app/module.js', 'xlayer-telemetry-app/plugin.json']
    assert first.with_name(first.name + '.sha256').is_file()
    with pytest.raises(ValueError, match='already exists'):
        PACKAGE.package(dist, tmp_path / 'a')


def test_package_rejects_wrong_identity_missing_bundle_and_signed_directory(tmp_path):
    dist = distribution(tmp_path / 'dist')
    (dist / 'module.js').unlink()
    with pytest.raises(ValueError, match='missing'):
        PACKAGE.package(dist, tmp_path / 'out')
    (dist / 'module.js').write_text('bundle')
    (dist / 'MANIFEST.txt').write_text('signed')
    with pytest.raises(ValueError, match='signed'):
        PACKAGE.package(dist, tmp_path / 'out')
    (dist / 'MANIFEST.txt').unlink()
    (dist / 'plugin.json').write_text('{"id":"another-plugin","type":"app"}')
    with pytest.raises(ValueError, match='identity'):
        PACKAGE.package(dist, tmp_path / 'out')


def test_package_symlink_cannot_include_external_file(tmp_path):
    dist = distribution(tmp_path / 'dist')
    (dist / 'leak').symlink_to(tmp_path / 'external')
    with pytest.raises(ValueError, match='symbolic'):
        PACKAGE.package(dist, tmp_path / 'out')


def test_readiness_requires_owned_process_and_two_actual_completions(tmp_path):
    class Live:
        def poll(self): return None
        def wait(self, timeout): raise subprocess.TimeoutExpired('owned demo', timeout)
    log = tmp_path / 'log'
    log.write_text('COMPLETED Step 127\nCOMPLETED Step 129\n')
    (tmp_path / 'connection.json').write_text('{"grafana":"http://127.0.0.1:12345"}')
    assert CI.wait_for_fixtures(Live(), log, tmp_path, time.monotonic() + 1)['grafana'].endswith('12345')
    class Exited:
        def poll(self): return 1
    with pytest.raises(RuntimeError, match='exited'):
        CI.wait_for_fixtures(Exited(), log, tmp_path, time.monotonic() + 1)
    with pytest.raises(TimeoutError):
        CI.wait_for_fixtures(Live(), log, tmp_path, time.monotonic() - 1)


def test_archive_cannot_extract_through_preexisting_symlink(tmp_path):
    output = tmp_path / 'tools'
    output.mkdir()
    (output / 'redirect').symlink_to(tmp_path / 'other', target_is_directory=True)
    with pytest.raises(ValueError, match='symbolic'):
        TOOLS.extract_target(output, 'redirect/tool')


def test_log_tail_is_bounded(tmp_path):
    log = tmp_path / 'large.log'
    log.write_bytes(b'a' * 100000 + b'\nlast line\n')
    assert len(CI.bounded_tail(log)) == 65536
    assert CI.bounded_tail(log).endswith('last line\n')


def test_cleanup_does_not_signal_exited_process():
    class Exited:
        def poll(self): return 0
        def terminate(self): raise AssertionError('Already exited process must not be signalled')
    CI.stop_owned(Exited())


def test_package_cannot_output_inside_distribution(tmp_path):
    dist = distribution(tmp_path / 'dist')
    with pytest.raises(ValueError, match='outside'):
        PACKAGE.package(dist, dist / 'packages')


def test_download_sha_mismatch_never_extracts_or_replaces_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(TOOLS, 'ASSETS', (('fake.tar.gz', 'https://example.test/fake', '0' * 64),))
    monkeypatch.setattr(TOOLS.urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(b'untrusted'))
    monkeypatch.setattr(TOOLS, 'unpack', lambda *args: pytest.fail('Unverified download must not be extracted'))
    with pytest.raises(ValueError, match='Publisher SHA256 mismatch'):
        TOOLS.install(tmp_path)
    assert not (tmp_path / 'fake.tar.gz').exists()
    assert not list(tmp_path.glob('*.part'))


def test_selected_tool_install_reuses_verified_archive_without_other_downloads(tmp_path, monkeypatch):
    archive = tmp_path / 'prometheus.tar.gz'
    with tarfile.open(archive, 'w:gz') as package:
        member = tarfile.TarInfo('prometheus-test/promtool')
        member.size = 4
        member.mode = 0o755
        package.addfile(member, io.BytesIO(b'tool'))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(TOOLS, 'ASSETS', (
        ('grafana.tar.gz', 'https://example.test/grafana', '0' * 64),
        ('prometheus.tar.gz', 'https://example.test/prometheus', digest),
        ('loki.zip', 'https://example.test/loki', '0' * 64),
    ))
    monkeypatch.setattr(TOOLS.urllib.request, 'urlopen', lambda *args, **kwargs: pytest.fail('Selected verified archive must avoid network'))
    TOOLS.install(tmp_path, only=['prometheus'])
    assert (tmp_path / 'prometheus-test/promtool').read_bytes() == b'tool'
    assert not (tmp_path / 'grafana.tar.gz').exists()
    assert not (tmp_path / 'loki.zip').exists()


def test_invalid_tool_selection_fails_before_creating_directory_or_downloading(tmp_path, monkeypatch):
    output = tmp_path / 'tools'
    monkeypatch.setattr(TOOLS.urllib.request, 'urlopen', lambda *args, **kwargs: pytest.fail('Invalid selection must avoid network'))
    with pytest.raises(ValueError, match='Unknown tool'):
        TOOLS.install(output, only=['promtool'])
    assert not output.exists()


def test_browser_deadline_cleanup_signals_only_its_owned_session(monkeypatch):
    signals = []
    monkeypatch.setattr(CI.os, 'killpg', lambda pid, sig: signals.append((pid, sig)))
    class Browser:
        pid = 12345
        def poll(self): return None
        def wait(self, timeout): return -15
        def terminate(self): pytest.fail('Browser cleanup must include owned child group')
    CI.stop_owned(Browser(), whole_group=True)
    assert signals == [(12345, CI.signal.SIGTERM)]


def test_structured_grafana_datasource_error_is_per_refid_and_has_no_fake_frames():
    payload = CI.datasource_error_result([{'refId':'A'}, {'refId':'B'}])
    assert set(payload['results']) == {'A', 'B'}
    assert all(row['status']==503 and row['frames']==[] and row['errorSource']=='downstream' for row in payload['results'].values())
    assert all('Synthetic browser boundary' in row['error'] for row in payload['results'].values())


def test_opt_in_single_completed_comparison_still_requires_real_completion(tmp_path):
    class Live:
        def poll(self): return None
        def wait(self, timeout): raise subprocess.TimeoutExpired('owned demo', timeout)
    log = tmp_path / 'log';log.write_text('COMPLETED Step 128\n')
    (tmp_path / 'connection.json').write_text('{"grafana":"http://127.0.0.1:12345"}')
    assert CI.wait_for_fixtures(Live(),log,tmp_path,time.monotonic()+1,comparisons=1)['grafana'].endswith('12345')
