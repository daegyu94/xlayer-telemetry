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
