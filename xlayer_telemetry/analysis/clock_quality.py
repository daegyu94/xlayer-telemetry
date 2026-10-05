"""Conservative clock checks using existing Node Exporter metrics.

The scrape-relative offset includes transport/collection delay. It is a
screening signal, not an NTP measurement or a timestamp correction.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable

from ..prometheus import escape_label
from ..time_alignment import validate_alignment


def assess_clocks(
    query: Callable[[str, float, float, float], dict | None],
    *, cluster: str, nodes: Iterable[str], start: float, end: float,
    max_skew_seconds: float = 1.0, max_sample_age_seconds: float = 30.0,
    require_sync: bool = True,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "reference": "prometheus_scrape_clock", "max_skew_seconds": max_skew_seconds,
        "max_sample_age_seconds": max_sample_age_seconds,
        "require_sync": require_sync, "nodes": {},
    }
    for node in sorted(set(nodes)):
        selector = f'job="telemetry",cluster="{escape_label(cluster)}",instance="{escape_label(node)}"'
        wall = f"node_time_seconds{{{selector}}}"
        expressions = {
            "offset_seconds": f"{wall} - timestamp({wall})",
            "sample_age_seconds": f"time() - timestamp({wall})",
            "sync_status": f"node_timex_sync_status{{{selector}}}",
        }
        entry: dict[str, Any] = {"status": "aligned", "issues": []}
        for key, expression in expressions.items():
            try:
                stats = query(expression, start, end, max(1, min(5, (end - start) / 20)))
                entry[key] = stats
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                entry[key] = None
                entry["issues"].append(f"{key}:{type(exc).__name__}")
        def value(key: str, field: str) -> float | None:
            raw = (entry.get(key) or {}).get(field)
            return float(raw) if type(raw) in (int, float) and math.isfinite(raw) else None
        low, high = value("offset_seconds", "min"), value("offset_seconds", "max")
        age = value("sample_age_seconds", "max")
        sync = value("sync_status", "min")
        if low is None or high is None or age is None or (require_sync and sync is None):
            entry["status"] = "unknown"
            entry["issues"].append("missing_clock_evidence")
        if low is not None and high is not None and max(abs(low), abs(high)) > max_skew_seconds:
            entry["status"] = "unsafe"
            entry["issues"].append("scrape_relative_clock_offset")
        if age is not None and (age > max_sample_age_seconds or age < 0):
            entry["status"] = "unsafe"
            entry["issues"].append("stale_or_invalid_clock_sample")
        if require_sync and sync is not None and sync < 1:
            entry["status"] = "unsafe"
            entry["issues"].append("clock_unsynchronized")
        result["nodes"][node] = entry
    result["status"] = (
        "unsafe" if any(item["status"] == "unsafe" for item in result["nodes"].values())
        else "unknown" if not result["nodes"] or any(item["status"] == "unknown" for item in result["nodes"].values())
        else "aligned"
    )
    return result


def assess_interval(query, *, cluster: str, nodes: Iterable[str], window: dict,
                    producer_node: str, config: dict, producer_clock_nodes: Iterable[str] = ()) -> dict:
    """A mapped workload window can align with monitoring-host scrape time.

    This does not repair producer-timestamped sources such as 3FS DateTime.
    Retain kernel screening separately so OS clock problems remain visible.
    """
    screening = assess_clocks(query, cluster=cluster, nodes=nodes,
                              start=window['start'], end=window['end'],
                              max_skew_seconds=config.get('max_skew_seconds', 1),
                              max_sample_age_seconds=config.get('max_sample_age_seconds', 30),
                              require_sync=config.get('require_sync', True))
    if 'time_alignment' not in window and not config.get('calibration_reference'):
        return screening
    limit = min(float(config.get('max_skew_seconds', 1)), (window['end']-window['start'])/10)
    result = validate_alignment(window, config.get('calibration_reference'), max_uncertainty=limit)
    alignment = window.get('time_alignment', {})
    if not isinstance(alignment, dict) or alignment.get('node') != producer_node:
        result = {'status': 'unknown', 'issue': 'calibration_node_mismatch'}
    result.update(nodes={producer_node: {'status': result['status']}},
                  system_clock_screening=screening, scope='mapped_workload_to_prometheus_scrape_time')
    if producer_clock_nodes:
        extra = assess_clocks(query, cluster=cluster, nodes=producer_clock_nodes,
                              start=window['start'], end=window['end'],
                              max_skew_seconds=config.get('max_skew_seconds', 1),
                              max_sample_age_seconds=config.get('max_sample_age_seconds', 30),
                              require_sync=config.get('require_sync', True))
        result['producer_clock_screening'] = extra
        result['nodes'].update(extra['nodes'])
        if extra['status'] != 'aligned':
            result['status'] = extra['status']
    return result


def main() -> None:
    import argparse
    import json
    import time
    from ..prometheus import _DeadlinePrometheusClient as PrometheusClient

    parser = argparse.ArgumentParser(description="Check node clocks against Prometheus scrape time")
    parser.add_argument("--prometheus-url", required=True)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--node", action="append", required=True)
    parser.add_argument("--lookback-seconds", type=float, default=60)
    parser.add_argument("--max-skew-seconds", type=float, default=1)
    parser.add_argument("--allow-unsynchronized", action="store_true", help="Inspect offsets without requiring kernel sync status")
    args = parser.parse_args()
    if not math.isfinite(args.lookback_seconds) or args.lookback_seconds <= 0 or not math.isfinite(args.max_skew_seconds) or args.max_skew_seconds <= 0:
        parser.error("lookback and max skew must be finite and positive")
    end = time.time()
    report = assess_clocks(PrometheusClient(args.prometheus_url).query_range,
                          cluster=args.cluster, nodes=args.node,
                          start=end - args.lookback_seconds, end=end,
                          max_skew_seconds=args.max_skew_seconds,
                          require_sync=not args.allow_unsynchronized)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["status"] == "aligned" else 1)


if __name__ == "__main__":
    main()
