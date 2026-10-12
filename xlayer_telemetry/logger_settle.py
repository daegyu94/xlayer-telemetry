"""Sample a freshly opened logger until quiet, with a monotonic wait budget."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import time

from .fileio import atomic_write_text


def validate_policy(quiet_seconds: float, max_wait_seconds: float) -> None:
    if not math.isfinite(quiet_seconds) or not 0 < quiet_seconds <= 600:
        raise ValueError("TELEMETRY_LOGGER_SETTLE_SECONDS must be finite and in (0, 600].")
    if not math.isfinite(max_wait_seconds) or not quiet_seconds <= max_wait_seconds <= 3600:
        raise ValueError("TELEMETRY_LOGGER_MAX_WAIT_SECONDS must be finite, at least TELEMETRY_LOGGER_SETTLE_SECONDS, and at most 3600.")


def wait_for_logger(path: Path, *, quiet_seconds: float, max_wait_seconds: float) -> dict:
    """Quiet is sampled visibility, never proof that remote writers finished."""
    validate_policy(quiet_seconds, max_wait_seconds)
    began = time.monotonic()
    changed_at = began
    previous = None
    size = None
    while True:
        try:
            # A held FUSE descriptor can retain an old peer-node view. Read a
            # bounded suffix from a fresh open, not stat-only cached metadata.
            with path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                stream.seek(max(0, info.st_size - 256))
                tail = stream.read(256)
            signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, tail)
            size = info.st_size
        except FileNotFoundError:
            signature, size, tail = None, None, b""
        except OSError:
            return {"status": "failed", "reason": "logger_unreadable",
                    "waited_seconds": time.monotonic() - began}
        now = time.monotonic()
        if signature != previous or signature is None:
            previous = signature
            changed_at = now
        quiet = now - changed_at
        elapsed = now - began
        result = {"observed_at": time.time(), "quiet_seconds": quiet_seconds,
                  "max_wait_seconds": max_wait_seconds, "waited_seconds": elapsed,
                  "observed_quiet_seconds": quiet, "size_bytes": size,
                  "coverage": "sampled_logger_visibility_not_writer_completion"}
        if signature is not None and quiet >= quiet_seconds:
            complete = bool(tail) and tail.endswith(b"\n")
            return {**result, "status": "settled" if complete else "incomplete",
                    "reason": "quiet_complete_line" if complete else "empty_or_unterminated_logger"}
        if elapsed >= max_wait_seconds:
            return {**result, "status": "timeout",
                    "reason": "logger_missing" if signature is None else "logger_not_quiet"}
        time.sleep(min(.2, quiet_seconds, max_wait_seconds - elapsed))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quiet-seconds", type=float, default=20)
    parser.add_argument("--max-wait-seconds", type=float, default=120)
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()
    try:
        validate_policy(args.quiet_seconds, args.max_wait_seconds)
    except ValueError as error:
        parser.error(str(error))
    if args.check_config:
        return
    if args.input is None or args.output is None:
        parser.error("--input and --output are required")
    result = wait_for_logger(args.input, quiet_seconds=args.quiet_seconds,
                             max_wait_seconds=args.max_wait_seconds)
    atomic_write_text(args.output, json.dumps(result, indent=2) + "\n")
    raise SystemExit({"settled": 0, "timeout": 3, "incomplete": 4, "failed": 1}[result["status"]])


if __name__ == "__main__":
    main()
