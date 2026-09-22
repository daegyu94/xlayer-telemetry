#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
telemetry_python="${TELEMETRY_PYTHON:-python3}"
output_dir=""
run_id=""
node_name="${TELEMETRY_NODE:-$(hostname)}"
rl_insight_url="${RL_INSIGHT_SERVER_URL:-}"
diagnostics_config=""
diagnostics_interval="10"
execution_mode="auto"
declare -a sources=()
declare -a settings=()

usage() {
  cat <<'EOF'
Usage:
  bash scripts/run_verl_with_telemetry.sh \
    --output DIR [--run-id ID] [--node NAME] \
    [--rl-insight-url URL] [--source NAME=ENDPOINT] [--set NAME=VALUE] \
    [--diagnostics-config FILE] [--diagnostics-interval SECONDS] \
    [--execution-mode auto|sync|async] \
    -- VERL_COMMAND [ARGS...]

The wrapper adds VERL's file logger (and rl_insight when configured), starts the
step-metric bridge, writes telemetry-manifest.json, and preserves the workload
exit code. It does not modify VERL source code.
EOF
}

while (($#)); do
  case "$1" in
    --output)
      [[ $# -ge 2 ]] || { echo "--output requires a value" >&2; exit 2; }
      output_dir="$2"
      shift 2
      ;;
    --run-id)
      [[ $# -ge 2 ]] || { echo "--run-id requires a value" >&2; exit 2; }
      run_id="$2"
      shift 2
      ;;
    --node)
      [[ $# -ge 2 ]] || { echo "--node requires a value" >&2; exit 2; }
      node_name="$2"
      shift 2
      ;;
    --rl-insight-url)
      [[ $# -ge 2 ]] || { echo "--rl-insight-url requires a value" >&2; exit 2; }
      rl_insight_url="$2"
      shift 2
      ;;
    --diagnostics-config)
      [[ $# -ge 2 ]] || { echo "--diagnostics-config requires a value" >&2; exit 2; }
      diagnostics_config="$2"
      shift 2
      ;;
    --diagnostics-interval)
      [[ $# -ge 2 ]] || { echo "--diagnostics-interval requires a value" >&2; exit 2; }
      diagnostics_interval="$2"
      shift 2
      ;;
    --execution-mode)
      [[ $# -ge 2 ]] || { echo "--execution-mode requires a value" >&2; exit 2; }
      execution_mode="$2"
      shift 2
      ;;
    --source)
      [[ $# -ge 2 ]] || { echo "--source requires NAME=ENDPOINT" >&2; exit 2; }
      sources+=("$2")
      shift 2
      ;;
    --set)
      [[ $# -ge 2 ]] || { echo "--set requires NAME=VALUE" >&2; exit 2; }
      settings+=("$2")
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ -n "$output_dir" ]] || { echo "--output is required" >&2; exit 2; }
(($#)) || { echo "a VERL command is required after --" >&2; exit 2; }
[[ "$execution_mode" == auto || "$execution_mode" == sync || "$execution_mode" == async ]] || { echo "--execution-mode must be auto, sync, or async" >&2; exit 2; }
[[ "$diagnostics_interval" =~ ^[0-9]+([.][0-9]+)?$ ]] && awk -v value="$diagnostics_interval" 'BEGIN { exit !(value > 0) }' || { echo "--diagnostics-interval must be positive" >&2; exit 2; }
if [[ -n "$diagnostics_config" ]]; then
  [[ -f "$diagnostics_config" ]] || { echo "diagnostics config is not a file: $diagnostics_config" >&2; exit 2; }
  diagnostics_config="$(cd "$(dirname "$diagnostics_config")" && pwd -P)/$(basename "$diagnostics_config")"
  PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" \
    "$telemetry_python" -m xlayer_telemetry.diagnostics \
      --config "$diagnostics_config" --check-config
fi
if [[ -z "$run_id" ]]; then
  run_id="verl-$(date -u +%Y%m%dT%H%M%SZ)"
fi

mkdir -p "$output_dir/logs" "$output_dir/telemetry-metrics" "$output_dir/telemetry-events"
output_dir="$(cd "$output_dir" && pwd -P)"
if [[ -e "$output_dir/telemetry-manifest.json" || -e "$output_dir/logs/verl-metrics.jsonl" ]]; then
  echo "output already contains a run; choose a new --output directory" >&2
  exit 2
fi
export TELEMETRY_RUN_ID="$run_id"
export TELEMETRY_NODE="$node_name"
export TELEMETRY_METRICS_DIR="$output_dir/telemetry-metrics"
export TELEMETRY_EVENTS_DIR="$output_dir/telemetry-events"
export VERL_FILE_LOGGER_PATH="$output_dir/logs/verl-metrics.jsonl"
step_history_path="$output_dir/telemetry-events/verl-steps.jsonl"
if [[ -n "$rl_insight_url" ]]; then
  export RL_INSIGHT_SERVER_URL="$rl_insight_url"
fi

command=("$@")
if [[ "$execution_mode" == auto ]]; then
  execution_mode=sync
  for argument in "${command[@]}"; do
    if [[ "$argument" == "verl.experimental.fully_async_policy.fully_async_main" || "$argument" == actor_rollout_ref.rollout.mode=async ]]; then
      execution_mode=async
    fi
  done
fi
is_main_ppo=0
for argument in "${command[@]}"; do
  if [[ "$argument" == "verl.trainer.main_ppo" || "$argument" == "verl.experimental.fully_async_policy.fully_async_main" ]]; then
    is_main_ppo=1
  fi
done

has_logger=0
has_project=0
has_experiment=0
for argument in "${command[@]}"; do
  [[ "$argument" == trainer.logger=* ]] && has_logger=1
  [[ "$argument" == trainer.project_name=* ]] && has_project=1
  [[ "$argument" == trainer.experiment_name=* ]] && has_experiment=1
done

if ((is_main_ppo)); then
  if ((has_logger)); then
    logger_value=""
    for argument in "${command[@]}"; do
      if [[ "$argument" == trainer.logger=* ]]; then
        logger_value="${argument#trainer.logger=}"
      fi
    done
    file_logger_pattern='(^|\[|,)[[:space:]]*"?file"?[[:space:]]*(\]|,|$)'
    if [[ ! "$logger_value" =~ $file_logger_pattern ]]; then
      echo "trainer.logger override must include file for the telemetry bridge" >&2
      exit 2
    fi
  else
    if [[ -n "$rl_insight_url" ]]; then
      command+=('trainer.logger=["console","file","rl_insight"]')
    else
      command+=('trainer.logger=["console","file"]')
    fi
  fi
  ((has_project)) || command+=('trainer.project_name=agent-rl')
  ((has_experiment)) || command+=("trainer.experiment_name=$run_id")
else
  echo "[telemetry] command is not verl.trainer.main_ppo; logger overrides were not added" >&2
fi

manifest_args=(
  --output "$output_dir/telemetry-manifest.json"
  --run-id "$run_id"
  --role "trainer=$node_name"
  --role "rollout=$node_name"
  --artifact "logs=$output_dir/logs"
)
if [[ -n "$diagnostics_config" ]]; then
  manifest_args+=(--artifact "diagnostics=$output_dir/diagnostics")
fi
has_execution_mode_setting=0
for setting in "${settings[@]}"; do
  [[ "$setting" == execution_mode=* ]] && has_execution_mode_setting=1
done
((has_execution_mode_setting)) || manifest_args+=(--set "execution_mode=$execution_mode")
for source in "${sources[@]}"; do
  manifest_args+=(--source "$source")
done
for setting in "${settings[@]}"; do
  manifest_args+=(--set "$setting")
done

PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" \
  "$telemetry_python" -m xlayer_telemetry.manifest "${manifest_args[@]}"

bridge_pid=""
diagnostics_pid=""
stop_bridge() {
  if [[ -n "$bridge_pid" ]] && kill -0 "$bridge_pid" 2>/dev/null; then
    kill "$bridge_pid" 2>/dev/null || true
    wait "$bridge_pid" 2>/dev/null || true
  fi
}
stop_diagnostics() {
  if [[ -n "$diagnostics_pid" ]] && kill -0 "$diagnostics_pid" 2>/dev/null; then
    kill "$diagnostics_pid" 2>/dev/null || true
    wait "$diagnostics_pid" 2>/dev/null || true
  fi
}
stop_sidecars() {
  stop_bridge
  stop_diagnostics
}
workload_pid=""
interrupt_workload() {
  local signal="$1" status="$2"
  trap '' INT TERM
  if [[ -n "$workload_pid" ]]; then
    kill -s "$signal" "$workload_pid" 2>/dev/null || true
    wait "$workload_pid" 2>/dev/null || true
  fi
  exit "$status"
}
trap stop_sidecars EXIT
# Background commands may inherit SIGINT ignored; SIGTERM requests cleanup.
trap 'interrupt_workload TERM 130' INT
trap 'interrupt_workload TERM 143' TERM

PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" \
  "$telemetry_python" -m xlayer_telemetry.adapters.verl \
    --input "$VERL_FILE_LOGGER_PATH" \
    --metrics-dir "$TELEMETRY_METRICS_DIR" \
    --run-id "$TELEMETRY_RUN_ID" \
    --worker-id driver \
    --node "$node_name" \
    --history "$step_history_path" \
    --execution-mode "$execution_mode" \
    --poll-interval 0.2 \
    --follow \
    > "$output_dir/logs/telemetry-bridge.log" 2>&1 &
bridge_pid=$!
if [[ -n "$diagnostics_config" ]]; then
  PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" \
    "$telemetry_python" -m xlayer_telemetry.diagnostics \
      --config "$diagnostics_config" \
      --history "$step_history_path" \
      --output "$output_dir/diagnostics" \
      --run-id "$run_id" --node "$node_name" \
      --execution-mode "$execution_mode" \
      --interval "$diagnostics_interval" \
      > "$output_dir/logs/telemetry-diagnostics.log" 2>&1 &
  diagnostics_pid=$!
fi

printf '[telemetry] run_id=%s output=%s\n' "$run_id" "$output_dir"
printf '[telemetry] executing:'
printf ' %q' "${command[@]}"
printf '\n'

set +e
"${command[@]}" &
workload_pid=$!
wait "$workload_pid"
workload_status=$?
workload_pid=""
set -e

stop_sidecars
bridge_pid=""
diagnostics_pid=""
if [[ -f "$VERL_FILE_LOGGER_PATH" ]]; then
  PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" \
    "$telemetry_python" -m xlayer_telemetry.adapters.verl \
      --input "$VERL_FILE_LOGGER_PATH" \
      --metrics-dir "$TELEMETRY_METRICS_DIR" \
      --run-id "$TELEMETRY_RUN_ID" \
      --worker-id driver \
      --node "$node_name" \
      --history "$step_history_path" \
      --execution-mode "$execution_mode" || echo "[telemetry] final metric export failed" >&2
fi
if [[ -n "$diagnostics_config" ]]; then
  PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" \
    "$telemetry_python" -m xlayer_telemetry.diagnostics \
      --config "$diagnostics_config" --history "$step_history_path" \
      --output "$output_dir/diagnostics" --run-id "$run_id" \
      --node "$node_name" --execution-mode "$execution_mode" --once --pending-only \
      >> "$output_dir/logs/telemetry-diagnostics.log" 2>&1 || echo "[telemetry] final diagnosis failed" >&2
fi

PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" \
  "$telemetry_python" -m xlayer_telemetry.show_run "$output_dir" || echo "[telemetry] run summary failed" >&2
exit "$workload_status"
