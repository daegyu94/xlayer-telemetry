"""Exercise script path handling and optional service lifecycle without GPU inference."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("name", ["setup.sh", "check_tools.sh", "verl_local.sh",
                                  "local_llm.sh", "install_telemetry_tools.sh"])
def test_help_has_no_installation_or_service_side_effects(tmp_path: Path, name: str) -> None:
    result = subprocess.run(["bash", str(ROOT / "scripts" / name), "--help"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=5,
                            env=os.environ | {"TELEMETRY_HOME": str(tmp_path / "telemetry")})
    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_setup_and_tool_check_use_checkout_from_another_directory(tmp_path: Path) -> None:
    repo = tmp_path / "checkout with spaces"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    for name in ("setup.sh", "check_tools.sh"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    caller = tmp_path / "caller"
    caller.mkdir()
    fake_python = tmp_path / "python"
    calls = tmp_path / "calls.jsonl"
    fake_python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, shutil, sys\n"
        "with open(os.environ['CALLS'], 'a') as f:\n"
        "    f.write(json.dumps({'cwd':os.getcwd(), 'args':sys.argv[1:], "
        "'pythonpath':os.environ.get('PYTHONPATH','')}) + '\\n')\n"
        "if sys.argv[1:3] == ['-m', 'venv']:\n"
        "    root = pathlib.Path(sys.argv[3]).resolve()\n"
        "    (root / 'bin').mkdir(parents=True)\n"
        "    shutil.copy2(sys.argv[0], root / 'bin' / 'python')\n"
        "    (root / 'bin' / 'activate').write_text(f'export PATH=\"{root}/bin:$PATH\"\\n')\n"
    )
    fake_python.chmod(0o755)
    env = os.environ | {"PYTHON": str(fake_python), "CALLS": str(calls)}
    subprocess.run(["bash", str(scripts / "setup.sh")], cwd=caller, env=env,
                   capture_output=True, text=True, check=True, timeout=5)
    assert (repo / ".venv" / "bin" / "python").exists()
    assert not (caller / ".venv").exists()
    records = [json.loads(line) for line in calls.read_text().splitlines()]
    assert all(record["cwd"] == str(repo) for record in records)
    assert records[-1]["args"] == ["-m", "pip", "install", "-r", "requirements.txt", "-e", "."]
    subprocess.run(["bash", str(scripts / "check_tools.sh")], cwd=caller, env=env,
                   capture_output=True, text=True, check=True, timeout=5)
    record = json.loads(calls.read_text().splitlines()[-1])
    assert record["args"] == ["-m", "xlayer_telemetry.tool_check"]
    assert record["pythonpath"].split(os.pathsep)[0] == str(repo)


@pytest.mark.parametrize("port", ["0", "65536", "999999999999999999999"])
def test_local_llm_rejects_invalid_port_before_start(tmp_path: Path, port: str) -> None:
    state = tmp_path / "telemetry"
    result = subprocess.run(["bash", str(ROOT / "scripts/local_llm.sh"), "up"],
                            env=os.environ | {"TELEMETRY_HOME": str(state), "LLM_PORT": port},
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
    assert "LLM_PORT must be between" in result.stderr
    assert not state.exists()


def test_local_llm_serializes_start_and_releases_lock_for_stop(tmp_path: Path) -> None:
    """A real local HTTP process simulates Ollama; no model or GPU is used."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    home = tmp_path / "telemetry"
    executable = home / "tools/ollama-v0.34.4/bin/ollama"
    executable.parent.mkdir(parents=True)
    started, ready = tmp_path / "started", tmp_path / "ready"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import http.server, os, pathlib, sys\n"
        "class Handler(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200 if pathlib.Path(os.environ['READY']).exists() else 503)\n"
        "        self.end_headers(); self.wfile.write(b'{\"version\":\"mock\"}')\n"
        "if sys.argv[1] == 'serve':\n"
        "    address, port = os.environ['OLLAMA_HOST'].split(':')\n"
        "    server = http.server.HTTPServer((address, int(port)), Handler)\n"
        "    pathlib.Path(os.environ['STARTED']).write_text(str(os.getpid()))\n"
        "    server.serve_forever()\n"
        "elif sys.argv[1] == 'ps': print('mock: no inference performed')\n"
    )
    executable.chmod(0o755)
    env = os.environ | {"TELEMETRY_HOME": str(home), "LLM_PORT": str(port), "LLM_GPU": "0",
                        "STARTED": str(started), "READY": str(ready),
                        "http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1",
                        "NO_PROXY": "", "no_proxy": ""}
    command = ["bash", str(ROOT / "scripts/local_llm.sh")]
    up = subprocess.Popen(command + ["up"], env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while not started.exists() and up.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert started.exists(), up.communicate(timeout=5)
        duplicate = subprocess.run(command + ["up"], env=env, capture_output=True,
                                   text=True, timeout=5)
        assert duplicate.returncode == 1
        assert "Another local LLM lifecycle command" in duplicate.stderr
        ready.touch()
        stdout, stderr = up.communicate(timeout=10)
        assert up.returncode == 0, stderr
        assert "Local LLM ready" in stdout
        status = subprocess.run(command + ["status"], env=env, capture_output=True,
                                text=True, timeout=5, check=True)
        assert "no inference performed" in status.stdout
        pid, _, boot = (home / "state/local-llm/server.pid").read_text().split()
        assert int(pid) == int(started.read_text())
        assert boot == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        down = subprocess.run(command + ["down"], env=env, capture_output=True,
                              text=True, timeout=20)
        assert down.returncode == 0, down.stderr
        assert not (home / "state/local-llm/server.pid").exists()
    finally:
        if up.poll() is None:
            up.terminate()
            up.communicate(timeout=20)
        subprocess.run(command + ["down"], env=env, capture_output=True, timeout=20)
