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

    assert install() == 3
    assert install() == 3
    env["TEST_ARCH"] = "aarch64"
    assert install() == 6, "AMD64 cache must not satisfy an ARM64 download"
    script.write_text(script.read_text().replace("v1.19.2", "v1.19.3"))
    assert install() == 7, "a previous release must not satisfy an updated URL"


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
