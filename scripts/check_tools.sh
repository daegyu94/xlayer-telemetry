#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 0 ]]; then
  echo 'Usage: bash scripts/check_tools.sh (reports optional tools on PATH)'
  [[ $# == 1 && ( "$1" == --help || "$1" == -h ) ]] && exit 0
  exit 2
fi
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$repo_root/.venv/bin/python" ]]; then
  printf 'Missing telemetry Python; run %s/scripts/setup.sh first.\n' "$repo_root" >&2
  exit 1
fi

PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" \
  "$repo_root/.venv/bin/python" -m xlayer_telemetry.tool_check
