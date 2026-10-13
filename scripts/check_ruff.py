"""Reject new Ruff findings while the recorded Phase 0 violations are retired."""

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).with_name("ruff-legacy.txt")


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "F,E9,B006,B008",
         "--output-format", "json", "xlayer_telemetry", "scripts", "examples", "tests"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if result.returncode not in (0, 1):
        print(result.stderr, file=sys.stderr)
        return result.returncode
    findings = json.loads(result.stdout)
    actual = {
        f"{Path(item['filename']).relative_to(ROOT)}:{item['location']['row']}:"
        f"{item['location']['column']}: {item['code']} {item['message']}"
        for item in findings
    }
    expected = set(BASELINE.read_text().splitlines())
    for label, entries in (("New Ruff finding", actual - expected),
                           ("Retired Ruff finding; remove from baseline", expected - actual)):
        for entry in sorted(entries):
            print(f"{label}: {entry}", file=sys.stderr)
    return int(actual != expected)


if __name__ == "__main__":
    raise SystemExit(main())
