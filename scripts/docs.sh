#!/usr/bin/env bash
# Optional documentation environment; independent from the telemetry runtime.
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
docs_env=${DOCS_ENV:-"$repo_root/.venv-docs"}
site_dir="$repo_root/artifacts/docs-site"

case "${1:---help}" in
  install)
    "${PYTHON:-python3}" -m venv "$docs_env"
    "$docs_env/bin/python" -m pip install -r "$repo_root/requirements-docs.txt"
    ;;
  build|serve)
    [[ -x "$docs_env/bin/python" ]] || {
      echo "Run bash scripts/docs.sh install first (Python 3.11+)." >&2
      exit 2
    }
    "$docs_env/bin/python" -m sphinx -n -W --keep-going -b html "$repo_root/docs" "$site_dir"
    if [[ "$1" == serve ]]; then
      exec "$docs_env/bin/python" -m http.server "${DOCS_PORT:-18080}" --bind 127.0.0.1 --directory "$site_dir"
    fi
    ;;
  -h|--help)
    echo "Usage: bash scripts/docs.sh {install|build|serve}"
    echo "Optional: PYTHON, DOCS_ENV, DOCS_PORT (default: 18080)"
    ;;
  *) echo "Unknown command: $1" >&2; exit 2 ;;
esac
