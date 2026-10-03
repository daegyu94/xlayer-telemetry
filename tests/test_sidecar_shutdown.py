"""A finished workload must not wait indefinitely for a broken owned sidecar."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("sidecar", ["bridge", "health", "diagnostics"])
def test_unresponsive_owned_sidecar_preserves_workload_exit_and_cleanup(tmp_path, sidecar):
    dispatcher = tmp_path / "telemetry-python"
    marker = tmp_path / "sidecar.pid"
    dispatcher.write_text("#!/usr/bin/env python3\n" + textwrap.dedent('''
        import os, signal, sys, time
        args = sys.argv[1:]
        selected = os.environ['TEST_UNRESPONSIVE_SIDECAR']
        is_bridge = 'xlayer_telemetry.adapters.verl' in args and '--follow' in args
        is_health = 'xlayer_telemetry.telemetry_health' in args and '--once' not in args and '--finish' not in args
        is_diagnostics = 'xlayer_telemetry.analysis.diagnostics' in args and '--interval' in args
        if {'bridge': is_bridge, 'health': is_health, 'diagnostics': is_diagnostics}[selected]:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            with open(os.environ['TEST_SIDECAR_PID'], 'w') as stream:
                stream.write(str(os.getpid()))
            while True:
                time.sleep(60)
        os.execv(os.environ['TEST_REAL_PYTHON'], [os.environ['TEST_REAL_PYTHON'], *args])
    '''))
    dispatcher.chmod(0o755)
    workload = tmp_path / "workload.py"
    workload.write_text(textwrap.dedent('''
        import os, pathlib, time
        marker = pathlib.Path(os.environ['TEST_SIDECAR_PID'])
        deadline = time.monotonic() + 5
        while not marker.exists():
            if time.monotonic() >= deadline:
                raise SystemExit(99)
            time.sleep(.01)
        raise SystemExit(7)
    '''))
    output = tmp_path / "run"
    command = ["bash", str(ROOT / "scripts/run_verl_with_telemetry.sh"), "--output", str(output)]
    if sidecar == "diagnostics":
        config = tmp_path / "diagnostics.json"
        config.write_text(json.dumps({"schema_version": 1, "prometheus": {"url": "http://127.0.0.1:1", "timeout_seconds": .01}}))
        command += ["--diagnostics-config", str(config)]
    command += ["--", sys.executable, str(workload)]
    unrelated = subprocess.Popen(["sleep", "30"])
    wrapper = subprocess.Popen(command, cwd=ROOT, env=os.environ | {
        "TELEMETRY_PYTHON": str(dispatcher), "TEST_REAL_PYTHON": sys.executable,
        "TEST_UNRESPONSIVE_SIDECAR": sidecar, "TEST_SIDECAR_PID": str(marker),
    }, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        assert wrapper.wait(timeout=8) == 7
        assert marker.is_file(), "fault injection did not reach the selected sidecar"
        assert not Path(f"/proc/{int(marker.read_text())}").exists()
        assert unrelated.poll() is None
        health = json.loads((output / "telemetry-health.json").read_text())
        assert health["workload"] == {"status": "finished", "exit_code": 7}
    finally:
        # Only fixture-owned processes; release the old implementation's wait
        # after a RED assertion so the failed regression leaves no child behind.
        if marker.is_file():
            try:
                os.kill(int(marker.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            wrapper.wait(timeout=5)
        except subprocess.TimeoutExpired:
            wrapper.kill()
            wrapper.wait(timeout=5)
        if wrapper.stderr is not None:
            wrapper.stderr.close()
        unrelated.terminate()
        unrelated.wait(timeout=5)


@pytest.mark.parametrize("foreign_marker", [None, "another-wrapper"])
def test_stale_sidecar_group_does_not_kill_reused_unrelated_session(tmp_path, foreign_marker):
    import shlex

    env = dict(os.environ)
    env.pop("XLAYER_SIDECAR_OWNER", None)
    if foreign_marker is not None:
        env["XLAYER_SIDECAR_OWNER"] = foreign_marker
    unrelated = subprocess.Popen(["sleep", "30"], start_new_session=True, env=env)
    source = (ROOT / "scripts/run_verl_with_telemetry.sh").read_text()
    # Exercise the actual shutdown functions with a formerly-owned PID now
    # referring to a separate real session. No PID reuse timing is required.
    helpers = source[source.index('bridge_pid=""'):source.index('workload_pid=""')]
    harness = tmp_path / "stale-group.sh"
    harness.write_text("set -euo pipefail\ntelemetry_python=" + shlex.quote(sys.executable) + "\n" +
                       helpers + f"\nstop_sidecar {unrelated.pid}\n")
    try:
        result = subprocess.run(["bash", str(harness)], capture_output=True, text=True, timeout=9)
        assert result.returncode == 0, result.stderr
        assert unrelated.poll() is None, "a stale PGID was treated as an owned sidecar group"
    finally:
        if unrelated.poll() is None:
            unrelated.terminate()
        unrelated.wait(timeout=5)


def _running(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def _kill(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_exited_sidecar_leader_does_not_leave_its_worker_running(tmp_path):
    marker = tmp_path / "worker.pid"
    dispatcher = tmp_path / "telemetry-python"
    dispatcher.write_text("#!/usr/bin/env python3\n" + textwrap.dedent('''
        import os, subprocess, sys
        args = sys.argv[1:]
        if 'xlayer_telemetry.adapters.verl' in args and '--follow' in args:
            subprocess.Popen([os.environ['TEST_REAL_PYTHON'], '-c',
                'import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                'open(os.environ["TEST_WORKER_PID"],"w").write(str(os.getpid())); time.sleep(60)'])
            raise SystemExit(0)
        os.execv(os.environ['TEST_REAL_PYTHON'], [os.environ['TEST_REAL_PYTHON'], *args])
    '''))
    dispatcher.chmod(0o755)
    workload = tmp_path / "workload.py"
    workload.write_text(textwrap.dedent('''
        import os, pathlib, time
        marker = pathlib.Path(os.environ['TEST_WORKER_PID'])
        deadline = time.monotonic() + 5
        while not marker.exists():
            if time.monotonic() >= deadline: raise SystemExit(99)
            time.sleep(.01)
        raise SystemExit(7)
    '''))
    wrapper = subprocess.Popen(["bash", str(ROOT / "scripts/run_verl_with_telemetry.sh"),
        "--output", str(tmp_path / "run"), "--", sys.executable, str(workload)], cwd=ROOT,
        env=os.environ | {"TELEMETRY_PYTHON": str(dispatcher), "TEST_REAL_PYTHON": sys.executable,
                          "TEST_WORKER_PID": str(marker)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert wrapper.wait(timeout=9) == 7
        assert marker.is_file()
        assert not _running(int(marker.read_text())), "sidecar leader exited but its child survived"
    finally:
        if marker.is_file():
            _kill(int(marker.read_text()))
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=5)


@pytest.mark.skipif(not Path("/proc/self/task").exists(), reason="Linux process ownership validation")
def test_forced_diagnostics_shutdown_stops_actual_blocked_analyzer(tmp_path):
    import time

    output = tmp_path / "run"
    (output / "telemetry-events").mkdir(parents=True)
    history = output / "telemetry-events/verl-steps.jsonl"
    os.mkfifo(history)
    config = tmp_path / "diagnostics.json"
    config.write_text(json.dumps({"schema_version": 1, "prometheus": {"url": "http://127.0.0.1:1"},
                                  "analysis_deadline_seconds": 30}))
    release = tmp_path / "release"
    workload = tmp_path / "workload.py"
    workload.write_text("import pathlib,sys,time\np=pathlib.Path(sys.argv[1])\n"
                        "while not p.exists(): time.sleep(.01)\nraise SystemExit(7)\n")
    wrapper = subprocess.Popen(["bash", str(ROOT / "scripts/run_verl_with_telemetry.sh"),
        "--output", str(output), "--diagnostics-config", str(config),
        "--", sys.executable, str(workload), str(release)], cwd=ROOT,
        env=os.environ | {"TELEMETRY_PYTHON": sys.executable},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    unrelated = subprocess.Popen(["sleep", "30"])
    diagnostics = None
    descendants = []
    try:
        deadline = time.monotonic() + 8
        blocked = False
        while time.monotonic() < deadline:
            for pid in Path(f"/proc/{wrapper.pid}/task/{wrapper.pid}/children").read_text().split():
                try:
                    args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                    if b"xlayer_telemetry.analysis.diagnostics" not in args or b"--interval" not in args:
                        continue
                    diagnostics = int(pid)
                    descendants = [int(child) for child in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()]
                    blocked = any(Path(f"/proc/{child}/wchan").read_text() == "wait_for_partner" for child in descendants)
                except FileNotFoundError:
                    continue
            if blocked:
                break
            time.sleep(.02)
        assert blocked, "actual analyzer did not reach the injected FIFO read"
        os.kill(diagnostics, signal.SIGSTOP)
        # The already-blocked worker keeps the old inode open. Remove only the
        # fixture pathname so final export can run without another blocked read.
        history.unlink()
        release.touch()
        assert wrapper.wait(timeout=9) == 7
        assert all(not _running(pid) for pid in descendants), "analyzer/resource tracker survived its controller"
        assert unrelated.poll() is None
    finally:
        for pid in descendants:
            _kill(pid)
        if diagnostics is not None:
            _kill(diagnostics)
        release.touch()
        if wrapper.poll() is None:
            try:
                wrapper.wait(timeout=5)
            except subprocess.TimeoutExpired:
                wrapper.kill()
                wrapper.wait(timeout=5)
        unrelated.terminate()
        unrelated.wait(timeout=5)
