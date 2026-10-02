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
import uuid

from .operations.config import KEYS, ConfigError, assets_root, config_path, initialize, load_config, migrate, snapshot, validate
from .operations.health import doctor, latest_run, sources, status


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="xltel", description="XLayer Telemetry: collect, correlate, diagnose.",
        epilog="Start: init -> doctor -> install-tools -> up -> run -- COMMAND -> inspect -> down")
    root.add_argument("--config", metavar="FILE", help="TOML or trusted Bash config (default: ~/.config/xlayer/config.toml; existing config.conf; XLAYER_CONFIG)")
    root.add_argument("--verbose", action="store_true", help="Show resolved config and runtime asset paths")
    commands = root.add_subparsers(dest="action", metavar="COMMAND")
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
    run = commands.add_parser("run", help="Wrap an existing VERL command; preserve its exit code",
                              epilog="Example: xltel run --mode async -- python -m verl.trainer.main_ppo ...")
    run.add_argument("--mode", choices=("auto", "sync", "async"))
    run.add_argument("--run-id", help="Unique run ID (default: generated for each run)")
    run.add_argument("--output", type=Path, help="Run artifact directory; cannot contain an earlier run")
    run.add_argument("--node", help="Logical collector node name")
    run.add_argument("command", nargs=argparse.REMAINDER, help="Workload argv after --; otherwise VERL_COMMAND array")
    inspect = commands.add_parser("inspect", help="Inspect saved artifacts, not current service health")
    inspect.add_argument("run", nargs="?", help="Run ID or directory (default: configured/latest run)")
    logs = commands.add_parser("logs", help="Read managed launcher logs")
    logs.add_argument("role", nargs="?", choices=("server", "node"))
    logs.add_argument("--follow", "-f", action="store_true")
    logs.add_argument("--lines", "-n", type=int, default=50)
    source = commands.add_parser("sources", help="Inspect native scrape sources independently of runs")
    source.add_argument("--json", action="store_true")
    sub = source.add_subparsers(dest="source_action")
    sub.add_parser("refresh", help="Reload registered endpoints into initialized file discovery")
    threefs = sub.add_parser("threefs", help="Query optional ClickHouse shared-service distributions")
    threefs.add_argument("--window-seconds", type=float, default=300)
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
        print(f"{name + ' metrics':<22} {'sample':<24} {data['health']}")
    for data in result["native_sources"]["sources"]:
        print(f"{data['telemetry_source']:<22} {'configured source':<24} {data['status']}")
    for name, value in result.get("optional_sources", {}).items():
        print(f"{name:<22} {'optional source':<24} {value}")
    for target in result.get("collector_targets", []):
        print(f"{target['node']:<22} {'collector target':<24} {target['health']}")
    if result["latest_run"]:
        run = result["latest_run"]
        print(f"\nLatest run: {run['run_id']}  mode={run['execution_mode']}  step={run['step']}")
    print(f"\nGrafana: {result['grafana_url']}\n{result['note']}")


def _run(args, config: dict[str, str], configured_command: list[str]) -> None:
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    command = command or configured_command
    if not command or not command[0] or command[0].startswith("/path/to/"):
        raise ConfigError("Pass xltel run -- COMMAND or set VERL_COMMAND=(...) in the config.")
    run_id = args.run_id or config["RUN_ID"]
    if run_id == "auto":
        run_id = "verl-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    output = args.output.expanduser().absolute() if args.output else Path(config["RUN_ROOT"])
    if not args.output and (args.run_id or config["RUN_ID"] == "auto"):
        output = Path(config["TELEMETRY_RUNS_ROOT"]) / run_id
    config = config | {"RUN_ID": run_id, "RUN_ROOT": str(output)}
    if args.node:
        config["NODE_NAME"] = args.node
    if args.mode:
        config["EXECUTION_MODE"] = args.mode
    validate(config)
    argv = ["bash", str(assets_root() / "scripts/run_verl_with_telemetry.sh"),
            "--output", str(output), "--run-id", run_id, "--node", config["NODE_NAME"],
            "--execution-mode", config["EXECUTION_MODE"]]
    if config.get("DIAGNOSTICS_CONFIG"):
        argv.extend(["--diagnostics-config", config["DIAGNOSTICS_CONFIG"]])
    argv.extend(["--", *command])
    from .fileio import atomic_write_text
    state = Path(config["TELEMETRY_HOME"]) / "state"
    state.mkdir(parents=True, exist_ok=True)
    # Invalid/failed startup pointers are ignored unless a manifest exists.
    atomic_write_text(state / "last-cli-run.json", json.dumps({"path": str(output)}) + "\n")
    print(f"Run {run_id}  node={config['NODE_NAME']}  mode={config['EXECUTION_MODE']}\nOutput: {output}", flush=True)
    if output.parent != Path(config["TELEMETRY_RUNS_ROOT"]):
        print("Warning: output is outside TELEMETRY_RUNS_ROOT; configure collector input to see this run live.", file=sys.stderr)
    # Replace Python with the wrapper: SIGINT/SIGTERM and workload exit code stay unchanged.
    os.execvpe("bash", argv, os.environ | config)


def execute(args) -> int:
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
    if args.action == "config":
        if args.config_action == "show":
            visible = {key: value for key, value in config.items() if key in KEYS}
            print(json.dumps(visible | {"VERL_COMMAND": f"<{len(command)} arguments; redacted>"}, indent=2))
        else:
            if config.get("TELEMETRY_SOURCES_FILE"):
                from .source_discovery import build_file_discovery
                build_file_discovery(json.loads(Path(config["TELEMETRY_SOURCES_FILE"]).read_text()))
            if config.get("DIAGNOSTICS_CONFIG"):
                from .analysis.diagnostics import load_config as load_diagnosis
                load_diagnosis(Path(config["DIAGNOSTICS_CONFIG"]))
            print(f"Valid config: {path}")
        return 0
    if args.action == "doctor":
        result = doctor(config, role=args.role)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for item in result["checks"]:
                print(f"[{item['status']}] {item['component']}" + (f" -> {item['action']}" if item["action"] else ""))
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
        from .show_run import summarize
        print(summarize(run))
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
            result = inspect_threefs(load_diagnosis(Path(config["DIAGNOSTICS_CONFIG"])), seconds=args.window_seconds)
        else:
            result = sources(config)
        if args.json or args.source_action == "threefs":
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print("Source               Status             Scope")
            for row in result["sources"]:
                print(f"{row['telemetry_source']:<20} {row['status']:<18} {row['scope']}")
                print(f"  {row['metrics_url']}")
            if not result["sources"]:
                print("No native sources configured; set TELEMETRY_SOURCES_FILE.")
        return 1 if result.get("backend_error") or any(r["status"] != "up" for r in result.get("sources", [])) else 0
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
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
