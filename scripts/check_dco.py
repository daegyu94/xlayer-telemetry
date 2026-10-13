"""Check that each new commit carries its author's DCO sign-off."""

import re
import subprocess
import sys


def check(base: str, head: str) -> list[str]:
    revision = head if not base or set(base) == {"0"} else f"{base}..{head}"
    output = subprocess.check_output(
        ["git", "log", "-z", "--format=%H%x00%ae%x00%B", revision]
    )
    fields = output.decode("utf-8", errors="replace").rstrip("\0").split("\0")
    failures = []
    for index in range(0, len(fields) - 2, 3):
        commit, author_email, message = fields[index : index + 3]
        trailers = message.strip().split("\n\n")[-1].splitlines()
        signed = any(
            match and match.group(1).casefold() == author_email.casefold()
            for line in trailers
            if (match := re.fullmatch(r"Signed-off-by: .+ <([^<>\s]+)>\s*", line))
        )
        if not signed:
            failures.append(commit)
    return failures


if __name__ == "__main__":
    failures = check(sys.argv[1], sys.argv[2])
    for sha in failures:
        print(f"Missing author Signed-off-by trailer: {sha}", file=sys.stderr)
    sys.exit(bool(failures))
