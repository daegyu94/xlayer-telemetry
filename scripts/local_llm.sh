#!/usr/bin/env bash
# Optional single-user inference service; never started by telemetry up.
set -euo pipefail
if [[ "${1:-}" == --config ]]; then
  source "${2:?Specify a trusted Bash config file}"
  shift 2
fi
action="${1:-help}"
telemetry_home="${TELEMETRY_HOME:-$HOME/telemetry}"
version="${OLLAMA_VERSION:-0.34.4}"
model="${LLM_MODEL:-qwen3.5:27b}"
port="${LLM_PORT:-11434}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && "$port" =~ ^[0-9]+$ ]] || exit 2
runtime="$telemetry_home/tools/ollama-v$version"
state="$telemetry_home/state/local-llm"
export OLLAMA_HOST="127.0.0.1:$port"
export OLLAMA_MODELS="$telemetry_home/models/ollama"
identity() {
  local stat
  local -a fields
  [[ -r /proc/$1/stat ]] || return 1
  IFS= read -r stat < "/proc/$1/stat" || return 1
  read -r -a fields <<< "${stat##*) }"
  [[ "${fields[0]}" != Z ]] || return 1
  printf '%s\n' "${fields[19]}"
}
running() {
  local current
  [[ -f "$state/server.pid" ]] || return 1
  read -r pid started boot < "$state/server.pid" || return 1
  [[ "$pid" =~ ^[0-9]+$ && "$started" =~ ^[0-9]+$ ]] || return 1
  [[ "$boot" == "$(cat /proc/sys/kernel/random/boot_id)" ]] || return 1
  current="$(identity "$pid")" || return 1
  [[ "$started" == "$current" ]]
}
stop() {
  if running; then
    kill -TERM "$pid"
    for ((i=0; i<150; i++)); do
      running || break
      sleep 0.1
    done
    if running; then echo "Still stopping; inspect $state/server.log" >&2; return 1; fi
  fi
  rm -f "$state/server.pid"
}
case "$action" in
  install)
    case "$(uname -s)/$(uname -m)" in
      Linux/x86_64) arch=amd64 ;;
      Linux/aarch64) arch=arm64 ;;
      *) echo 'This helper requires Linux x86_64 or ARM64' >&2; exit 2 ;;
    esac
    if [[ -x "$runtime/bin/ollama" ]]; then echo "Already installed: $runtime"; exit 0; fi
    cache="$telemetry_home/tools/.downloads/ollama-$version"
    mkdir -p "$cache"
    asset="ollama-linux-$arch.tar.zst"
    base="https://github.com/ollama/ollama/releases/download/v$version"
    curl -fsSL --retry 2 "$base/sha256sum.txt" -o "$cache/sha256sum.txt"
    curl -fsSL --retry 2 "$base/$asset" -o "$cache/$asset"
    (cd "$cache"; awk -v a="./$asset" '$2 == a {print}' sha256sum.txt | sha256sum -c -)
    staging="$(mktemp -d "$telemetry_home/tools/.ollama-install.XXXXXX")"
    trap 'rm -rf "$staging"' EXIT
    tar --zstd -xf "$cache/$asset" -C "$staging"
    mv "$staging" "$runtime"
    trap - EXIT
    echo "Installed: $runtime"
    ;;
  up)
    : "${LLM_GPU:?Set LLM_GPU to a GPU UUID or index reserved for diagnosis}"
    [[ -x "$runtime/bin/ollama" ]] || { echo 'Run local_llm.sh install first' >&2; exit 2; }
    mkdir -p "$state" "$OLLAMA_MODELS"
    if running; then echo 'Local LLM is already running'; exit 0; fi
    if curl -fsS --max-time 2 "http://$OLLAMA_HOST/api/version" >/dev/null 2>&1; then
      echo "Another Ollama service uses $OLLAMA_HOST; set LLM_PORT or use that endpoint" >&2
      exit 1
    fi
    CUDA_VISIBLE_DEVICES="$LLM_GPU" OLLAMA_VULKAN=false OLLAMA_NO_CLOUD=true \
      OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 \
      OLLAMA_CONTEXT_LENGTH=32768 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KEEP_ALIVE=5m \
      nohup setsid "$runtime/bin/ollama" serve > "$state/server.log" 2>&1 < /dev/null &
    pid=$!
    started="$(identity "$pid")"
    printf '%s %s %s\n' "$pid" "$started" "$(cat /proc/sys/kernel/random/boot_id)" > "$state/server.pid"
    trap 'stop || true' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    for ((i=0; i<30; i++)); do
      running || break
      if curl -fsS --max-time 1 "http://$OLLAMA_HOST/api/version" >/dev/null 2>&1; then
        trap - EXIT INT TERM
        echo "Local LLM ready: http://$OLLAMA_HOST (GPU $LLM_GPU)"
        exit 0
      fi
      sleep 1
    done
    tail -n 20 "$state/server.log" >&2
    exit 1
    ;;
  pull) "$runtime/bin/ollama" pull "$model" ;;
  status) "$runtime/bin/ollama" ps ;;
  down) stop; echo 'Local LLM stopped' ;;
  *) echo 'Usage: bash scripts/local_llm.sh [--config FILE] install|up|pull|status|down'; exit 2 ;;
esac
