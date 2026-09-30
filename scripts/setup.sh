#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 0 ]]; then
  echo 'Usage: bash scripts/setup.sh (optional environment: PYTHON=python3.12)'
  [[ $# == 1 && ( "$1" == --help || "$1" == -h ) ]] && exit 0
  exit 2
fi
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
"${PYTHON:-python3}" -c 'import sys; sys.version_info >= (3, 10) or sys.exit("Python 3.10 or newer is required")'
"${PYTHON:-python3}" -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -e .
