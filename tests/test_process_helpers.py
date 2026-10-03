import signal

from tests._process_helpers import child_processes, process_exists, process_running, read_process, signal_process


def _stat(root, pid, parent_pid, *, state="S", start_time=100):
    directory = root / str(pid)
    directory.mkdir(exist_ok=True)
    # Include spaces and a closing parenthesis in comm to exercise parsing.
    fields = [state, str(parent_pid), *(["0"] * 17), str(start_time)]
    (directory / "stat").write_text(f"{pid} (fixture ) worker) " + " ".join(fields))


def test_child_discovery_uses_parent_relationship_without_task_children(tmp_path):
    _stat(tmp_path, 10, 1)
    _stat(tmp_path, 11, 10)
    _stat(tmp_path, 12, 10)
    _stat(tmp_path, 13, 11)  # Grandchild is not a direct child.
    _stat(tmp_path, 14, 99)  # Matching command name does not imply ownership.
    (tmp_path / "15").mkdir()  # A process can disappear during enumeration.
    (tmp_path / "self").mkdir()
    assert not (tmp_path / "10/task/10/children").exists()
    assert [process.pid for process in child_processes(10, tmp_path)] == [11, 12]


def test_process_identity_distinguishes_reaped_zombie_and_reused_pids(tmp_path, monkeypatch):
    _stat(tmp_path, 11, 10)
    process = read_process(11, tmp_path)
    assert process_exists(process, tmp_path)
    assert process_running(process, tmp_path)
    calls = []
    monkeypatch.setattr("tests._process_helpers.os.kill", lambda *args: calls.append(args))
    signal_process(process, signal.SIGTERM, tmp_path)
    assert calls == [(11, signal.SIGTERM)]
    _stat(tmp_path, 11, 1, state="Z")
    assert process_exists(process, tmp_path)
    assert not process_running(process, tmp_path)
    _stat(tmp_path, 11, 99, start_time=101)
    assert not process_exists(process, tmp_path)
    assert not process_running(process, tmp_path)
    signal_process(process, signal.SIGKILL, tmp_path)
    assert calls == [(11, signal.SIGTERM)]
    (tmp_path / "11/stat").unlink()
    assert not process_exists(process, tmp_path)
