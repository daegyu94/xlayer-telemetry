"""Resolve declarative TOML or trusted Bash configuration into one runtime model."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
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
    "TELEMETRY_TARGETS", "LOKI_LISTEN_ADDR",
    "PROMETHEUS_PORT", "GRAFANA_PORT", "LOKI_PORT",
)

PATH_KEYS = {"TELEMETRY_HOME", "TOOLS_DIR", "RUN_ROOT", "SERVER_OUTPUT_DIR", "NODE_OUTPUT_DIR",
             "TELEMETRY_RUNS_ROOT", "TELEMETRY_METRICS_DIR", "TELEMETRY_SOURCES_FILE", "DIAGNOSTICS_CONFIG",
             "TELEMETRY_PYTHON"}
BOOL_KEYS = {"ENABLE_LOGS", "ENABLE_GPU_METRICS", "ENABLE_ALERTS"}
AGE_KEYS = {"TELEMETRY_METRICS_MAX_AGE_SECONDS", "TELEMETRY_HEALTH_MAX_AGE_SECONDS"}
PORT_KEYS = {"PROMETHEUS_PORT", "GRAFANA_PORT", "LOKI_PORT"}


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
    selected = explicit or os.environ.get("XLAYER_CONFIG")
    if selected:
        return Path(selected).expanduser().absolute()
    directory = Path.home() / ".config/xlayer"
    # Keep existing installations on their config until the user migrates.
    legacy = directory / "config.conf"
    return legacy if legacy.exists() else directory / "config.toml"


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
        "PROMETHEUS_PORT": "19090", "GRAFANA_PORT": "13000", "LOKI_PORT": "13100",
    }


def _read_bash(path: Path) -> tuple[dict[str, str], list[str], dict[str, str]]:
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
    return {key: value for key, value in raw.items() if value != "__XLTEL_UNSET__"}, command, exports


def _read_toml(path: Path) -> tuple[dict[str, str], list[str], dict[str, str]]:
    try:
        import tomllib
    except ImportError:  # Python 3.10; installed through the conditional dependency.
        import tomli as tomllib
    with path.open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ConfigError("TOML config exceeds the 1 MiB limit.")
    try:
        document = tomllib.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ConfigError("Invalid TOML config; check syntax and duplicate keys.") from exc
    if set(document) - {"telemetry", "workload", "environment"}:
        raise ConfigError("TOML supports only [telemetry], [workload], and [environment] tables.")
    for section in ("telemetry", "workload", "environment"):
        if not isinstance(document.get(section, {}), dict):
            raise ConfigError(f"[{section}] must be a table.")
    raw = {}
    for key, value in document.get("telemetry", {}).items():
        if key not in KEYS:
            raise ConfigError("Unknown [telemetry] key; use the setting names in xltel config show.")
        if key in BOOL_KEYS and type(value) is bool:
            raw[key] = "1" if value else "0"
        elif key in AGE_KEYS and type(value) in {int, float}:
            raw[key] = format(Decimal(str(value)), "f")
        elif key in PORT_KEYS and type(value) is int:
            raw[key] = str(value)
        elif isinstance(value, str):
            raw[key] = str(Path(value).expanduser()) if key in PATH_KEYS and value else value
        else:
            raise ConfigError(f"{key} must be a string" + (", boolean or 0/1 string." if key in BOOL_KEYS else
                             " or positive number." if key in AGE_KEYS else " or integer." if key in PORT_KEYS else "."))
    workload = document.get("workload", {})
    if set(workload) - {"command"}:
        raise ConfigError("[workload] supports only command = [\"executable\", \"argument\", ...].")
    command = workload.get("command", [])
    if not isinstance(command, list) or any(not isinstance(arg, str) or "\0" in arg for arg in command):
        raise ConfigError("workload.command must be an array of strings without NUL bytes.")
    exports = document.get("environment", {})
    for key, value in exports.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in KEYS or not isinstance(value, str) or "\0" in value:
            raise ConfigError("[environment] requires variable names outside [telemetry] and string values without NUL bytes.")
    if any("\0" in value for value in raw.values()):
        raise ConfigError("TOML settings cannot contain NUL bytes.")
    return raw, command, exports


def load_config(path: Path) -> tuple[dict[str, str], list[str]]:
    if not path.is_file():
        raise ConfigError(f"Config not found: {path}. Run xltel init or select --config FILE.")
    raw, command, exports = _read_toml(path) if path.suffix.lower() == ".toml" else _read_bash(path)
    config = defaults()
    config.update(raw)
    config.update({key: os.environ[key] for key in KEYS if key in os.environ})
    # Keep trusted config exports (e.g. CUDA_VISIBLE_DEVICES, backend credentials)
    # in the child environment, never in displayed config or disk snapshots.
    config.update({key: value for key, value in exports.items()
                   if key not in KEYS and key not in os.environ and key not in {"_", "SHLVL", "PWD", "OLDPWD"}})
    home = Path(config["TELEMETRY_HOME"])
    if "TOOLS_DIR" not in raw and "TOOLS_DIR" not in os.environ:
        config["TOOLS_DIR"] = str(home / "tools")
    # Preserve the legacy Bash launcher's default for an explicitly named run.
    run_parent = config.get("TELEMETRY_RUNS_ROOT") or str(home / "runs" if config["RUN_ID"] == "auto" else Path.home() / "telemetry-runs")
    config.setdefault("RUN_ROOT", str(Path(run_parent) / config["RUN_ID"]))
    config.setdefault("TELEMETRY_RUNS_ROOT", str(Path(config["RUN_ROOT"]).parent))
    config.setdefault("SERVER_OUTPUT_DIR", str(home / "state/server"))
    config.setdefault("NODE_OUTPUT_DIR", str(home / "state/node"))
    for service in ("PROMETHEUS", "GRAFANA", "LOKI"):
        url_key = service + "_URL"
        if url_key not in raw and url_key not in os.environ:
            config[url_key] = "http://127.0.0.1:" + config[service + "_PORT"]
    validate(config)
    return config, command


def validate(config: dict[str, str]) -> None:
    for key in PORT_KEYS:
        if config.get(key) and (not config[key].isdigit() or not 1 <= int(config[key]) <= 65535):
            raise ConfigError(f"{key} must be an integer port from 1 to 65535.")
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
    seen = set()
    for entry in config.get("TELEMETRY_TARGETS", "").split(",") if config.get("TELEMETRY_TARGETS") else []:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}=[A-Za-z0-9_.-]+", entry):
            raise ConfigError("TELEMETRY_TARGETS must be comma-separated unique node=IPv4-or-hostname entries (port 19100).")
        node = entry.split("=", 1)[0]
        if node in seen:
            raise ConfigError("TELEMETRY_TARGETS node names must be unique.")
        seen.add(node)
    if config.get("LOKI_LISTEN_ADDR") and not re.fullmatch(r"[A-Za-z0-9_.-]+", config["LOKI_LISTEN_ADDR"]):
        raise ConfigError("LOKI_LISTEN_ADDR must be an IPv4 address or hostname without a port.")


def migrate(path: Path, output: Path) -> None:
    """Write a private, resolved TOML snapshot without copying arbitrary exports."""
    config, command = load_config(path)
    text = "# Resolved settings; exported environment variables are not copied.\n[telemetry]\n"
    text += "".join(f"{key} = {json.dumps(value, ensure_ascii=False)}\n" for key, value in sorted(config.items()) if key in KEYS)
    text += "\n[workload]\ncommand = " + json.dumps(command, ensure_ascii=False) + "\n"
    if output.suffix.lower() != ".toml":
        raise ConfigError("Migration output must end in .toml.")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ConfigError("Migration output already exists; choose another --output path.") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)


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
            stream.write('''# Declarative config; environment values override [telemetry].
[telemetry]
TELEMETRY_HOME = "~/telemetry"
RUN_ID = "auto"
# ENABLE_LOGS = true
# ENABLE_GPU_METRICS = false  # CPU-only collector nodes.
# EXECUTION_MODE = "async"  # Trainer mode hidden inside a shell launcher.

[workload]
# command = ["/path/to/verl-env/bin/python", "-m", "verl.trainer.main_ppo"]
''' if path.suffix.lower() == ".toml" else '''# Trusted Bash config. Environment values override these settings in xltel.
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
