"""Abrupt controller exit must not strand a blocked rule-analysis worker."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from tests._process_helpers import child_processes, process_running, signal_process


@pytest.mark.skipif(sys.platform != 'linux', reason='requires Linux process identity and FIFO state')
@pytest.mark.parametrize('exit_mode', ['sigkill', 'os_exit', 'sigkill_inherited_handle'])
def test_blocked_analysis_worker_exits_with_its_controller(tmp_path, exit_mode):
    os.mkfifo(tmp_path / 'blocked.jsonl')
    script = tmp_path / 'controller.py'
    script.write_text('''from pathlib import Path
import os, sys, threading, time
from xlayer_telemetry.analysis.deadline import IsolatedAnalyzer
if __name__ == '__main__':
    root = Path(sys.argv[1])
    def crash():
        while not (root / 'crash').exists():
            time.sleep(.01)
        os._exit(17)
    threading.Thread(target=crash, daemon=True).start()
    with IsolatedAnalyzer({'prometheus': {'url': 'http://127.0.0.1:1'}}, seconds=30) as analyzer:
        analyzer._start()
        if sys.argv[2] == 'sigkill_inherited_handle':
            sibling = os.fork()
            if sibling == 0:
                time.sleep(30)
                os._exit(0)
            (root / 'sibling').write_text(str(sibling))
        analyzer.prepare(root / 'blocked.jsonl', root / 'output', periodic_when_idle=False)
''')
    owner = subprocess.Popen([sys.executable, str(script), str(tmp_path), exit_mode],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    children = []
    try:
        deadline = time.monotonic() + 5
        blocked = False
        blocked_workers = []
        while time.monotonic() < deadline:
            children = child_processes(owner.pid)
            for child in children:
                try:
                    if Path(f'/proc/{child.pid}/wchan').read_text() == 'wait_for_partner':
                        blocked = True
                        blocked_workers.append(child)
                except (FileNotFoundError, ProcessLookupError):
                    pass
            if blocked:
                break
            time.sleep(.02)
        assert blocked, 'actual analysis worker did not reach the FIFO history read'
        sibling_pid = int((tmp_path / 'sibling').read_text()) if exit_mode == 'sigkill_inherited_handle' else None
        # A forked sibling legitimately retains the resource tracker handle.
        # The blocked analyzer must still exit without killing that sibling.
        workers = blocked_workers if sibling_pid is not None else children
        if exit_mode.startswith('sigkill'):
            owner.kill()
        else:
            (tmp_path / 'crash').touch()
        owner.wait(timeout=3)
        assert owner.returncode == (-signal.SIGKILL if exit_mode.startswith('sigkill') else 17)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and any(process_running(child) for child in workers):
            time.sleep(.02)
        assert not any(process_running(child) for child in workers)
        if sibling_pid is not None:
            assert any(child.pid == sibling_pid and process_running(child) for child in children)
        assert unrelated.poll() is None
        assert not (tmp_path / 'output').exists()
    finally:
        for child in children:
            signal_process(child, signal.SIGKILL)
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=3)
        unrelated.terminate()
        unrelated.wait(timeout=3)
