"""Installer retry reuses complete, locally verified archives only."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).parents[1]


def test_installer_reuses_valid_cache_and_redownloads_corruption(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "curl").write_text("""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$CURL_LOG"
while (( $# )); do
  if [[ $1 == --output ]]; then printf 'test archive' > "$2"; break; fi
  shift
done
""")
    (fake / "tar").write_text("#!/usr/bin/env bash\nexit 0\n")
    from xlayer_telemetry.grafana_release import release
    import platform
    arch='arm64' if platform.machine() in {'aarch64','arm64'} else 'amd64'
    digest=release(arch=arch)['sha256']
    # Emulate a publisher-verified fixture only for its known test bytes;
    # a corrupted cache still computes its actual hash and must redownload.
    (fake/'sha256sum').write_text(f'''#!/usr/bin/env bash
if [[ $# == 1 && $1 == grafana.tar.gz* ]] && [[ $(cat "$1") == 'test archive' ]]; then
  printf '%s  %s\\n' '{digest}' "$1"
else
  /usr/bin/sha256sum "$@" | while read -r hash name; do
    if [[ $name == ./grafana.tar.gz ]] && [[ $(cat "$name") == 'test archive' ]]; then hash='{digest}'; fi
    printf '%s  %s\\n' "$hash" "$name"
  done
fi
''')

    (fake / "unzip").write_text("#!/usr/bin/env bash\ntouch loki-linux-amd64 alloy-linux-amd64 loki-linux-arm64 alloy-linux-arm64\n")
    for binary in fake.iterdir():
        binary.chmod(0o755)
    tools = tmp_path / "tools"
    log = tmp_path / "curl.log"
    env = os.environ | {"PATH": str(fake) + ":" + os.environ["PATH"],
                        "TOOLS_DIR": str(tools), "CURL_LOG": str(log)}
    argv = ["bash", str(ROOT / "scripts/install_telemetry_tools.sh"), "server"]
    def install():
        result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        return result
    install()
    count = len(log.read_text().splitlines())
    result = install()
    assert len(log.read_text().splitlines()) == count
    assert result.stdout.count("Using verified cached archive") == count
    (tools / "grafana.tar.gz").write_text("corrupt archive")
    install()
    assert len(log.read_text().splitlines()) == count + 1


def test_installer_cache_requires_same_release_and_architecture(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "uname").write_text('#!/bin/sh\ncase "$1" in -s) echo Linux ;; -m) echo "$TEST_ARCH" ;; esac\n')
    (fake / "curl").write_text('''#!/usr/bin/env bash
printf '%s\\n' "${@: -1}" >> "$CURL_LOG"
while (( $# )); do
  if [[ $1 == --output ]]; then printf 'test archive' > "$2"; break; fi
  shift
done
''')
    (fake / "tar").write_text("#!/usr/bin/env bash\nexit 0\n")
    (fake / "unzip").write_text("#!/usr/bin/env bash\ntouch alloy-linux-amd64 alloy-linux-arm64\n")
    for binary in fake.iterdir():
        binary.chmod(0o755)
    tools = tmp_path / "tools"
    log = tmp_path / "curl.log"
    script = tmp_path / "installer.sh"
    script.write_text((ROOT / "scripts/install_telemetry_tools.sh").read_text())
    env = os.environ | {"PATH": str(fake) + ":" + os.environ["PATH"],
                        "TOOLS_DIR": str(tools), "CURL_LOG": str(log), "TEST_ARCH": "x86_64"}

    def install():
        result = subprocess.run(["bash", str(script), "node"], env=env,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        return len(log.read_text().splitlines())

    assert install() == 2
    assert install() == 2
    env["TEST_ARCH"] = "aarch64"
    assert install() == 4, "AMD64 cache must not satisfy an ARM64 download"
    script.write_text(script.read_text().replace("v1.19.2", "v1.19.3"))
    assert install() == 5, "a previous release must not satisfy an updated URL"


def test_interrupted_archive_is_not_resumed_from_another_url(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "curl").write_text('''#!/usr/bin/env bash
while (( $# )); do
  if [[ $1 == --output ]]; then
    if [[ -e $2 ]]; then cp "$2" "$RESUMED_CONTENT"; fi
    printf 'partial-new-archive' > "$2"
    exit 22
  fi
  shift
done
''')
    (fake / "curl").chmod(0o755)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "node_exporter.tar.gz.part").write_text("partial from a different architecture")
    (tools / "node_exporter.tar.gz.part.source-url").write_text("https://example.invalid/old-release\n")
    marker = tmp_path / "resumed-content"
    result = subprocess.run(["bash", str(ROOT / "scripts/install_telemetry_tools.sh"), "node"],
        env=os.environ | {"PATH": str(fake) + ":" + os.environ["PATH"], "TOOLS_DIR": str(tools),
                          "RESUMED_CONTENT": str(marker)}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 22
    assert not marker.exists(), "curl must not append a new URL's bytes to an unrelated partial archive"


def test_public_install_uses_selected_python_outside_checkout(tmp_path, monkeypatch):
    """A staged wheel has no checkout PYTHONPATH and no activated environment."""
    import venv
    import sys
    from setuptools import build_meta
    import shutil
    source = tmp_path / 'source'
    source.mkdir()
    for name in ('pyproject.toml', 'README.md'):
        shutil.copy2(ROOT/name, source/name)
    for name in ('xlayer_telemetry', 'scripts', 'config', 'examples/dashboards', 'examples/live-demo'):
        shutil.copytree(ROOT/name, source/name, ignore=shutil.ignore_patterns('__pycache__'))
    monkeypatch.chdir(source)
    wheel_dir = tmp_path / 'wheel'
    wheel_dir.mkdir()
    wheel = wheel_dir / build_meta.build_wheel(str(wheel_dir))
    environment = tmp_path / 'environment'
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / 'bin/python'
    if sys.version_info < (3, 11):
        # Reuse the already-installed declared runtime dependency offline.
        import tomli
        site = subprocess.check_output([str(python), '-c',
            'import sysconfig; print(sysconfig.get_paths()["purelib"])'], text=True).strip()
        shutil.copytree(Path(tomli.__file__).parent, Path(site)/'tomli')
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-deps', str(wheel)],
                   check=True, capture_output=True, timeout=30)
    fake = tmp_path / 'bin'
    fake.mkdir()
    # Stop after interpreter selection; no network or archives are required.
    (fake / 'curl').write_text('''#!/usr/bin/env bash
while (( $# )); do
 if [[ $1 == --output ]]; then printf test > "$2"; break; fi
 shift
done
''')
    (fake / 'tar').write_text('#!/bin/sh\nexit 0\n')
    (fake / 'python3').write_text('#!/bin/sh\necho wrong-python >&2\nexit 91\n')
    # The real selected Python resolves the packaged release module. Its URL
    # arrives at this download, where a fixture intentionally ends installation.
    (fake / 'sha256sum').write_text('#!/bin/sh\ncase "$1" in grafana.tar.gz.part) echo selected-python-reached >&2; exit 73;; *) exec /usr/bin/sha256sum "$@";; esac\n')
    for path in fake.iterdir(): path.chmod(0o755)
    config = tmp_path / 'config'
    config.write_text(f'TELEMETRY_HOME={str(tmp_path / "state")!r}\n')
    env = {key: value for key, value in os.environ.items()
           if key not in {'PYTHONPATH', 'PYTHON', 'TELEMETRY_PYTHON', 'VIRTUAL_ENV'}}
    env['PATH'] = str(fake) + ':/usr/bin:/bin'
    result = subprocess.run([str(environment / 'bin/xltel'), '--config', str(config),
                             'install-tools', '--role', 'server'], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 73, result.stdout + result.stderr
    assert 'selected-python-reached' in result.stderr
    assert 'wrong-python' not in result.stderr
