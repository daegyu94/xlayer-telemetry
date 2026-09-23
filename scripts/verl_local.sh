#!/usr/bin/env bash
# Run the single-host VERL walkthrough from one local Bash config.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
usage() {
  cat <<'EOF'
Usage: bash scripts/verl_local.sh --config FILE install|server|node|run

Use one trusted local Bash config for all four commands.
Run server, node, and run in separate terminals from the checkout root.
EOF
}

if [[ $# -ne 3 || "$1" != --config ]]; then
  usage >&2
  exit 2
fi
config_file="$2"
action="$3"
if [[ "$config_file" != /* ]]; then
  config_file="$PWD/$config_file"
fi
if [[ ! -f "$config_file" ]]; then
  echo "Config file not found: $config_file" >&2
  exit 2
fi
# Like server.conf, this file is sourced as Bash: use only a trusted local file.
source "$config_file"

: "${RUN_ID:?Set RUN_ID in the config file}"
if [[ ! "$RUN_ID" =~ ^[A-Za-z0-9_.-]{1,64}$ ]]; then
  echo "RUN_ID must be 1-64 letters, digits, dots, underscores, or hyphens" >&2
  exit 2
fi
telemetry_home="${TELEMETRY_HOME:-$HOME/telemetry}"
tools_dir="${TOOLS_DIR:-$telemetry_home/tools}"
run_root="${RUN_ROOT:-$HOME/telemetry-runs/$RUN_ID}"
node_name="${NODE_NAME:-gpu-local}"
node_addr="${NODE_ADDR:-127.0.0.1}"
cluster_name="${CLUSTER_NAME:-training-cluster}"
telemetry_python="${TELEMETRY_PYTHON:-$repo_root/.venv/bin/python}"
enable_logs="${ENABLE_LOGS:-0}"
if [[ "$run_root" != /* || "$telemetry_home" != /* || "$tools_dir" != /* ]]; then
  echo "RUN_ROOT, TELEMETRY_HOME, and TOOLS_DIR must be absolute paths" >&2
  exit 2
fi
if [[ "$enable_logs" != 0 && "$enable_logs" != 1 ]]; then
  echo "ENABLE_LOGS must be 0 or 1" >&2
  exit 2
fi
if [[ "$action" != install && ! -x "$telemetry_python" ]]; then
  echo "Telemetry Python not found: $telemetry_python (run scripts/setup.sh)" >&2
  exit 2
fi

case "$action" in
  install)
    TOOLS_DIR="$tools_dir" bash "$repo_root/scripts/install_telemetry_tools.sh" node
    TOOLS_DIR="$tools_dir" bash "$repo_root/scripts/install_telemetry_tools.sh" server
    ;;
  server)
    TOOLS_DIR="$tools_dir" \
    OUTPUT_DIR="${SERVER_OUTPUT_DIR:-$telemetry_home/state/server}" \
    CLUSTER_NAME="$cluster_name" \
    TELEMETRY_TARGETS="$node_name=$node_addr" \
    TELEMETRY_SOURCES_FILE="${TELEMETRY_SOURCES_FILE:-}" \
    ENABLE_ALERTS="${ENABLE_ALERTS:-0}" \
    ENABLE_LOGS="$enable_logs" \
    LOKI_LISTEN_ADDR='127.0.0.1' \
    PYTHON="$telemetry_python" \
      bash "$repo_root/scripts/run_telemetry.sh" server
    ;;
  node)
    mkdir -p "$(dirname "$run_root")"
    loki_push_url=''
    telemetry_log_roots=''
    if [[ "$enable_logs" == 1 ]]; then
      loki_push_url='http://127.0.0.1:13100/loki/api/v1/push'
      telemetry_log_roots="verl=$(dirname "$run_root")"
    fi
    node_args=(
      "TOOLS_DIR=$tools_dir"
      "OUTPUT_DIR=${NODE_OUTPUT_DIR:-$telemetry_home/state/node}"
      "NODE_ADDR=$node_addr"
      "NODE_NAME=$node_name"
      "CLUSTER_NAME=$cluster_name"
      "TELEMETRY_METRICS_DIR=$run_root/telemetry-metrics"
      "PYTHON=$telemetry_python"
      "LOKI_PUSH_URL=$loki_push_url"
      "TELEMETRY_LOG_ROOTS=$telemetry_log_roots"
    )
    env "${node_args[@]}" bash "$repo_root/scripts/run_telemetry.sh" node
    ;;
  run)
    command_decl="$(declare -p VERL_COMMAND 2>/dev/null || true)"
    if [[ "$command_decl" != 'declare -a '* ]]; then
      echo "Set VERL_COMMAND=(...) as a Bash array in the config file" >&2
      exit 2
    fi
    if (( ${#VERL_COMMAND[@]} == 0 )) || [[ "${VERL_COMMAND[0]}" == /path/to/* ]]; then
      echo "Replace VERL_COMMAND with your executable VERL recipe" >&2
      exit 2
    fi
    extra_args=()
    if [[ -n "${DIAGNOSTICS_CONFIG:-}" ]]; then
      extra_args+=(--diagnostics-config "$DIAGNOSTICS_CONFIG")
    fi
    TELEMETRY_PYTHON="$telemetry_python" \
      bash "$repo_root/scripts/run_verl_with_telemetry.sh" \
        --output "$run_root" --run-id "$RUN_ID" --node "$node_name" \
        "${extra_args[@]}" -- "${VERL_COMMAND[@]}"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
