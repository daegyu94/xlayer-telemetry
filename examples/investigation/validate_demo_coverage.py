"""Check every provisioned dashboard query against an existing synthetic stack.

This reads Prometheus/Loki and writes a new coverage report. It does not start
services, fabricate backend responses, or change dashboard queries.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import urlencode
from urllib.request import urlopen


def panels(items):
    for panel in items:
        yield panel
        yield from panels(panel.get("panels", []))


def render_query(expr, *, cluster, run_id, observer_node):
    values = dict.fromkeys(("node", "gpu", "engine", "sandbox_node", "device", "mount", "storage_system",
                           "storage_node", "ssd", "phase", "role", "worker", "trace_id", "record_id",
                           "diagnosis_method", "workload"), ".*")
    literal = lambda value: json.dumps(re.escape(value), ensure_ascii=False)[1:-1]
    values.update(cluster=literal(cluster), run_id=literal(run_id), source_node=literal(observer_node),
                  log_run_id=literal(run_id), telemetry_run_id=literal(run_id), training_max_age="300",
                  __rate_interval="1m", __interval="2s")

    def substitute(match):
        name = match[1] or match[2]
        if name not in values:
            raise ValueError("Unknown dashboard query variable: " + name)
        return values[name]
    return re.sub(r"\$\{(\w+)(?::\w+)?\}|\$(\w+)", substitute, expr)


def get(url, path, params):
    with urlopen(url.rstrip("/") + path + "?" + urlencode(params), timeout=10) as response:
        result = json.load(response)
    if result.get("status") != "success":
        raise ValueError("Backend query did not succeed: " + str(result))
    return result["data"]["result"]


def validate(args):
    if args.lookback_seconds <= 0:
        raise ValueError("lookback-seconds must be positive")
    now = time.time()
    checks = []
    for path in sorted(args.dashboard_dir.glob("*.json")):
        dashboard = json.loads(path.read_text())
        for panel in panels(dashboard["panels"]):
            for target in panel.get("targets", []):
                datasource = target.get("datasource", panel.get("datasource", {})).get("uid")
                if target.get("hide"):
                    continue
                check = {"dashboard": dashboard["uid"], "panel": panel["title"],
                         "ref_id": target["refId"], "datasource": datasource}
                try:
                    expr = render_query(target["expr"], cluster=args.cluster, run_id=args.run_id,
                                        observer_node=args.observer_node)
                    check["query"] = expr
                    if datasource == "telemetry-prometheus":
                        result = get(args.prometheus_url, "/api/v1/query", {"query": expr, "time": now})
                        covered = bool(result) and all(math.isfinite(float(item["value"][1])) for item in result)
                    elif datasource == "telemetry-loki":
                        if not args.loki_url:
                            check.update(status="not_validated", reason="Loki not configured")
                            checks.append(check)
                            continue
                        result = get(args.loki_url, "/loki/api/v1/query_range",
                                     {"query": expr, "start": int((now - args.lookback_seconds) * 1e9), "end": int(now * 1e9), "limit": 100})
                        covered = any(item.get("values") for item in result)
                    else:
                        raise ValueError("Unknown datasource: " + str(datasource))
                    check.update(status="covered" if covered else "empty_or_nonfinite", series=len(result))
                except (OSError, ValueError, KeyError, TypeError) as error:
                    check.update(status="error", error=str(error))
                checks.append(check)
    if not checks:
        raise ValueError("No dashboard queries found in " + str(args.dashboard_dir))
    report = {"data_origin": "synthetic", "cluster": args.cluster, "run_id": args.run_id,
              "all_queries_covered": all(c["status"] == "covered" for c in checks),
              "log_lookback_seconds": args.lookback_seconds, "query_count": len(checks), "covered": sum(c["status"] == "covered" for c in checks),
              "checks": checks,
              "limitations": ["Nonempty query results do not validate real runtime instrumentation or causality.",
                              "Checks use All resource filters and one selected run/observer; arbitrary selections may correctly be empty.",
                              "Classic histogram bucket counters are illustrative; no real vLLM/Ray/3FS is started."]}
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(f"Covered {report['covered']}/{len(checks)} dashboard queries; report: {args.output}")
    return 0 if report["all_queries_covered"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard-dir", required=True, type=Path, help="Provisioned dashboard JSON directory")
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:19090")
    parser.add_argument("--loki-url", help="Optional; omitted Loki queries are explicitly not validated")
    parser.add_argument("--cluster", default="demo-b300")
    parser.add_argument("--run-id", default="verl-agent-demo")
    parser.add_argument("--observer-node", default="gpu-node-0")
    parser.add_argument("--lookback-seconds", type=int, default=3600, help="Loki window, default the investigation journey last hour")
    parser.add_argument("--output", required=True, type=Path, help="New JSON report file")
    raise SystemExit(validate(parser.parse_args()))


if __name__ == "__main__":
    main()
