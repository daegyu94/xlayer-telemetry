"""Workload cleanup must recheck live session members before escalation."""

from pathlib import Path
import signal

from xlayer_telemetry import owned_shutdown


def _member(proc: Path, pid: int, group: int, marker: bytes) -> None:
    process = proc / str(pid)
    process.mkdir()
    fields = ["S", "1", str(group), str(group)] + ["0"] * 15 + ["100"]
    (process / "stat").write_text(f"{pid} (worker) " + " ".join(fields))
    (process / "environ").write_bytes(marker + b"\0")


def test_group_check_rejects_foreign_and_unreadable_members(tmp_path, monkeypatch):
    marker = b"XLAYER_WORKLOAD_OWNER=mine"
    _member(tmp_path, 41, 41, marker)
    assert owned_shutdown.group_is_owned(41, marker, tmp_path) is True

    _member(tmp_path, 42, 41, b"XLAYER_WORKLOAD_OWNER=someone-else")
    assert owned_shutdown.group_is_owned(41, marker, tmp_path) is None
    (tmp_path / "42/environ").write_bytes(marker + b"\0")
    original = Path.read_bytes

    def unreadable(path):
        if path == tmp_path / "42/environ":
            raise PermissionError("unreadable member")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    assert owned_shutdown.group_is_owned(41, marker, tmp_path) is None


def test_late_child_is_rechecked_before_kill(monkeypatch):
    states = iter([True, True, True, True, False])
    clock = [0.0]
    signals = []
    monkeypatch.setattr(owned_shutdown, "group_is_owned", lambda *_: next(states))
    monkeypatch.setattr(owned_shutdown.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(owned_shutdown.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(owned_shutdown.os, "killpg", lambda group, sig: signals.append((group, sig)))
    owned_shutdown.stop(41, b"owner", grace=0.5)
    assert signals == [(41, signal.SIGKILL)]


def test_reused_group_is_never_signalled(monkeypatch):
    states = iter([True, True, True, None])
    clock = [0.0]
    signals = []
    monkeypatch.setattr(owned_shutdown, "group_is_owned", lambda *_: next(states))
    monkeypatch.setattr(owned_shutdown.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(owned_shutdown.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(owned_shutdown.os, "killpg", lambda group, sig: signals.append((group, sig)))
    owned_shutdown.stop(41, b"owner", grace=0.5)
    assert signals == []


def test_surviving_child_is_reported_after_bounded_wait(monkeypatch, capsys):
    clock = [0.0]
    signals = []
    monkeypatch.setattr(owned_shutdown, "group_is_owned", lambda *_: True)
    monkeypatch.setattr(owned_shutdown.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(owned_shutdown.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(owned_shutdown.os, "killpg", lambda group, sig: signals.append((group, sig)))
    owned_shutdown.stop(41, b"owner", grace=0.5, settle=0.5)
    assert clock[0] == 1.0
    assert signals == [(41, signal.SIGKILL)]
    assert "remain pending after KILL" in capsys.readouterr().err


def test_scan_crossing_settle_deadline_does_not_sleep_negative(monkeypatch):
    clock = [0.0]
    scans = [0]

    def scan(*_):
        scans[0] += 1
        if scans[0] == 3:
            clock[0] = 0.6
        return True

    monkeypatch.setattr(owned_shutdown, "group_is_owned", scan)
    monkeypatch.setattr(owned_shutdown.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(owned_shutdown.time, "sleep", lambda seconds: assert_nonnegative(seconds))
    monkeypatch.setattr(owned_shutdown.os, "killpg", lambda *_: None)

    def assert_nonnegative(seconds):
        assert seconds >= 0

    owned_shutdown.stop(41, b"owner", grace=0, settle=0.5)
    assert scans[0] == 4
