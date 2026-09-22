#!/usr/bin/env bash
set -euo pipefail

if [[ ! -d .venv ]]; then
  printf 'Missing .venv; run ./scripts/setup.sh first.\n' >&2
  exit 1
fi

. .venv/bin/activate
python -m xlayer_telemetry.tool_check
