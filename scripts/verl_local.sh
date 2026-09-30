#!/usr/bin/env bash
# Run the single-host VERL walkthrough from one local Bash config.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
usage() {
  cat <<'EOF'
Usage: bash scripts/verl_local.sh --config FILE install|up|run|inspect|down|server|node

Use one trusted local Bash config for every command.
Use up, run, and down in one terminal; server and node remain available for manual runs.
Use inspect to read this config's run artifacts without typing RUN_ROOT again.
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
if [[ "$action" != install && "$action" != down && ! -x "$telemetry_python" ]]; then
  echo "Telemetry Python not found: $telemetry_python (run scripts/setup.sh)" >&2
  exit 2
fi

stack_dir="$telemetry_home/state/verl-local"
process_start_time() {
  local stat
  local -a fields
  IFS= read -r stat < "/proc/$1/stat" || return 1
  read -r -a fields <<< "${stat##*) }"
  [[ "${fields[0]:-}" != Z && "${fields[19]:-}" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "${fields[19]}"
}

role_running() {
  local pid started current
  [[ -f "$stack_dir/$1.pid" ]] || return 1
  read -r pid started < "$stack_dir/$1.pid" || return 1
  [[ "$pid" =~ ^[0-9]+$ && "$started" =~ ^[0-9]+$ ]] || return 1
  current="$(process_start_time "$pid")" || return 1
  [[ "$current" == "$started" ]]
}

stop_role() {
  local role="$1" pid started attempt
  [[ -f "$stack_dir/$role.pid" ]] || return 0
  if role_running "$role"; then
    read -r pid started < "$stack_dir/$role.pid"
    kill -TERM "$pid" 2>/dev/null || true
    for ((attempt = 0; attempt < 100; attempt++)); do
      role_running "$role" || break
      sleep 0.1
    done
    if role_running "$role"; then
      echo "Could not stop $role (PID $pid); see $stack_dir/$role.log" >&2
      return 1
    fi
  fi
  rm -f "$stack_dir/$role.pid"
}

start_role() {
  local role="$1" pid started
  nohup bash "$repo_root/scripts/verl_local.sh" --config "$config_file" "$role" \
    > "$stack_dir/$role.log" 2>&1 < /dev/null &
  pid=$!
  if ! started="$(process_start_time "$pid")"; then
    echo "$role exited before its process could be recorded; see $stack_dir/$role.log" >&2
    return 1
  fi
  if ! printf '%s %s\n' "$pid" "$started" > "$stack_dir/$role.pid"; then
    kill -TERM "$pid" 2>/dev/null || true
    return 1
  fi
}

case "$action" in
  install)
    TOOLS_DIR="$tools_dir" bash "$repo_root/scripts/install_telemetry_tools.sh" node
    TOOLS_DIR="$tools_dir" bash "$repo_root/scripts/install_telemetry_tools.sh" server
    ;;
  up)
    if ! command -v curl >/dev/null 2>&1; then
      echo 'curl is required to check node collector readiness' >&2
      exit 2
    fi
    mkdir -p "$stack_dir"
    if role_running server || role_running node; then
      echo "Monitoring is already running; use down before up" >&2
      exit 1
    fi
    rm -f "$stack_dir/server.pid" "$stack_dir/node.pid"
    up_complete=0
    cleanup_up() {
      if [[ "$up_complete" == 0 ]]; then
        stop_role node || true
        stop_role server || true
      fi
    }
    trap cleanup_up EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    start_role server
    start_role node
    probe_addr="$node_addr"
    [[ "$probe_addr" != 0.0.0.0 ]] || probe_addr=127.0.0.1
    ready=0
    for ((attempt = 0; attempt < 90; attempt++)); do
      if ! role_running server || ! role_running node; then break; fi
      if grep -Fq 'Monitoring server ready:' "$stack_dir/server.log" && \
         curl --noproxy '*' -fsS --max-time 1 "http://$probe_addr:19100/metrics" >/dev/null 2>&1; then
        ready=1
        break
      fi
      sleep 1
    done
    if [[ "$ready" == 1 ]]; then
      sleep 1
      if ! role_running server || ! role_running node; then ready=0; fi
    fi
    if [[ "$ready" != 1 ]]; then
      echo "Monitoring did not become ready; recent logs:" >&2
      tail -n 20 "$stack_dir/server.log" "$stack_dir/node.log" >&2 || true
      exit 1
    fi
    up_complete=1
    trap - EXIT INT TERM
    echo "Monitoring ready: Grafana=http://127.0.0.1:13000"
    echo "Logs: $stack_dir/server.log and $stack_dir/node.log"
    ;;
  down)
    status=0
    stop_role node || status=1
    stop_role server || status=1
    if [[ "$status" == 0 ]]; then echo 'Monitoring stopped'; fi
    exit "$status"
    ;;
  server)
    exec env TOOLS_DIR="$tools_dir" \
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
      "ENABLE_GPU_METRICS=${ENABLE_GPU_METRICS:-1}"
      "TELEMETRY_METRICS_DIR=$run_root/telemetry-metrics"
      "PYTHON=$telemetry_python"
      "LOKI_PUSH_URL=$loki_push_url"
      "TELEMETRY_LOG_ROOTS=$telemetry_log_roots"
    )
    exec env "${node_args[@]}" bash "$repo_root/scripts/run_telemetry.sh" node
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
        --execution-mode "${EXECUTION_MODE:-auto}" \
        "${extra_args[@]}" -- "${VERL_COMMAND[@]}"
    ;;
  inspect)
    printf 'Configured run: %s (node=%s cluster=%s)\n' "$RUN_ID" "$node_name" "$cluster_name"
    printf 'Collector snapshots: %s/telemetry-metrics\n' "$run_root"
    printf 'Native source registration: %s\n' "${TELEMETRY_SOURCES_FILE:-not configured}"
    printf 'Diagnosis config: %s\n' "${DIAGNOSTICS_CONFIG:-not configured}"
    printf 'Loki logs/steps: ENABLE_LOGS=%s\n' "$enable_logs"
    echo 'Configuration is not proof of metric collection; inspect saved values below and live targets in Prometheus.'
    if [[ ! -d "$run_root" ]]; then
      echo "No run artifacts yet: $run_root"
      exit 0
    fi
    PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" \
      "$telemetry_python" -m xlayer_telemetry.show_run "$run_root"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
