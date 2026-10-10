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
from urllib.parse import urlencode

from .._http_transport import request_bytes
from .config import assets_root
from .run_artifacts import read_run_state
from ..source_discovery import load_file_discovery
from ..subsystems import inspect_sources, parse_target_response, summarize_sources


def dashboard_url(config: dict[str, str], uid: str, **variables: str) -> str:
    query = urlencode({"var-" + key: value for key, value in variables.items() if value})
    return config["GRAFANA_URL"].rstrip("/") + "/d/" + uid + ("?" + query if query else "")


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
        body = request_bytes(url, None, 2, max_response_bytes=4 * 1024 * 1024)
        data = json.loads(body) if json_body else None
        if json_body and not isinstance(data, dict):
            raise ValueError
        return {"health": "healthy", "data": data}
    except (OSError, ValueError, RuntimeError):
        return {"health": "unreachable", "data": None}


def sources(config: dict[str, str], *, targets: list[dict] | None = None,
            backend_error: str | None = None) -> dict:
    path = config.get("TELEMETRY_SOURCES_FILE")
    if not path:
        return {"status": "not_configured", "sources": []}
    try:
        groups = load_file_discovery(Path(path))
    except (OSError, ValueError, TypeError) as exc:
        return {"status": "invalid_config", "config_error": type(exc).__name__, "sources": [],
                "next_action": "Check TELEMETRY_SOURCES_FILE with xltel config validate."}
    if targets is not None:
        return summarize_sources(groups, targets, config["GRAFANA_URL"], config["CLUSTER_NAME"],
                                 backend_error=backend_error)
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


def _target_configuration(address, targets):
    """Compare literal configured hosts; never resolve DNS or infer connectivity."""
    from ipaddress import ip_address
    from urllib.parse import urlsplit
    if len(targets) != 1:
        return 'ambiguous' if targets else 'unknown'
    try:
        parsed = urlsplit(targets[0].get('scrapeUrl', ''))
        actual = parsed.hostname
        if not actual or parsed.port != 19100 or parsed.path != '/metrics':
            return 'unknown'
        if actual.casefold() == address.casefold():
            return 'matched'
        try:
            return 'matched' if ip_address(actual) == ip_address(address) else 'mismatch'
        except ValueError:
            return 'unknown'
    except (TypeError, ValueError):
        return 'unknown'


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
    target_error = None
    try:
        targets = parse_target_response(target_result.get("data"))
    except ValueError:
        targets = []
        target_error = "unreachable" if target_result["health"] != "healthy" else "invalid_response"
    matching = [target for target in targets if target.get("labels", {}).get("job") == "telemetry"
                and target["labels"].get("cluster") == config["CLUSTER_NAME"]
                and target["labels"].get("nodename") == config["NODE_NAME"]]
    from .config import effective_targets
    addresses = effective_targets(config)
    expected = set(addresses)
    cluster_targets = [target for target in targets if target["labels"].get("job") == "telemetry"
                       and target["labels"].get("cluster") == config["CLUSTER_NAME"]
                       and target["labels"].get("nodename") in expected]
    target_nodes = {target["labels"].get("nodename") for target in cluster_targets}
    collector_rows = []
    for name in sorted(expected):
        rows = [target for target in cluster_targets if target['labels'].get('nodename') == name]
        collector_rows.append({'node': name, 'health': 'up' if rows and all(t.get('health') == 'up' for t in rows)
            else 'down' if rows else 'unavailable' if target_error else 'not_discovered',
            'configuration_status': _target_configuration(addresses[name], rows), 'expected_address': addresses[name]})
    collectors_ok = expected == target_nodes and all(row['health'] == 'up' and row['configuration_status'] == 'matched' for row in collector_rows)
    services["node"]["health"] = ("healthy" if matching and all(t.get("health") == "up" for t in matching)
                                  else "degraded" if matching else "unknown")
    own_configuration = _target_configuration(addresses.get(config['NODE_NAME'], config['NODE_ADDR']), matching)
    services['node']['configuration_status'] = own_configuration
    from ..time_alignment import CalibrationCache, reference_now
    calibration = CalibrationCache(Path(config["TELEMETRY_TIME_CALIBRATION_FILE"]), node=config["NODE_NAME"]) if config.get("TELEMETRY_TIME_CALIBRATION_FILE") else None
    now = reference_now(calibration, time.time())
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
    saved_run = read_run_state(run, now=now,
        max_age_seconds=float(config["TELEMETRY_METRICS_MAX_AGE_SECONDS"])) if run else None
    snapshot = saved_run["snapshot"] if saved_run else {
        "scope": "stored_artifact", "health": "not_configured", "age_seconds": None}
    metrics = {"gpu": gpu, "verl": snapshot}
    native = sources(config, targets=targets, backend_error=target_error)
    selected = ("server", "node") if role == "all" else (role,)
    owned = all(services[selected_role]["process"] == "running" for selected_role in selected)
    endpoints_ok = all(services[name]["health"] in {"healthy", "disabled"}
                       for name in ("prometheus", "grafana", "loki", "node"))
    if role == "all":
        endpoints_ok = endpoints_ok and collectors_ok
    native_ok = not (native.get("backend_error") or native.get("config_error")) and all(row["status"] == "up" for row in native["sources"])
    if role == "server":
        endpoints_ok = services["server"]["health"] == "healthy" and collectors_ok
        gpu = {"health": "not_applicable", "age_seconds": None}
        metrics["gpu"] = gpu
    if role == "node":
        # A collector depends on scrape/log delivery, not a remote UI or engines.
        endpoints_ok = services["node"]["health"] == "healthy" and own_configuration == 'matched' and services["loki"]["health"] in {"healthy", "disabled"}
        native_ok = True
    healthy = owned and endpoints_ok and native_ok and gpu["health"] in {"fresh", "disabled"}
    if role == "server":
        healthy = owned and endpoints_ok and native_ok
    reachable = any(services[name]["health"] == "healthy" for name in ("prometheus", "grafana", "node"))
    return {"status": "healthy" if healthy else "degraded" if owned or reachable else "stopped", "managed_role": role,
            "services": services, "metrics": metrics, "native_sources": native,
            "latest_run": saved_run,
            "grafana_url": config["GRAFANA_URL"],
            "investigation_url": dashboard_url(config, "xlayer-start-here", cluster=config["CLUSTER_NAME"], node=config["NODE_NAME"]),
            "target_discovery": {"status": "unavailable" if target_error else "observed", "error": target_error,
                **({'action': 'Check PROMETHEUS_URL and monitoring-host connectivity; unavailable discovery is not evidence of collector failure.'} if target_error else {})},
            "collector_targets": collector_rows,
            "optional_sources": {"threefs": "configured_not_probed" if config.get("DIAGNOSTICS_CONFIG") and
                                 read_json(Path(config["DIAGNOSTICS_CONFIG"])).get("threefs") else "not_configured"},
            "note": "Endpoint health is not process ownership. Stale completed-run data is not a workload failure."}


def correlation_preflight(config: dict[str,str], *, client=None, now=None) -> dict:
    """Read-only admission check; each diagnosis still checks its own window."""
    from ..analysis.clock_quality import assess_clocks, clock_inventory
    from ..analysis.diagnostics import load_config
    from ..analysis.query_budget import QueryBudget
    from ..prometheus import _DeadlinePrometheusClient
    path=config.get('DIAGNOSTICS_CONFIG')
    if not path:
        return {'status':'not_configured','issues':['DIAGNOSTICS_CONFIG missing'],
                'system_time_changed':False}
    settings=load_config(Path(path))
    inventory=clock_inventory(settings,settings.get('node') or config['NODE_NAME'])
    policy=settings.get('clock',{})
    end=time.time() if now is None else now
    budget=QueryBudget(settings.get('query_budget_seconds',30))
    backend=client or _DeadlinePrometheusClient(settings['prometheus']['url'])
    backend=budget.wrap(backend,'prometheus',configurable_timeout=client is None)
    quality=assess_clocks(backend.query_range,cluster=settings.get('cluster',''),nodes=inventory['nodes'],
        start=end-60,end=end,max_skew_seconds=policy.get('max_skew_seconds',1),
        max_sample_age_seconds=policy.get('max_sample_age_seconds',30),
        max_uncertainty_seconds=policy.get('max_uncertainty_seconds',policy.get('max_skew_seconds',1)),
        require_sync=True)
    status='blocked' if quality['status']=='unsafe' else 'pass' if quality['status']=='aligned' and not inventory['issues'] else 'needs_attention'
    return {'status':status,'inventory':inventory,'clock_quality':quality,
            'query_execution':budget.summary(),'system_time_changed':False,
            'interpretation':'sampled preflight; not continuous clock proof, topology discovery or resource attribution'}


def doctor(config: dict[str, str], *, role: str = "all", correlation: bool = False) -> dict:
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
    for executable in ("bash", "curl", "flock", "setsid", "tar", "unzip", "sha256sum"):
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
    from .cluster import configuration_report
    configuration = configuration_report(config, correlation=correlation)
    if config.get("TELEMETRY_SOURCES_FILE"):
        check("native sources config", not any(row['code'] == 'invalid_native_sources' for row in configuration['issues']),
              "Run xltel cluster validate --json.")
    if config.get("DIAGNOSTICS_CONFIG"):
        check("diagnosis config", configuration['diagnosis_config'] == 'validated',
              "Run xltel cluster validate --json.")
    check("cluster configuration", configuration['status'] == 'valid',
          "Run xltel cluster validate --json; distinguish configuration issues from missing observations.")
    check("config", True)
    preflight=configuration['clock_preflight'] if correlation else None
    if preflight is not None:
        check('correlation prerequisites',preflight['status']=='pass','Check correlation_preflight nodes, clocks and diagnostics config.')
    return {"status": "ready" if all(c["status"] != "missing" for c in checks) else "incomplete", "checks": checks,
            "cluster_configuration": configuration,
            **({'correlation_preflight':preflight} if preflight is not None else {})}
