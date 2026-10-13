#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
profile=${1:-default}
case "$profile" in
  fast|default|full) [[ $# -eq 0 ]] || shift ;;
  -h|--help) echo 'Usage: bash scripts/check.sh [fast|default|full] [tests/test_*.py ...]'; exit 0 ;;
  *) echo "Unknown check profile: $profile" >&2; exit 2 ;;
esac

if [[ -n ${PYTHON:-} ]]; then
  python_cmd=$PYTHON
elif [[ -x $repo_root/.venv/bin/python ]]; then
  python_cmd="$repo_root/.venv/bin/python"
else
  common_dir=$(git rev-parse --path-format=absolute --git-common-dir)
  python_cmd="$(dirname "$common_dir")/.venv/bin/python"
fi
command -v "$python_cmd" >/dev/null || {
  echo 'Development Python is missing: run bash scripts/setup.sh or set PYTHON.' >&2
  exit 2
}
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
mapfile -t changed < <({ git diff --name-only HEAD; git ls-files --others --exclude-standard; } | sort -u)
tests=("$@")
if (( ${#tests[@]} == 0 )) && [[ -n ${CHECK_TESTS:-} ]]; then
  read -r -a tests <<< "$CHECK_TESTS"
fi
if (( ${#tests[@]} == 0 )); then
  for path in "${changed[@]}"; do
    [[ $path == tests/test_*.py ]] && tests+=("$path")
  done
  if (( ${#tests[@]} == 0 )); then
    for path in "${changed[@]}"; do
      case "$path" in
        xlayer_telemetry/*|examples/*|scripts/*|config/*|grafana/xlayer-app/*)
          echo "No related tests selected for $path; pass test paths or set CHECK_TESTS." >&2
          exit 2 ;;
      esac
    done
    tests=(tests/test_schema.py tests/test_artifact_reader_contracts.py)
  fi
fi
for path in "${tests[@]}"; do
  test_file=${path%%::*}
  if [[ $test_file != tests/test_*.py || ! -f $test_file ]]; then
    echo "Missing or invalid test: $path" >&2
    exit 2
  fi
done

"$python_cmd" scripts/check_ruff.py
"$python_cmd" scripts/metric_coverage.py --check
"$python_cmd" -m pytest -q "${tests[@]}"
[[ $profile == fast ]] && exit 0

git diff --check HEAD
for path in "${changed[@]}"; do
  [[ -f $path ]] || continue
  case "$path" in
    *.py) "$python_cmd" -m py_compile "$path" ;;
    *.sh) bash -n "$path" ;;
    docs/diagrams/*.d2) "$python_cmd" scripts/render_diagrams.py --check ;;
  esac
done
app_checked=0
for path in "${changed[@]}"; do
  if [[ $path == grafana/xlayer-app/* ]]; then
    npm test --prefix grafana/xlayer-app
    npm run typecheck --prefix grafana/xlayer-app
    npm run build --prefix grafana/xlayer-app
    app_checked=1
    break
  fi
done
[[ $profile == default ]] && exit 0

[[ -n ${PROMTOOL:-} && -x ${PROMTOOL:-} ]] || { echo 'full requires PROMTOOL.' >&2; exit 2; }
[[ -n ${XLAYER_TEST_CLICKHOUSE_CONTAINER:-} || -n ${XLAYER_TEST_CLICKHOUSE_URL:-} ]] || { echo 'full requires an owned ClickHouse container or loopback URL.' >&2; exit 2; }
[[ -n ${XLAYER_CHECK_TOOLS:-} && -d ${XLAYER_CHECK_TOOLS:-} ]] || { echo 'full requires XLAYER_CHECK_TOOLS.' >&2; exit 2; }
[[ -n ${XLAYER_CHECK_OUTPUT:-} && ! -e ${XLAYER_CHECK_OUTPUT:-} ]] || { echo 'full requires a new XLAYER_CHECK_OUTPUT directory.' >&2; exit 2; }
if (( ! app_checked )); then
  npm test --prefix grafana/xlayer-app
  npm run typecheck --prefix grafana/xlayer-app
  npm run build --prefix grafana/xlayer-app
fi
"$python_cmd" -m pytest -q
"$python_cmd" -m pytest -q grafana/xlayer-app/scripts
"$python_cmd" -m compileall -q xlayer_telemetry examples
git ls-files -z '*.sh' | xargs -0 -r -n 1 bash -n
"$python_cmd" scripts/render_diagrams.py --check
bash scripts/docs.sh build
"$python_cmd" grafana/xlayer-app/scripts/ci_demo.py --tools "$XLAYER_CHECK_TOOLS" \
  --state "$XLAYER_CHECK_OUTPUT/state" --output "$XLAYER_CHECK_OUTPUT/result"
