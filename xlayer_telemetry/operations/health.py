"""Bounded health checks; process identity and data freshness are separate."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time
from urllib.request import urlopen

from .config import assets_root
from ..source_discovery import build_file_discovery
from ..subsystems import inspect_sources


def process_identity(path: Path) -> dict:
    try:
        fields = path.read_text().split()
        if len(fields) not in {2, 3}:
            raise ValueError
        pid, started = map(int, fields[:2])
        if pid <= 1:
            raise ValueError
        if len(fields) == 3 and fields[2] != Path("/proc/sys/kernel/random/boot_id").read_text().strip():
            raise ValueError
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        if stat[0] == "Z" or int(stat[19]) != started:
            raise ValueError
        return {"process": "running", "pid": pid}
    except (OSError, ValueError, IndexError):
        return {"process": "stopped", "stale_pid": path.exists()}


def probe(url: str, *, json_body: bool = False) -> dict:
    try:
        with urlopen(url, timeout=2) as response:
            body = response.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise ValueError
        data = json.loads(body) if json_body else None
        if json_body and not isinstance(data, dict):
            raise ValueError
        return {"health": "healthy", "data": data}
    except (OSError, ValueError):
        return {"health": "unreachable", "data": None}


def sources(config: dict[str, str]) -> dict:
    path = config.get("TELEMETRY_SOURCES_FILE")
    if not path:
        return {"status": "not_configured", "sources": []}
    groups = build_file_discovery(json.loads(Path(path).read_text()))
    return inspect_sources(groups, config["PROMETHEUS_URL"], config["GRAFANA_URL"],
                           config["CLUSTER_NAME"], timeout=2)


def latest_run(config: dict[str, str]) -> Path | None:
    pointer = read_json(Path(config["TELEMETRY_HOME"]) / "state/last-cli-run.json").get("path")
    if isinstance(pointer, str) and Path(pointer).is_absolute() and (Path(pointer) / "telemetry-manifest.json").is_file():
        return Path(pointer)
    explicit = Path(config["RUN_ROOT"])
    if (explicit / "telemetry-manifest.json").is_file():
        return explicit
    root = Path(config["TELEMETRY_RUNS_ROOT"])
    try:
        candidates = list(root.glob("*/telemetry-manifest.json"))
        return max(candidates, key=lambda p: p.stat().st_mtime).parent if candidates else None
    except OSError:
        return None


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def status(config: dict[str, str], *, role: str = "all") -> dict:
    state = Path(config["TELEMETRY_HOME"]) / "state/verl-local"
    services = {role: process_identity(state / f"{role}.pid") for role in ("server", "node")}
    endpoints = {"prometheus": (config["PROMETHEUS_URL"] + "/-/ready", False),
                 "grafana": (config["GRAFANA_URL"] + "/api/health", True)}
    if config["ENABLE_LOGS"] == "1":
        endpoints["loki"] = (config["LOKI_URL"] + "/ready", False)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda item: probe(item[0], json_body=item[1]), endpoints.values()))
    for name, result in zip(endpoints, results):
        services[name] = {"health": result["health"], "ownership": "endpoint_only"}
        if name == "grafana" and result["health"] == "healthy" and result["data"].get("database") != "ok":
            services[name]["health"] = "degraded"
    services.setdefault("loki", {"health": "disabled"})
    services["server"]["health"] = ("healthy" if all(services[name]["health"] in {"healthy", "disabled"}
                                    for name in ("prometheus", "grafana", "loki")) else "degraded")
    for service_role in ("server", "node"):
        services[service_role]["ownership"] = "managed_launcher"
    target_result = probe(config["PROMETHEUS_URL"] + "/api/v1/targets?state=active", json_body=True)
    target_data = (target_result.get("data") or {}).get("data", {})
    targets = target_data.get("activeTargets", []) if isinstance(target_data, dict) else []
    if not isinstance(targets, list):
        targets = []
    targets = [t for t in targets if isinstance(t, dict) and isinstance(t.get("labels"), dict)]
    matching = [target for target in targets if target.get("labels", {}).get("job") == "telemetry"
                and target["labels"].get("cluster") == config["CLUSTER_NAME"]
                and target["labels"].get("nodename") == config["NODE_NAME"]]
    configured_targets = config.get("TELEMETRY_TARGETS") or f"{config['NODE_NAME']}={config['NODE_ADDR']}"
    expected = {entry.split("=", 1)[0] for entry in configured_targets.split(",")}
    cluster_targets = [target for target in targets if target["labels"].get("job") == "telemetry"
                       and target["labels"].get("cluster") == config["CLUSTER_NAME"]
                       and target["labels"].get("nodename") in expected]
    target_nodes = {target["labels"].get("nodename") for target in cluster_targets}
    collectors_ok = expected == target_nodes and all(target.get("health") == "up" for target in cluster_targets)
    services["node"]["health"] = ("healthy" if matching and all(t.get("health") == "up" for t in matching)
                                  else "degraded" if matching else "unreachable")
    now = time.time()
    node_output = Path(config["NODE_OUTPUT_DIR"])
    gpu_age = None
    try:
        for line in (node_output / "textfile/gpu.prom").read_text().splitlines():
            if line.startswith("telemetry_gpu_sample_timestamp_seconds "):
                gpu_age = now - float(line.split()[1])
    except (OSError, ValueError):
        pass
    if gpu_age is not None and not math.isfinite(gpu_age):
        gpu_age = None
    gpu = {"health": "disabled" if config["ENABLE_GPU_METRICS"] == "0" else
           "unavailable" if gpu_age is None else "clock_skew" if gpu_age < -5 else
           "fresh" if gpu_age <= 30 else "stale", "age_seconds": gpu_age}
    run = latest_run(config)
    manifest = read_json(run / "telemetry-manifest.json") if run else {}
    health = read_json(run / "telemetry-health.json") if run else {}
    snapshots = list((run / "telemetry-metrics").glob("verl-trainer-driver*.json")) if run else []
    latest = max(snapshots, key=lambda p: p.stat().st_mtime) if snapshots else None
    sample = read_json(latest) if latest else {}
    # Producer time, not file mtime, determines metric freshness.
    timestamp = sample.get("observed_at", sample.get("timestamp", sample.get("timestamp_unix_seconds")))
    if timestamp is None:
        timestamp = sample.get("timestamp_unix_nano")
        if isinstance(timestamp, (float, int)):
            timestamp /= 1e9
    age = now - timestamp if type(timestamp) in {float, int} and math.isfinite(timestamp) else None
    metrics = {"gpu": gpu, "verl": {"health": "not_configured" if run is None else
               "unavailable" if age is None else "clock_skew" if age < -5 else
               "fresh" if age <= float(config["TELEMETRY_METRICS_MAX_AGE_SECONDS"]) else "stale",
               "age_seconds": age}}
    native = sources(config)
    selected = ("server", "node") if role == "all" else (role,)
    owned = all(services[selected_role]["process"] == "running" for selected_role in selected)
    endpoints_ok = all(services[name]["health"] in {"healthy", "disabled"}
                       for name in ("prometheus", "grafana", "loki", "node"))
    if role == "all":
        endpoints_ok = endpoints_ok and collectors_ok
    native_ok = not native.get("backend_error") and all(row["status"] == "up" for row in native["sources"])
    if role == "server":
        endpoints_ok = services["server"]["health"] == "healthy" and collectors_ok
        gpu = {"health": "not_applicable", "age_seconds": None}
        metrics["gpu"] = gpu
    healthy = owned and endpoints_ok and native_ok and gpu["health"] in {"fresh", "disabled"}
    if role == "server":
        healthy = owned and endpoints_ok and native_ok
    reachable = any(services[name]["health"] == "healthy" for name in ("prometheus", "grafana", "node"))
    return {"status": "healthy" if healthy else "degraded" if owned or reachable else "stopped", "managed_role": role,
            "services": services, "metrics": metrics, "native_sources": native,
            "latest_run": {"path": str(run), "run_id": manifest.get("run_id"),
                           "execution_mode": manifest.get("configuration", {}).get("execution_mode"),
                           "step": sample.get("step"), "telemetry": health.get("status")} if run else None,
            "grafana_url": config["GRAFANA_URL"],
            "collector_targets": [{"node": node, "health": "up" if node in target_nodes and
                                    all(t.get("health") == "up" for t in cluster_targets if t["labels"].get("nodename") == node)
                                    else "down" if node in target_nodes else "not_discovered"} for node in sorted(expected)],
            "optional_sources": {"threefs": "configured_not_probed" if config.get("DIAGNOSTICS_CONFIG") and
                                 read_json(Path(config["DIAGNOSTICS_CONFIG"])).get("threefs") else "not_configured"},
            "note": "Endpoint health is not process ownership. Stale completed-run data is not a workload failure."}


def doctor(config: dict[str, str], *, role: str = "all") -> dict:
    checks = []

    def check(name, ok, action="", optional=False):
        checks.append({"component": name, "status": "ok" if ok else "disabled" if optional else "missing",
                       "action": "" if ok or optional else action})

    import sys
    check("Python >=3.10", sys.version_info >= (3, 10), "Install Python 3.10 or newer.")
    check("xlayer-telemetry", True)
    check("Linux", platform.system() == "Linux", "Use a Linux host for local collectors.")
    check("runtime assets", (assets_root() / "scripts/verl_local.sh").is_file())
    check("telemetry Python", os.access(config["TELEMETRY_PYTHON"], os.X_OK), "Set TELEMETRY_PYTHON to an installed Python.")
    state = Path(config["TELEMETRY_HOME"])
    ancestor = state
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    check("state directory", ancestor.is_dir() and os.access(ancestor, os.W_OK), "Choose a writable TELEMETRY_HOME.")
    for executable in ("bash", "curl", "flock", "tar", "unzip", "sha256sum"):
        check(executable, shutil.which(executable) is not None, f"Install {executable} using your OS package manager.")
    arch = "arm64" if platform.machine() in {"aarch64", "arm64"} else "amd64"
    tools = Path(config["TOOLS_DIR"])
    paths = {"Prometheus": tools / f"prometheus-3.5.0.linux-{arch}/prometheus",
             "Node Exporter": tools / f"node_exporter-1.9.1.linux-{arch}/node_exporter",
             "Grafana": tools / "grafana-v12.1.0/bin/grafana", "Loki": tools / f"loki-linux-{arch}",
             "Alloy": tools / f"alloy-linux-{arch}"}
    for name, path in paths.items():
        if role == "node" and name in {"Prometheus", "Grafana", "Loki"}:
            continue
        if role == "server" and name in {"Node Exporter", "Alloy"}:
            continue
        check(name, os.access(path, os.X_OK), "Run xltel install-tools.",
              optional=name in {"Loki", "Alloy"} and config["ENABLE_LOGS"] == "0")
    if config["ENABLE_GPU_METRICS"] == "1" and role != "server":
        from ..tool_check import _run_version
        available, _, _ = _run_version(("nvidia-smi", "--version"))
        accessible = False
        if available:
            try:
                accessible = subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=5).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
        check("GPU access", accessible, "Check NVIDIA driver access or set ENABLE_GPU_METRICS=0.")
    else:
        check("GPU metrics", False, optional=True)
    if config.get("TELEMETRY_SOURCES_FILE"):
        build_file_discovery(json.loads(Path(config["TELEMETRY_SOURCES_FILE"]).read_text()))
        check("native sources config", True)
    if config.get("DIAGNOSTICS_CONFIG"):
        from ..analysis.diagnostics import load_config
        load_config(Path(config["DIAGNOSTICS_CONFIG"]))
        check("diagnosis config", True)
    check("config", True)
    return {"status": "ready" if all(c["status"] != "missing" for c in checks) else "incomplete", "checks": checks}
