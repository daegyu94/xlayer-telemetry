"""Bounded cleanup for a workload's private process session."""

import os
from pathlib import Path
import signal
import sys
import time


def group_is_owned(group: int, marker: bytes, proc: Path = Path("/proc")) -> bool | None:
    """Return None when a live member cannot be verified safely."""
    found = False
    try:
        for process in proc.iterdir():
            if not process.name.isdecimal():
                continue
            try:
                stat = (process / "stat").read_text().rsplit(") ", 1)[1].split()
                if int(stat[2]) != group or int(stat[3]) != group or stat[0] == "Z":
                    continue
                if marker not in (process / "environ").read_bytes().split(b"\0"):
                    return None
                # A PID can be reused between reading stat and environ.
                if (process / "stat").read_text().rsplit(") ", 1)[1].split()[19] != stat[19]:
                    return None
                found = True
            except FileNotFoundError:
                continue
    except (OSError, ValueError, IndexError):
        return None
    return found


def stop(group: int, marker: bytes, grace: float = 5.0, settle: float = 2.0) -> None:
    deadline = time.monotonic() + grace
    while True:
        owned = group_is_owned(group, marker)
        if owned is None:
            print("[telemetry] refusing group escalation: ownership could not be verified", file=sys.stderr)
            return
        if not owned:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.25, remaining))

    # This scan includes children forked during the grace period.
    owned = group_is_owned(group, marker)
    if owned is None:
        print("[telemetry] refusing group escalation: ownership could not be verified", file=sys.stderr)
        return
    if not owned:
        return
    print("[telemetry] workload descendants did not stop; sending KILL to the owned session", file=sys.stderr)
    try:
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        print("[telemetry] workload group could not be signalled", file=sys.stderr)
        return
    deadline = time.monotonic() + settle
    while time.monotonic() < deadline:
        owned = group_is_owned(group, marker)
        if owned is not True:
            return
        time.sleep(min(0.25, deadline - time.monotonic()))
    if group_is_owned(group, marker) is True:
        print("[telemetry] workload descendants remain pending after KILL", file=sys.stderr)


if __name__ == "__main__":
    stop(int(sys.argv[1]), ("XLAYER_WORKLOAD_OWNER=" + sys.argv[2]).encode())
