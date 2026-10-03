"""Inspect Linux fixture processes without requiring CONFIG_PROC_CHILDREN."""
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Process:
    pid: int
    parent_pid: int
    state: str
    start_time: int


def read_process(pid, proc_root=Path("/proc")):
    try:
        # comm can contain spaces and closing parentheses. The remaining
        # fields begin at field 3 (state); starttime is field 22.
        fields = (proc_root / str(pid) / "stat").read_text().rsplit(") ", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return Process(int(pid), int(fields[1]), fields[0], int(fields[19]))


def child_processes(parent_pid, proc_root=Path("/proc")):
    # /proc/PID/task/PID/children is optional even on Linux. PPID in stat is
    # available on minimal procfs implementations and gives the same direct
    # child relationship. Never substitute a global command-name search.
    children = []
    for entry in proc_root.iterdir():
        if entry.name.isdecimal():
            process = read_process(int(entry.name), proc_root)
            if process is not None and process.parent_pid == parent_pid:
                children.append(process)
    return sorted(children, key=lambda process: process.pid)


def process_exists(process, proc_root=Path("/proc")):
    current = read_process(process.pid, proc_root)
    return current is not None and current.start_time == process.start_time


def process_running(process, proc_root=Path("/proc")):
    current = read_process(process.pid, proc_root)
    return (current is not None and current.start_time == process.start_time
            and current.state != "Z")


def signal_process(process, signum, proc_root=Path("/proc")):
    # Fixture cleanup must not act on a PID already reused by another process.
    if process_exists(process, proc_root):
        try:
            os.kill(process.pid, signum)
        except ProcessLookupError:
            pass
