"""Official XLayer Telemetry CLI; launcher semantics stay in existing scripts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

from .operations.config import KEYS, ConfigError, assets_root, config_path, initialize, load_config, migrate, snapshot, validate
from .operations.run_artifacts import read_run_state
from .operations.health import dashboard_url, doctor, latest_run, sources, status


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="xltel", description="XLayer Telemetry: collect, correlate, diagnose.",
        epilog="Start: init -> doctor -> install-tools -> up -> run -- COMMAND -> inspect -> down")
    root.add_argument("--config", metavar="FILE", help="TOML or trusted Bash config (default: ~/.config/xlayer/config.toml; existing config.conf; XLAYER_CONFIG)")
    root.add_argument("--verbose", action="store_true", help="Show resolved config and runtime asset paths")
    commands = root.add_subparsers(dest="action", metavar="COMMAND")
    from .operations.clock import add_commands
    add_commands(commands)
    cluster = commands.add_parser("cluster", help="Validate observation configuration or render an explicit static inventory")
    cluster_sub = cluster.add_subparsers(dest="cluster_action", required=True)
    cluster_check = cluster_sub.add_parser("validate", help="Offline configuration checks; optional bounded observed scrape/clock checks")
    cluster_check.add_argument("--inventory", type=Path, help="Static TOML/JSON inventory; existing runtime config is optional")
    cluster_check.add_argument("--json", action="store_true")
    cluster_check.add_argument("--live", action="store_true", help="Read Prometheus target observations, not physical connectivity or resource ownership")
    cluster_check.add_argument("--correlation", action="store_true", help="Reuse doctor clock preflight; requires a runtime diagnosis config")
    cluster_render = cluster_sub.add_parser("render", help="Render existing configuration contracts into a new private bundle; do not deploy")
    cluster_render.add_argument("--inventory", type=Path, required=True)
    cluster_render.add_argument("--output", type=Path, required=True)
    cluster_render.add_argument('--prometheus-url', help='Explicit monitoring query URL propagated to server and node configs; does not change server bind/exposure')
    cluster_render.add_argument("--json", action="store_true")
    app = commands.add_parser('app', help='Install, inspect or update the existing Grafana App')
    app_sub = app.add_subparsers(dest='app_action', required=True)
    for action in ('install','status','update','rollback'):
        command = app_sub.add_parser(action)
        command.add_argument('--json', action='store_true')
        command.add_argument('--external', action='store_true', help='Use explicit existing external Grafana paths; never manage its process')
        command.add_argument('--plugins-dir', type=Path)
        command.add_argument('--provisioning-dir', type=Path)
        if action != 'status':
            command.add_argument('--restart', action='store_true', help='Explicitly restart owned managed server services; external Grafana is never restarted')
        if action in ('install','update'):
            command.add_argument('--package', type=Path, help='Prebuilt plugin ZIP; matching .sha256 required unless --sha256 is supplied')
            command.add_argument('--sha256')
            command.add_argument('--build', action='store_true', help='Build/test in an isolated checkout copy instead of using validated CI artifacts')
            command.add_argument('--allow-unsigned', action='store_true', help='Explicit isolated PoC opt-in for this App ID only; not production signing')
    runs = commands.add_parser('runs', help='Search and compare saved Run artifacts, independent of backend retention')
    runs_sub = runs.add_subparsers(dest='runs_action', required=True)
    for action in ('list','compare','publish'):
        command = runs_sub.add_parser(action)
        command.add_argument('--root', type=Path, help='Artifact parent or a single saved Run; defaults to TELEMETRY_RUNS_ROOT')
        command.add_argument('--json', action='store_true')
        if action=='list':
            command.add_argument('--search', default='')
            command.add_argument('--model')
            command.add_argument('--status')
            command.add_argument('--after',help='Recorded time filter: ISO8601 with timezone')
            command.add_argument('--before',help='Recorded time filter: ISO8601 with timezone')
        elif action=='compare':
            command.add_argument('run_a')
            command.add_argument('run_b')
        else:
            command.add_argument('--output', type=Path, help='Existing Grafana dashboard provisioning directory')
    commands.add_parser("init", help="Create local config without overwriting existing settings")
    for action, help_text in (("up", "Start the managed monitoring stack"),
                              ("down", "Stop only this config's managed telemetry processes"),
                              ("restart", "Stop and start the managed stack"),
                              ("install-tools", "Download monitoring tools (no VERL or GPU driver installation)")):
        command = commands.add_parser(action, help=help_text)
        command.add_argument("--role", choices=("all", "server", "node"), default="all", help="Manage only this host's selected role (default: both)")
    for action, help_text in (("doctor", "Check config, installation and device access"),
                              ("status", "Show process ownership, endpoint health and sample freshness")):
        command = commands.add_parser(action, help=help_text)
        command.add_argument("--json", action="store_true", help="Machine-readable output")
        command.add_argument("--role", choices=("all", "server", "node"), default="all", help="Check this host's selected role")
        if action == "doctor":
            command.add_argument("--correlation", action="store_true", help="Read-only inventory and observed clock quality preflight")
            command.add_argument("--diagnostics-config", type=Path, help="Diagnosis JSON for correlation preflight (otherwise DIAGNOSTICS_CONFIG)")
    run = commands.add_parser("run", help="Wrap an existing VERL command; preserve its exit code",
                              epilog="Example: xltel run --mode async -- python -m verl.trainer.main_ppo ...")
    run.add_argument("--mode", choices=("auto", "sync", "async"))
    run.add_argument("--run-id", help="Unique run ID (default: generated for each run)")
    run.add_argument("--output", type=Path, help="Run artifact directory; cannot contain an earlier run")
    run.add_argument("--node", help="Logical collector node name")
    run.add_argument('--model',help='Explicit Run metadata only; does not configure framework weights')
    run.add_argument('--workload-fingerprint',help='Explicit comparison cohort identifier; not proof of full workload equivalence')
    run.add_argument("command", nargs=argparse.REMAINDER, help="Workload argv after --; otherwise VERL_COMMAND array")
    inspect = commands.add_parser("inspect", help="Inspect saved artifacts, not current service health")
    inspect.add_argument("run", nargs="?", help="Run ID or directory (default: configured/latest run)")
    inspect.add_argument('--execution-graph', action='store_true', help='Read bounded saved SDK execution relationships as JSON; never infer edges from Step/time')
    inspect.add_argument('--root-span', metavar='TRACE/SPAN', help='Explicit complete trajectory root for conservative critical-path analysis; requires --execution-graph')
    logs = commands.add_parser("logs", help="Read managed launcher logs")
    logs.add_argument("role", nargs="?", choices=("server", "node"))
    logs.add_argument("--follow", "-f", action="store_true")
    logs.add_argument("--lines", "-n", type=int, default=50)
    source = commands.add_parser("sources", help="Inspect native scrape sources independently of runs")
    source.add_argument("--json", action="store_true")
    sub = source.add_subparsers(dest="source_action")
    sub.add_parser("refresh", help="Reload registered endpoints into initialized file discovery")
    threefs = sub.add_parser("threefs", help="Query optional ClickHouse distributions and raw counters")
    threefs.add_argument("--window-seconds", type=float, default=300)
    threefs.add_argument("--series", action="store_true", help="Read bounded collection reports from threefs.time_series settings")
    threefs.add_argument("--start", type=float, help="Explicit source-clock epoch seconds; requires --end")
    threefs.add_argument("--end", type=float, help="Exclusive source-clock epoch seconds; requires --start")
    cfg = commands.add_parser("config", help="Locate, show or validate resolved configuration")
    cfg_sub = cfg.add_subparsers(dest="config_action", required=True)
    cfg_sub.add_parser("path", help="Print selected config path")
    cfg_sub.add_parser("show", help="Show known resolved values; workload argv is redacted")
    cfg_sub.add_parser("validate", help="Validate TOML or trusted Bash config without starting services")
    migration = cfg_sub.add_parser("migrate", help="Write resolved settings and command to private TOML; keep source unchanged")
    migration.add_argument("--output", type=Path, required=True)
    completion = commands.add_parser("completion", help="Print shell completion; does not read config or change dotfiles")
    completion.add_argument("shell", choices=("bash", "zsh", "fish"))
    return root


def _launch(config: dict[str, str], action: str, role: str = "all") -> int:
    root = assets_root()
    from .operations.config import effective_targets
    config={**config,'TELEMETRY_TARGETS':','.join(f'{node}={address}' for node,address in effective_targets(config).items())}
    effective = snapshot(config)
    # No shell interpolation of user values; the existing lifecycle lock owns mutations.
    child = subprocess.Popen(["bash", str(root / "scripts/verl_local.sh"), "--config", str(effective), action],
                             cwd=root, env=os.environ | config | {"XLAYER_CLI": "1", "XLAYER_MANAGED_ROLE": role})
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for sig in previous:
            signal.signal(sig, lambda signum, frame: child.send_signal(signum) if child.poll() is None else None)
        code = child.wait()
        if code and action in {"up", "install"}:
            print("xltel: check xltel doctor and xltel logs; use xltel install-tools for missing monitoring binaries.", file=sys.stderr)
        return code if code >= 0 else 128 - code
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _print_health(result: dict) -> None:
    print(f"XLayer Telemetry: {result['status']}  role={result.get('managed_role', 'all')}\n")
    print(f"{'Component':<22} {'Process / ownership':<24} Health")
    for name, data in result["services"].items():
        label = "managed " + name if name in {"server", "node"} else name
        print(f"{label:<22} {data.get('process', data.get('ownership', '-')):<24} {data.get('health', '-')}")
    for name, data in result["metrics"].items():
        label = "VERL saved snapshot" if name == "verl" else name + " metrics"
        print(f"{label:<22} {data.get('scope', 'sample'):<24} {data['health']}")
    if result["native_sources"].get("config_error"):
        print("Native sources: invalid_config; " + result["native_sources"]["next_action"])
    for data in result["native_sources"]["sources"]:
        print(f"{data['telemetry_source']:<22} {'configured source':<24} {data['status']}")
    for name, value in result.get("optional_sources", {}).items():
        print(f"{name:<22} {'optional source':<24} {value}")
    for target in result.get("collector_targets", []):
        print(f"{target['node']:<22} {'collector target':<24} {target['health']}")
        if target.get('configuration_status') != 'matched':
            print(f"  Target configuration: {target.get('configuration_status', 'unknown')}; check TELEMETRY_TARGETS and Prometheus scrape configuration.")
    if result.get('target_discovery', {}).get('action'):
        print(result['target_discovery']['action'])
    if result["latest_run"]:
        run = result["latest_run"]
        print(f"\nLatest saved run: {run['run_id']}  mode={run['execution_mode']}  step={run['step']}")
        print(f"Recorded workload: {run['workload']['status']}  exit={run['workload']['exit_code']}  telemetry={run['telemetry']}")
    print(f"\nGrafana: {result['grafana_url']}\nStart Here: {result['investigation_url']}\n{result['note']}")


def _run(args, config: dict[str, str], configured_command: list[str]) -> None:
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    command = command or configured_command
    if not command or not command[0] or command[0].startswith("/path/to/"):
        raise ConfigError("Pass xltel run -- COMMAND or set [workload].command in TOML (VERL_COMMAND=(...) in Bash).")
    run_id = args.run_id or config["RUN_ID"]
    if run_id == "auto":
        run_id = "verl-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    output = args.output.expanduser().absolute() if args.output else Path(config["RUN_ROOT"])
    if not args.output and (args.run_id or config["RUN_ID"] == "auto"):
        root = Path(config["TELEMETRY_RUNS_ROOT"]).resolve()
        output = (root / run_id).resolve()
        if run_id in {'.', '..'} or output == root or not output.is_relative_to(root):
            raise ConfigError('Run output must remain inside TELEMETRY_RUNS_ROOT. Choose a non-reserved Run ID or an explicit --output.')
    config = config | {"RUN_ID": run_id, "RUN_ROOT": str(output)}
    if args.node:
        config["NODE_NAME"] = args.node
    if args.mode:
        config["EXECUTION_MODE"] = args.mode
    validate(config)
    argv = ["bash", str(assets_root() / "scripts/run_verl_with_telemetry.sh"),
            "--output", str(output), "--run-id", run_id, "--node", config["NODE_NAME"],
            "--execution-mode", config["EXECUTION_MODE"],
            "--set", "cluster=" + config["CLUSTER_NAME"],
            "--set", "observer_node=" + config["NODE_NAME"]]
    for option,key in (('model','model_identifier'),('workload_fingerprint','workload_fingerprint')):
        value=getattr(args,option,None)
        if value is not None:
            if not value or len(value)>256 or any(ord(c)<32 for c in value):raise ConfigError('Run metadata must be a nonempty printable string of at most 256 characters.')
            argv.extend(['--set',key+'='+value])
    if config.get("DIAGNOSTICS_CONFIG"):
        argv.extend(["--diagnostics-config", config["DIAGNOSTICS_CONFIG"]])
    argv.extend(["--", *command])
    from .fileio import atomic_write_text
    state = Path(config["TELEMETRY_HOME"]) / "state"
    state.mkdir(parents=True, exist_ok=True)
    # Invalid/failed startup pointers are ignored unless a manifest exists.
    atomic_write_text(state / "last-cli-run.json", json.dumps({"path": str(output)}) + "\n")
    print(f"Run {run_id}  node={config['NODE_NAME']}  mode={config['EXECUTION_MODE']}\nOutput: {output}", flush=True)
    print("Run Overview: " + dashboard_url(config, "telemetry-overview", cluster=config["CLUSTER_NAME"],
          run_id=run_id, node=config["NODE_NAME"], source_node=config["NODE_NAME"]), flush=True)
    if output.parent != Path(config["TELEMETRY_RUNS_ROOT"]):
        print("Warning: output is outside TELEMETRY_RUNS_ROOT; configure collector input to see this run live.", file=sys.stderr)
    # Replace Python with the wrapper: SIGINT/SIGTERM and workload exit code stay unchanged.
    os.execvpe("bash", argv, os.environ | config)


def execute(args) -> int:
    if args.action == "cluster":
        from .operations.cluster import execute_cluster
        return execute_cluster(args)
    if args.action == "clock":
        from .operations.clock import execute as clock_execute
        return clock_execute(args)
    if args.action == "completion":
        from .operations.completion import generate
        print(generate(parser(), args.shell), end="")
        return 0
    path = config_path(args.config)
    if args.action == "init":
        created = initialize(path)
        print(f"{'Created' if created else 'Kept existing config'}: {path}\nNext: xltel doctor; xltel install-tools; xltel up")
        return 0
    if args.action == "config" and args.config_action == "path":
        print(path)
        return 0
    if args.action == "config" and args.config_action == "migrate":
        output = args.output.expanduser().absolute()
        migrate(path, output)
        print(f"Created: {output}\nUse: xltel --config {output} config validate")
        print("Migration resolves current environment overrides and paths; keep environment exports separately. Source was preserved.", file=sys.stderr)
        return 0
    config, command = load_config(path)
    if args.verbose:
        print(f"Config: {path}\nRuntime assets: {assets_root()}", file=sys.stderr)
    if args.action == 'app':
        from .operations import app
        paths = {'external':args.external,'plugins_dir':args.plugins_dir,'provisioning_dir':args.provisioning_dir}
        if not args.external and (args.plugins_dir or args.provisioning_dir):
            raise ConfigError('Explicit Grafana paths require --external; managed paths come from this config.')
        if args.app_action != 'status' and args.restart and args.external:
            raise ConfigError('External Grafana is never restarted by xltel. Use its existing service/deployment procedure.')
        if args.app_action in ('install','update'):
            with app.package_source(config, package=args.package, sha256=args.sha256, build=args.build) as (package,digest,source):
                result = app.install_package(config,package,digest,allow_unsigned=args.allow_unsigned,
                                             update=args.app_action=='update',**paths)
                result = {**result,'source':source}
        elif args.app_action=='rollback': result = app.rollback(config,**paths)
        else: result = app.status(config,**paths)
        if args.app_action in ('install','update') and not args.restart:
            observed=app.status(config,**paths)
            if observed['status']=='ready':result={**result,**observed}
        if getattr(args,'restart',False) and result.get('changed'):
            code = _launch(config,'down','server')
            if not code: code = _launch(config,'up','server')
            if code:
                if args.app_action=='update':
                    app.rollback(config,**paths)
                    _launch(config,'up','server')
                raise ConfigError('Managed activation failed; update rollback was attempted. Inspect this server config’s logs before retrying.')
            result = app.status(config,**paths)
        if args.json: print(json.dumps(result,indent=2,ensure_ascii=False))
        else:
            print('XLayer Telemetry App')
            for check in result.get('checks',[]): print(f"[{check['status'].upper()}] {check['component']}: {check['detail']}")
            print('Status: '+result['status'])
            if result.get('action') and result['status']=='needs_attention':print('Action: '+result['action'])
            if result.get('source'):print('Source: '+result['source'])
            if result.get('dashboard'): print('Dashboard: '+result['dashboard'])
            elif result['status']=='restart_required': print('Action: Restart Grafana with the existing lifecycle; managed: xltel restart --role server.')
            print('Reference: docs/app-deployment-reference.md')
        return 1 if result['status']=='needs_attention' else 0
    if args.action == 'runs':
        from .operations import runs
        root = args.root or Path(config['TELEMETRY_RUNS_ROOT'])
        if args.runs_action=='publish': result = runs.publish(root,args.output or Path(config['SERVER_OUTPUT_DIR'])/'dashboards')
        else:
            result = runs.catalog(root)
            if args.runs_action=='compare':
                selected=[]
                for name in (args.run_a,args.run_b):
                    matches=[row for row in result['runs'] if name in (row['run_id'],row['key'])]
                    if len(matches)!=1: raise ConfigError('Run identity is missing or ambiguous. Use xltel runs list --json and select its exact key.')
                    selected.append(matches[0])
                result = runs.compare(*selected)
            else:
                def cutoff(value):
                    if not value:return None
                    try:
                        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
                        if parsed.tzinfo is None:raise ValueError
                        return parsed.timestamp()*1000
                    except ValueError:raise ConfigError('Run time filters require ISO8601 with timezone, such as 2026-10-10T00:00:00Z.') from None
                after,before=cutoff(args.after),cutoff(args.before)
                if after is not None and before is not None and after>=before:raise ConfigError('--after must precede --before.')
                result['runs']=[row for row in result['runs'] if args.search.lower() in (row['run_id']+' '+(row['model'] or '')).lower()
                    and (not args.model or row['model']==args.model) and (not args.status or row['status']==args.status)
                    and (after is None or row['to'] is not None and row['to']>=after)
                    and (before is None or row['from'] is not None and row['from']<=before)]
        if args.json: print(json.dumps(result,indent=2,ensure_ascii=False))
        elif args.runs_action=='list':
            print('Run ID                         Model                  Observations  Avg Step (s)  Recorded Status')
            for row in result['runs']: print(f"{row['run_id']:<30} {(row['model'] or 'Unknown')[:22]:<22} {row['steps']:<13} {str(row['average_step'] if row['average_step'] is not None else 'N/A'):<13} {row['status']}")
            if result['truncated']: print('[WARN] Catalog limit reached; select a smaller --root. Counts describe retained records, not total training steps.')
        elif args.runs_action=='compare':
            print('Comparability: '+result['comparability'])
            for row in result['metrics']: print(f"{row['metric']}: {row['a']} → {row['b']} {row.get('unit') or ''}; change={row['delta_percent'] if row['delta_percent'] is not None else 'N/A'}% · {', '.join(row['reasons'])}")
        else: print(f"Published {result['run_count']} saved Runs: {result['path']}\nOpen /a/xlayer-telemetry-app/runs. Source: stored artifact snapshot; refresh publication after new records.")
        return 0
    if args.action == "config":
        if args.config_action == "show":
            visible = {key: value for key, value in config.items() if key in KEYS}
            print(json.dumps(visible | {"VERL_COMMAND": f"<{len(command)} arguments; redacted>"}, indent=2))
        else:
            from .operations.cluster import configuration_report
            report = configuration_report(config)
            errors = [row for row in report['issues'] if row['severity'] == 'error']
            if errors:
                details = {row['code'] + (f" ({row['error_type']})" if row.get('error_type') else '') for row in errors}
                raise ConfigError('Invalid topology/cluster configuration: ' + ', '.join(sorted(details)) + '. Use xltel cluster validate --json.')
            for row in report['issues']:
                print(f"[WARN] {row['code']}: {row['message']}")
            print(f"Valid config: {path}")
        return 0
    if args.action == "doctor":
        if args.diagnostics_config:
            config={**config,"DIAGNOSTICS_CONFIG":str(args.diagnostics_config.expanduser().absolute())}
        result = doctor(config, role=args.role, **({'correlation':True} if args.correlation else {}))
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for item in result["checks"]:
                print(f"[{item['status']}] {item['component']}" + (f" -> {item['action']}" if item["action"] else ""))
            preflight=result.get('correlation_preflight')
            if preflight:
                print('Correlation preflight: '+preflight['status'])
                for node,roles in preflight.get('inventory',{}).get('nodes',{}).items():
                    quality=preflight['clock_quality']['nodes'][node]
                    print(f"[{quality['status']}] {node} · {', '.join(roles)} · uncertainty={quality.get('uncertainty_seconds')}s · {', '.join(quality['issues'])}")
                for issue in preflight.get('inventory',{}).get('issues',preflight.get('issues',[])):
                    print(issue)
        return 0 if result["status"] == "ready" else 1
    if args.action == "status":
        result = status(config, role=args.role)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            _print_health(result)
        return 0 if result["status"] == "healthy" else 1
    if args.action in {"up", "down", "restart", "install-tools"}:
        if args.action == "restart":
            code = _launch(config, "down", args.role)
            if code:
                return code
        return _launch(config, "install" if args.action == "install-tools" else "up" if args.action == "restart" else args.action, args.role)
    if args.action == "run":
        _run(args, config, command)
    if args.action == "inspect":
        if args.run:
            candidate = Path(args.run).expanduser()
            if candidate.is_absolute() or "/" in args.run or candidate.is_dir():
                run = candidate.absolute()
            elif re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", args.run):
                run = Path(config["TELEMETRY_RUNS_ROOT"]) / args.run
            else:
                raise ConfigError("Invalid run ID; pass a run ID or directory.")
        else:
            run = latest_run(config)
        if not run or not run.is_dir():
            raise ConfigError("No run artifacts found; run a workload first or pass xltel inspect RUN_DIR.")
        if args.root_span and not args.execution_graph:
            raise ConfigError('--root-span requires --execution-graph.')
        if args.execution_graph:
            from .operations.execution import inspect_execution
            root = args.root_span.split('/') if args.root_span else None
            if root is not None and (len(root) != 2 or any(not part for part in root)):
                raise ConfigError('--root-span needs TRACE/SPAN from an observed SDK span.')
            print(json.dumps(inspect_execution(run, root=root), ensure_ascii=False, indent=2, allow_nan=False))
            return 0
        from .show_run import summarize
        print(summarize(run))
        from .time_alignment import CalibrationCache, reference_now
        calibration = CalibrationCache(Path(config["TELEMETRY_TIME_CALIBRATION_FILE"]), node=config["NODE_NAME"]) if config.get("TELEMETRY_TIME_CALIBRATION_FILE") else None
        saved = read_run_state(run, now=reference_now(calibration, time.time()),
            max_age_seconds=float(config["TELEMETRY_METRICS_MAX_AGE_SECONDS"]))
        run_id = saved["run_id"]
        cluster = saved["cluster"] or config["CLUSTER_NAME"]
        node = saved["observer_node"] or ""
        if isinstance(run_id, str):
            print("\nRun Overview: " + dashboard_url(config, "telemetry-overview",
                  cluster=cluster, run_id=run_id, node=node, source_node=node))
        return 0
    if args.action == "logs":
        if args.lines <= 0:
            raise ConfigError("--lines must be positive.")
        directory = Path(config["TELEMETRY_HOME"]) / "state/verl-local"
        paths = [directory / f"{role}.log" for role in ([args.role] if args.role else ["server", "node"])]
        if not all(p.is_file() for p in paths):
            raise ConfigError(f"Managed logs not found in {directory}; start xltel up first.")
        os.execvp("tail", ["tail", "-n", str(args.lines), *(["-F"] if args.follow else []), *map(str, paths)])
    if args.action == "sources":
        if args.source_action == "refresh":
            return _launch(config, "refresh-sources")
        if args.source_action == "threefs":
            if not config.get("DIAGNOSTICS_CONFIG"):
                raise ConfigError("Set DIAGNOSTICS_CONFIG to query 3FS.")
            from .analysis.diagnostics import load_config as load_diagnosis
            from .subsystems import inspect_threefs
            result = inspect_threefs(load_diagnosis(Path(config["DIAGNOSTICS_CONFIG"])),
                                     seconds=args.window_seconds, environment=os.environ | config,
                                     series=args.series, start=args.start, end=args.end)
        else:
            result = sources(config)
        if args.json or args.source_action == "threefs":
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print("Source               Status             Scope")
            for row in result["sources"]:
                print(f"{row['telemetry_source']:<20} {row['status']:<18} {row['scope']}")
                print(f"  {row['metrics_url']}")
            if result.get("config_error"):
                print("Native sources: invalid_config; " + result["next_action"])
            elif not result["sources"]:
                print("No native sources configured; set TELEMETRY_SOURCES_FILE.")
        return 1 if result.get("backend_error") or result.get("config_error") or any(r["status"] != "up" for r in result.get("sources", [])) else 0
    return 0


def main(argv: list[str] | None = None) -> int:
    command_parser = parser()
    args = command_parser.parse_args(argv)
    if not args.action:
        command_parser.print_help()
        return 0
    try:
        return execute(args)
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as exc:
        # ConfigError contains only validated fields; external errors may include secrets.
        message = str(exc) if isinstance(exc, ConfigError) else f"{type(exc).__name__}; check config, paths and backend connectivity."
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": message}))
        print(f"xltel: {message}", file=sys.stderr)
        if getattr(exc,'action',None):print('Action: '+exc.action,file=sys.stderr)
        if getattr(exc,'reference',None):print('Reference: '+exc.reference,file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
