import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


def test_interrupted_download_preserves_previous_archive(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    archive = tools / "node_exporter.tar.gz"
    archive.write_bytes(b"previous complete archive")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        '#!/bin/sh\nwhile test "$#" -gt 0; do\n'
        '  if test "$1" = --output; then shift; printf partial > "$1"; fi\n'
        '  shift\ndone\nexit 22\n'
    )
    curl.chmod(0o755)
    result = subprocess.run(["bash", str(ROOT / "scripts/install_telemetry_tools.sh"), "node"],
                            env=os.environ | {"TOOLS_DIR": str(tools),
                                              "PATH": f"{bin_dir}:{os.environ['PATH']}"},
                            text=True, capture_output=True, timeout=5)
    assert result.returncode == 22
    assert archive.read_bytes() == b"previous complete archive"
    assert (tools / "node_exporter.tar.gz.part").read_bytes() == b"partial"
    assert not (tools / "downloaded-archives.sha256").exists()


def test_invalid_install_role_does_not_start_downloads(tmp_path):
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/install_telemetry_tools.sh"), "invalid"],
        env=os.environ | {"TOOLS_DIR": str(tmp_path / "tools")}, capture_output=True, text=True)
    assert result.returncode == 2 and "Use node or server" in result.stderr
    assert not (tmp_path / "tools").exists()


def test_installer_requires_tools_dir() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/install_telemetry_tools.sh"), "server"],
        env={"PATH": os.environ["PATH"]},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Set TOOLS_DIR" in result.stderr


@pytest.mark.parametrize(
    ("machine", "release_arch"),
    [("aarch64", "arm64"), ("x86_64", "amd64")],
)
def test_installer_selects_release_architecture(
    tmp_path: Path, machine: str, release_arch: str
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uname").write_text(
        f'#!/bin/sh\ncase "$1" in -s) echo Linux ;; -m) echo {machine} ;; esac\n'
    )
    (bin_dir / "curl").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CURL_LOG"\n'
        'while test "$#" -gt 0; do\n'
        '  if test "$1" = --output; then shift; : > "$1"; fi\n'
        '  shift\n'
        'done\n'
    )
    (bin_dir / "tar").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "unzip").write_text(
        "#!/bin/sh\ntouch loki-linux-amd64 loki-linux-arm64 "
        "alloy-linux-amd64 alloy-linux-arm64\n"
    )
    for path in bin_dir.iterdir():
        path.chmod(0o755)

    curl_log = tmp_path / "curl.log"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/install_telemetry_tools.sh"), "server"],
        env=os.environ
        | {
            "CURL_LOG": str(curl_log),
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "TOOLS_DIR": str(tmp_path / "tools"),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    urls = curl_log.read_text()
    assert f"node_exporter-1.9.1.linux-{release_arch}.tar.gz" in urls
    assert 'smartctl' not in urls
    assert f"prometheus-3.5.0.linux-{release_arch}.tar.gz" in urls
    assert f"grafana-12.1.0.linux-{release_arch}.tar.gz" in urls
    assert f"loki-linux-{release_arch}.zip" in urls


def test_node_installer_downloads_alloy(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uname").write_text(
        '#!/bin/sh\ncase "$1" in -s) echo Linux ;; -m) echo aarch64 ;; esac\n'
    )
    (bin_dir / "curl").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CURL_LOG"\n'
        'while test "$#" -gt 0; do\n'
        '  if test "$1" = --output; then shift; : > "$1"; fi\n'
        '  shift\n'
        'done\n'
    )
    (bin_dir / "tar").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "unzip").write_text(
        "#!/bin/sh\ntouch loki-linux-amd64 loki-linux-arm64 "
        "alloy-linux-amd64 alloy-linux-arm64\n"
    )
    for path in bin_dir.iterdir():
        path.chmod(0o755)

    curl_log = tmp_path / "curl.log"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/install_telemetry_tools.sh"), "node"],
        env=os.environ
        | {
            "CURL_LOG": str(curl_log),
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "TOOLS_DIR": str(tmp_path / "tools"),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "alloy-linux-arm64.zip" in curl_log.read_text()
