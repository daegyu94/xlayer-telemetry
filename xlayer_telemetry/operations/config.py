"""Resolve trusted Bash configuration once, without evaluating workload argv."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import sysconfig
import tempfile
from urllib.parse import urlsplit


KEYS = (
    "TELEMETRY_HOME", "TOOLS_DIR", "RUN_ID", "RUN_ROOT", "NODE_NAME", "NODE_ADDR",
    "CLUSTER_NAME", "TELEMETRY_PYTHON", "ENABLE_GPU_METRICS", "ENABLE_LOGS",
    "ENABLE_ALERTS", "TELEMETRY_SOURCES_FILE", "DIAGNOSTICS_CONFIG", "EXECUTION_MODE",
    "PROMETHEUS_URL", "GRAFANA_URL", "LOKI_URL", "SERVER_OUTPUT_DIR", "NODE_OUTPUT_DIR",
    "TELEMETRY_METRICS_DIR", "TELEMETRY_RUNS_ROOT", "TELEMETRY_METRICS_MAX_AGE_SECONDS",
    "TELEMETRY_HEALTH_MAX_AGE_SECONDS", "TELEMETRY_LOG_ROOTS",
    "GF_FEATURE_TOGGLES_ENABLE", "GF_USERS_DEFAULT_THEME",
)


class ConfigError(ValueError):
    pass


def assets_root() -> Path:
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "scripts/verl_local.sh").is_file():
        return checkout
    installed = Path(sysconfig.get_path("data")) / "share/xlayer-telemetry"
    if not (installed / "scripts/verl_local.sh").is_file():
        raise ConfigError("Runtime assets are missing; reinstall xlayer-telemetry.")
    return installed


def config_path(explicit: str | None = None) -> Path:
    return Path(explicit or os.environ.get("XLAYER_CONFIG") or
                Path.home() / ".config/xlayer/config.conf").expanduser().absolute()


def defaults() -> dict[str, str]:
    home = Path.home() / "telemetry"
    return {
        "TELEMETRY_HOME": str(home), "TOOLS_DIR": str(home / "tools"), "RUN_ID": "auto",
        "NODE_NAME": "gpu-local", "NODE_ADDR": "127.0.0.1", "CLUSTER_NAME": "training-cluster",
        "TELEMETRY_PYTHON": sys.executable, "ENABLE_GPU_METRICS": "1", "ENABLE_LOGS": "0",
        "ENABLE_ALERTS": "0", "EXECUTION_MODE": "auto",
        "PROMETHEUS_URL": "http://127.0.0.1:19090", "GRAFANA_URL": "http://127.0.0.1:13000",
        "LOKI_URL": "http://127.0.0.1:13100", "TELEMETRY_METRICS_MAX_AGE_SECONDS": "300",
        "TELEMETRY_HEALTH_MAX_AGE_SECONDS": "300", "GF_FEATURE_TOGGLES_ENABLE": "extraThemes",
        "GF_USERS_DEFAULT_THEME": "dark",
    }


def load_config(path: Path) -> tuple[dict[str, str], list[str]]:
    if not path.is_file():
        raise ConfigError(f"Config not found: {path}. Run xltel init or select --config FILE.")
    # A separate fd keeps arbitrary config stdout out of JSON/CLI output.
    # Bash configs are executable trusted files, not a security sandbox.
    script = '''set -euo pipefail
source "$1" >&2
env -0 > "$2.env"
exec 3>"$2"
shift 2
for key in "$@"; do printf '%s\\0%s\\0' "$key" "${!key-__XLTEL_UNSET__}" >&3; done
if declare -p VERL_COMMAND 2>/dev/null | grep -q '^declare -a '; then
  if (( ${#VERL_COMMAND[@]} )); then printf '%s\\0' "${VERL_COMMAND[@]}" >&3; fi
elif [[ -v VERL_COMMAND ]]; then
  echo 'VERL_COMMAND must be a Bash array' >&2; exit 2
fi
'''
    with tempfile.TemporaryDirectory(prefix="xltel-config-") as temporary:
        output = Path(temporary) / "values"
        try:
            child = subprocess.Popen(["bash", "-c", script, "xltel", str(path), str(output), *KEYS],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                child.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate()
                raise
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConfigError("Cannot read Bash config; check syntax, bash installation, or blocking commands.") from exc
        if child.returncode:
            raise ConfigError("Invalid Bash config; check syntax with bash -n FILE and use VERL_COMMAND=(...).")
        values = output.read_bytes().decode().split("\0")
        exports = dict(entry.split("=", 1) for entry in Path(str(output) + ".env").read_bytes().decode().split("\0")
                       if "=" in entry)
    raw = dict(zip(values[:len(KEYS)*2:2], values[1:len(KEYS)*2:2]))
    command = values[len(KEYS)*2:-1]
    config = defaults()
    config.update({key: value for key, value in raw.items() if value != "__XLTEL_UNSET__"})
    config.update({key: os.environ[key] for key in KEYS if key in os.environ})
    # Keep trusted config exports (e.g. CUDA_VISIBLE_DEVICES, backend credentials)
    # in the child environment, never in displayed config or disk snapshots.
    config.update({key: value for key, value in exports.items()
                   if key not in KEYS and key not in os.environ and key not in {"_", "SHLVL", "PWD", "OLDPWD"}})
    home = Path(config["TELEMETRY_HOME"])
    if raw.get("TOOLS_DIR") == "__XLTEL_UNSET__" and "TOOLS_DIR" not in os.environ:
        config["TOOLS_DIR"] = str(home / "tools")
    # Preserve the legacy Bash launcher's default for an explicitly named run.
    run_parent = config.get("TELEMETRY_RUNS_ROOT") or str(home / "runs" if config["RUN_ID"] == "auto" else Path.home() / "telemetry-runs")
    config.setdefault("RUN_ROOT", str(Path(run_parent) / config["RUN_ID"]))
    config.setdefault("TELEMETRY_RUNS_ROOT", str(Path(config["RUN_ROOT"]).parent))
    config.setdefault("SERVER_OUTPUT_DIR", str(home / "state/server"))
    config.setdefault("NODE_OUTPUT_DIR", str(home / "state/node"))
    validate(config)
    return config, command


def validate(config: dict[str, str]) -> None:
    for key in ("RUN_ID", "NODE_NAME", "CLUSTER_NAME"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", config[key]):
            raise ConfigError(f"{key} must be 1-64 letters, digits, dots, underscores, or hyphens.")
    for key in ("TELEMETRY_HOME", "TOOLS_DIR", "RUN_ROOT", "SERVER_OUTPUT_DIR", "NODE_OUTPUT_DIR",
                "TELEMETRY_RUNS_ROOT", "TELEMETRY_METRICS_DIR", "TELEMETRY_SOURCES_FILE", "DIAGNOSTICS_CONFIG"):
        if config.get(key) and not Path(config[key]).is_absolute():
            raise ConfigError(f"{key} must be an absolute path.")
    for key in ("ENABLE_LOGS", "ENABLE_GPU_METRICS", "ENABLE_ALERTS"):
        if config[key] not in {"0", "1"}:
            raise ConfigError(f"{key} must be 0 or 1.")
    if config["EXECUTION_MODE"] not in {"auto", "sync", "async"}:
        raise ConfigError("EXECUTION_MODE must be auto, sync, or async.")
    for key in ("TELEMETRY_METRICS_MAX_AGE_SECONDS", "TELEMETRY_HEALTH_MAX_AGE_SECONDS"):
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", config[key]) or float(config[key]) <= 0:
            raise ConfigError(f"{key} must be positive.")
    for key in ("PROMETHEUS_URL", "GRAFANA_URL", "LOKI_URL"):
        url = urlsplit(config[key])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ConfigError(f"{key} must be an HTTP(S) URL without embedded credentials.")


def snapshot(config: dict[str, str]) -> Path:
    """Keep the resolved config available to background launcher children."""
    text = "".join(f"{key}={shlex.quote(value)}\n" for key, value in sorted(config.items()) if key in KEYS)
    directory = Path(config["TELEMETRY_HOME"]) / "state/cli-configs"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (hashlib.sha256(text.encode()).hexdigest()[:24] + ".conf")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(text)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def initialize(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with path.open("x", encoding="utf-8") as stream:
            path.chmod(0o600)
            stream.write('''# Trusted Bash config. Environment values override these settings in xltel.
TELEMETRY_HOME="$HOME/telemetry"
RUN_ID=auto
# Run xltel run -- <your existing VERL command>, or set VERL_COMMAND=(...).
# ENABLE_LOGS=1
# ENABLE_GPU_METRICS=0  # For CPU-only collector nodes.
# TELEMETRY_SOURCES_FILE="$TELEMETRY_HOME/config/native-sources.json"
# DIAGNOSTICS_CONFIG="$TELEMETRY_HOME/config/diagnostics.json"
# EXECUTION_MODE=async  # For trainer mode hidden inside a shell launcher.
''')
    except FileExistsError:
        return False
    config, _ = load_config(path)
    for directory in (Path(config["TELEMETRY_HOME"]) / "state", Path(config["TELEMETRY_RUNS_ROOT"])):
        directory.mkdir(parents=True, exist_ok=True)
    return True
