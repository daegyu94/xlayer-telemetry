#!/usr/bin/env bash
# Real veRL Agent RL steps with verl-lab's SWE-Bench Docker grader and XLayer spans.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
verl_lab_root=${VERL_LAB_ROOT:?Set VERL_LAB_ROOT to the existing verl-lab checkout.}
model_path=${MODEL_PATH:?Set MODEL_PATH to a local model snapshot.}
export MODEL_PATH="$model_path"
run_root=${RUN_ROOT:?Set RUN_ROOT to a new writable output directory.}
trainer_mode=${TRAINER_MODE:-sync}
steps=${TOTAL_TRAINING_STEPS:-2}
case "$trainer_mode" in
  sync) execution_mode=sync ;;
  colocate_async|separate_async) execution_mode=async ;;
  *) echo "TRAINER_MODE must be sync, colocate_async, or separate_async" >&2; exit 2 ;;
esac
[[ "$steps" =~ ^[1-9][0-9]*$ ]] || { echo "TOTAL_TRAINING_STEPS must be a positive integer" >&2; exit 2; }
mkdir -p "$run_root/logs"
run_root=$(cd "$run_root" && pwd -P)
[[ ! -e "$run_root/telemetry" && ! -e "$run_root/run" ]] || { echo "Run already exists: $run_root" >&2; exit 2; }

verl_python="$verl_lab_root/third_party/verl/.venv/bin/python"
[[ -x "$verl_python" ]] || { echo "Missing veRL Python: $verl_python" >&2; exit 2; }
[[ -f "$verl_lab_root/artifacts/verl-eval/swebench-agent-train.parquet" ]] || {
  echo "Prepare the verl-lab SWE-Bench dataset first" >&2; exit 2;
}
[[ -f "$verl_lab_root/results/swebench-agent-rl/base/network/check.py" ]] || {
  echo "Prepare the verl-lab SWE-Bench base checkout first" >&2; exit 2;
}

"$verl_python" "$repo_root/examples/sandbox/prepare_verl_lab_smoke.py" \
  --input "$verl_lab_root/artifacts/verl-eval/swebench-agent-train.parquet" \
  --output "$run_root/train.parquet" --repeat "$steps"

export VERL_ROOT="$verl_lab_root/third_party/verl"
export TRAIN_FILE="$run_root/train.parquet"
export VAL_FILE="$TRAIN_FILE"
export CUSTOM_REWARD_FUNCTION_PATH="$repo_root/examples/sandbox/verl_lab_swebench_tools.py"
export FUNCTION_TOOL_PATH="$CUSTOM_REWARD_FUNCTION_PATH"
export VERL_LAB_TOOLS_PATH="$verl_lab_root/scripts/swebench_agent_tools.py"
export XLAYER_SWE_EDIT_ONLY=1
export SWE_AGENT_TRACE_DIR="$run_root/tools"
export MULTI_TURN_ENABLE=True MAX_ASSISTANT_TURNS=3 MAX_TOOL_RESPONSE_LENGTH=1000
if [[ "$trainer_mode" == separate_async ]]; then
  export CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-0,1}
  [[ "$CUDA_VISIBLE_DEVICES" == *,* ]] || {
    echo "separate_async requires two visible GPUs; set CUDA_VISIBLE_DEVICES" >&2; exit 2;
  }
else
  export CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-0}
fi
export TRAINER_GPUS=1 FSDP_SIZE=1 MODEL_DTYPE=bf16
export TOTAL_TRAINING_STEPS="$steps" TRAIN_BATCH_SIZE=2 GEN_BATCH_SIZE=1 ROLLOUT_N=${ROLLOUT_N:-4}
export MAX_PROMPT_LENGTH=768 MAX_RESPONSE_LENGTH=640 MAX_MODEL_LEN=2048
export MAX_TOKEN_LEN_PER_GPU=3072 AGENT_NUM_WORKERS=2 DATA_SHUFFLE=False
export TRAINER_SAVE_FREQ=-1 ROLLOUT_DATA_DIR="$run_root/rollouts"
export DATALOADER_NUM_WORKERS=${DATALOADER_NUM_WORKERS:-0}
export EXPERIMENT_NAME=${EXPERIMENT_NAME:-xlayer-sandbox-smoke}
export NCCL_IB_DISABLE=1 NCCL_NET_PLUGIN=none NCCL_CUMEM_ENABLE=0
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
export TELEMETRY_PYTHON=${TELEMETRY_PYTHON:-"$repo_root/.venv/bin/python"}
[[ -x "$TELEMETRY_PYTHON" ]] || { echo "Missing telemetry Python: $TELEMETRY_PYTHON" >&2; exit 2; }
export SWE_AGENT_GRADER_IMAGE=${SWE_AGENT_GRADER_IMAGE:-python:3.12-alpine}
docker image inspect "$SWE_AGENT_GRADER_IMAGE" >/dev/null 2>&1 || {
  echo "Missing local grader image: $SWE_AGENT_GRADER_IMAGE" >&2; exit 2;
}

diagnostics_args=()
if [[ -n "${DIAGNOSTICS_CONFIG:-}" ]]; then
  diagnostics_args+=(--diagnostics-config "$DIAGNOSTICS_CONFIG")
fi

bash "$repo_root/scripts/run_verl_with_telemetry.sh" \
  --output "$run_root/telemetry" --run-id "${RUN_ID:-xlayer-sandbox-$(date -u +%Y%m%dT%H%M%SZ)}" \
  --node "${TELEMETRY_NODE:-$(hostname)}" --execution-mode "$execution_mode" \
  "${diagnostics_args[@]}" -- \
  bash "$verl_lab_root/scripts/run_verl_v1_benchmark.sh" "$trainer_mode" baseline "$run_root/logs/training.log"
