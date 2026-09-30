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
health_max_age_seconds="${TELEMETRY_HEALTH_MAX_AGE_SECONDS:-300}"
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
GPU/host collection and native vLLM/Ray scraping must be started separately.
--source records manifest metadata; it does not configure Prometheus scraping.
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

[[ "$health_max_age_seconds" =~ ^[0-9]+([.][0-9]+)?$ ]] && awk -v value="$health_max_age_seconds" 'BEGIN { exit !(value > 0) }' || { echo "TELEMETRY_HEALTH_MAX_AGE_SECONDS must be positive" >&2; exit 2; }
[[ -n "$output_dir" ]] || { echo "--output is required" >&2; exit 2; }
(($#)) || { echo "a VERL command is required after --" >&2; exit 2; }
[[ "$execution_mode" == auto || "$execution_mode" == sync || "$execution_mode" == async ]] || { echo "--execution-mode must be auto, sync, or async" >&2; exit 2; }
[[ "$diagnostics_interval" =~ ^[0-9]+([.][0-9]+)?$ ]] && awk -v value="$diagnostics_interval" 'BEGIN { exit !(value > 0) }' || { echo "--diagnostics-interval must be positive" >&2; exit 2; }
if [[ -n "$diagnostics_config" ]]; then
  [[ -f "$diagnostics_config" ]] || { echo "diagnostics config is not a file: $diagnostics_config" >&2; exit 2; }
  diagnostics_config="$(cd "$(dirname "$diagnostics_config")" && pwd -P)/$(basename "$diagnostics_config")"
  PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" \
    "$telemetry_python" -m xlayer_telemetry.analysis.diagnostics \
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
  # VERL's async rollout server also serves synchronous trainer steps.
  for argument in "${command[@]}"; do
    if [[ "$argument" == "verl.experimental.fully_async_policy.fully_async_main" || "$argument" == trainer.v1.trainer_mode=colocate_async || "$argument" == trainer.v1.trainer_mode=separate_async ]]; then
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
health_pid=""
stop_health() {
  if [[ -n "$health_pid" ]]; then
    kill "$health_pid" 2>/dev/null || true
    wait "$health_pid" 2>/dev/null || true
    health_pid=""
  fi
}
finish_health() {
  stop_health
  PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" "$telemetry_python" -m xlayer_telemetry.telemetry_health \
    --run-root "$output_dir" --finish "$1" --bridge-export "$2" --diagnostics-export "$3" \
    || echo "[telemetry] health record failed" >&2
}
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
  stop_health
  stop_bridge
  stop_diagnostics
}
workload_pid=""
interrupt_workload() {
  local signal="$1" status="$2"
  trap '' INT TERM
  if [[ -n "$workload_pid" ]]; then
    # The wrapper owns only this newly created session, never an existing Ray cluster.
    kill -s "$signal" -- "-$workload_pid" 2>/dev/null || true
    for ((attempt = 0; attempt < 50; attempt++)); do
      kill -0 -- "-$workload_pid" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 -- "-$workload_pid" 2>/dev/null; then
      kill -s KILL -- "-$workload_pid" 2>/dev/null || true
    fi
    wait "$workload_pid" 2>/dev/null || true
  fi
  diagnosis_outcome=disabled
  [[ -z "$diagnostics_config" ]] || diagnosis_outcome=interrupted
  finish_health "$status" interrupted "$diagnosis_outcome"
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
    "$telemetry_python" -m xlayer_telemetry.analysis.diagnostics \
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
printf '[telemetry] bridge=VERL completed steps; execution_mode=%s\n' "$execution_mode"
echo '[telemetry] GPU/host: node collector required; vLLM/Ray: monitoring server source registration required'
printf '[telemetry] executing:'
printf ' %q' "${command[@]}"
printf '\n'

set +e
health_args=(--run-root "$output_dir" --bridge-pid "$bridge_pid" --max-age-seconds "$health_max_age_seconds")
[[ -z "$diagnostics_pid" ]] || health_args+=(--diagnostics-pid "$diagnostics_pid")
PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" "$telemetry_python" -m xlayer_telemetry.telemetry_health \
  "${health_args[@]}" --once > "$output_dir/logs/telemetry-health.log" 2>&1
setsid -- "${command[@]}" &
workload_pid=$!
PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" "$telemetry_python" -m xlayer_telemetry.telemetry_health \
  "${health_args[@]}" > "$output_dir/logs/telemetry-health.log" 2>&1 &
health_pid=$!
wait "$workload_pid"
workload_status=$?
workload_pid=""
set -e

stop_sidecars
bridge_pid=""
diagnostics_pid=""
bridge_export=missing
diagnosis_export=disabled
if [[ -f "$VERL_FILE_LOGGER_PATH" ]]; then
  bridge_export=ok
  PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" \
    "$telemetry_python" -m xlayer_telemetry.adapters.verl \
      --input "$VERL_FILE_LOGGER_PATH" \
      --metrics-dir "$TELEMETRY_METRICS_DIR" \
      --run-id "$TELEMETRY_RUN_ID" \
      --worker-id driver \
      --node "$node_name" \
      --history "$step_history_path" \
      --execution-mode "$execution_mode" || { bridge_export=failed; echo "[telemetry] final metric export failed" >&2; }
  if ! compgen -G "$TELEMETRY_METRICS_DIR/verl-trainer-driver*.json" >/dev/null; then
    bridge_export=missing
    echo '[telemetry] no translated VERL snapshots; check logger keys with python -m xlayer_telemetry.adapters.verl --describe-metrics and logs/telemetry-bridge.log' >&2
  fi
else
  echo '[telemetry] VERL file logger output is missing; ensure trainer.logger includes file and the launcher forwards VERL_FILE_LOGGER_PATH' >&2
fi
if [[ -n "$diagnostics_config" ]]; then
  diagnosis_export=ok
  # 3FS ClickHouse distributions can arrive after the workload boundary.
  # Keep the final step pending until its service window has settled.
  settle_seconds=$("$telemetry_python" -c 'import json,sys; c=json.load(open(sys.argv[1])); print(c.get("threefs", {}).get("settle_seconds", 30) if c.get("threefs") else 0)' "$diagnostics_config")
  if awk -v value="$settle_seconds" 'BEGIN { exit !(value > 0) }'; then
    sleep "$settle_seconds"
  fi
  PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" \
    "$telemetry_python" -m xlayer_telemetry.analysis.diagnostics \
      --config "$diagnostics_config" --history "$step_history_path" \
      --output "$output_dir/diagnostics" --run-id "$run_id" \
      --node "$node_name" --execution-mode "$execution_mode" --once --pending-only --finalize-pending \
      >> "$output_dir/logs/telemetry-diagnostics.log" 2>&1 || { diagnosis_export=failed; echo "[telemetry] final diagnosis failed" >&2; }
  [[ -f "$output_dir/diagnostics/latest.json" ]] || diagnosis_export=missing
fi

finish_health "$workload_status" "$bridge_export" "$diagnosis_export"

PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" \
  "$telemetry_python" -m xlayer_telemetry.show_run "$output_dir" || echo "[telemetry] run summary failed" >&2
exit "$workload_status"
