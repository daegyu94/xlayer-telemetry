"""Only live shell jobs may receive sidecar shutdown signals."""

from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[1]


def test_sidecar_ownership_includes_stopped_but_excludes_completed_jobs(tmp_path):
    source = (ROOT / "scripts/run_verl_with_telemetry.sh").read_text()
    ownership = source[source.index("sidecar_is_owned() {"):source.index("sidecar_is_active() {")]
    harness = tmp_path / "job-ownership.sh"
    # Monitor mode exposes stopped status in Bash's job table. Without it,
    # Bash can continue to list a SIGSTOP'ed child as running.
    harness.write_text("set -euo pipefail\nset -m\n" + ownership + r'''
sleep 30 &
child=$!
trap 'kill -KILL "$child" 2>/dev/null || true; wait "$child" 2>/dev/null || true' EXIT
sidecar_is_owned "$child" || { echo "running child is not owned" >&2; exit 1; }

kill -STOP "$child"
# Allow Bash to collect the stopped status before testing its job table.
for ((attempt = 0; attempt < 100; attempt++)); do
  [[ " $(jobs -ps) " == *" $child "* ]] && break
  sleep .01
done
[[ " $(jobs -ps) " == *" $child "* ]] || { echo "fixture child did not stop" >&2; exit 1; }
sidecar_is_owned "$child" || { echo "stopped child is not owned" >&2; exit 1; }

kill -CONT "$child"
kill -TERM "$child"
for ((attempt = 0; attempt < 100; attempt++)); do
  kill -0 "$child" 2>/dev/null || break
  sleep .01
done
if kill -0 "$child" 2>/dev/null; then
  echo "fixture child did not exit" >&2
  exit 1
fi
# Do not wait or consume the job notification: this is precisely the state
# that previously triggered two unnecessary grace periods and stale-PID kills.
[[ " $(jobs -p) " == *" $child "* ]] || { echo "completed job fixture missing" >&2; exit 1; }
if sidecar_is_owned "$child"; then
  echo "completed child is still considered owned" >&2
  exit 1
fi
''')
    result = subprocess.run(["bash", str(harness)], capture_output=True, text=True, timeout=3)
    assert result.returncode == 0, result.stderr
