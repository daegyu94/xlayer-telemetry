"""Conservative clock checks using existing Node Exporter metrics.

The scrape-relative offset includes transport/collection delay. It is a
screening signal, not an NTP measurement or a timestamp correction.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable

from ..prometheus import escape_label
from ..time_alignment import validate_alignment


def producer_hosts_verified(clock: dict, hosts: Iterable[str], aliases: dict | None = None) -> bool:
    """Raw producer timestamps require coverage of actual returned hosts."""
    hosts=set(hosts)
    nodes=(clock.get('system_clock_screening',{}).get('nodes',{})
           if clock.get('scope')=='mapped_workload_to_prometheus_scrape_time' else clock.get('nodes',{}))
    nodes={**nodes,**clock.get('producer_clock_screening',{}).get('nodes',{})}
    return bool(hosts) and clock.get('status')=='aligned' and all(
        isinstance(host,str) and bool(host) and
        nodes.get((aliases or {}).get(host,host),{}).get('status')=='aligned' for host in hosts)


def clock_inventory(config: dict, producer_node: str) -> dict:
    """Configured observation hosts, not discovered execution dependencies."""
    roles: dict[str,list[str]] = {}
    def add(node, role):
        if isinstance(node,str) and node:
            roles.setdefault(node, []).append(role)
    add(producer_node, 'trainer/observer')
    for key, role in (('compute_node','compute'), ('rollout_node','rollout/vLLM/Ray/client'),
                      ('storage_node','storage context')):
        add(config.get(key) or producer_node, role)
    for replica in config.get('rollout_replicas', []):
        for node in replica['nodes']:
            add(node, 'declared rollout replica:' + replica['id'])
    sandbox = config.get('sandbox') or {}
    if sandbox.get('enabled'):
        add(sandbox.get('node') or producer_node, 'sandbox')
    threefs = config.get('threefs') or {}
    for node in threefs.get('clock_nodes', []):
        add(node, '3FS producer')
    for node in threefs.get('time_series', {}).get('host_clock_nodes', {}).values():
        add(node, '3FS collection host mapping')
    prom, clock = config.get('prometheus', {}), config.get('clock', {})
    if 'mooncake_storage' in prom.get('metric_profiles', []):
        add(prom.get('mooncake_master_node'), 'Mooncake master')
    for node in clock.get('nodes', []):
        add(node, 'explicit observation host')
    add(clock.get('monitoring_node'), 'monitoring/Prometheus')
    if len(roles) > 32:
        raise ValueError('clock inventory exceeds 32 nodes')
    cross_node = len(roles) > 1
    issues = []
    if cross_node and not clock.get('monitoring_node'):
        issues.append('clock:monitoring_node:not_configured')
    if cross_node and not config.get('cluster'):
        issues.append('clock:cluster:not_configured')
    return {'nodes':dict(sorted(roles.items())), 'operating_scope':'cross-node' if cross_node else 'single-resource-node',
            'issues':issues, 'kind':'configured_observation_inventory_not_dependency_map'}


def assess_clocks(
    query: Callable[[str, float, float, float], dict | None],
    *, cluster: str, nodes: Iterable[str], start: float, end: float,
    max_skew_seconds: float = 1.0, max_sample_age_seconds: float = 30.0,
    require_sync: bool = True, max_uncertainty_seconds: float | None = None,
) -> dict[str, Any]:
    nodes = sorted(set(nodes))
    if (not nodes or len(nodes) > 32 or any(not isinstance(node, str) or not node for node in nodes)
            or not all(type(v) in (int,float) and math.isfinite(v) for v in (start,end,max_skew_seconds,max_sample_age_seconds))
            or start >= end or max_skew_seconds <= 0 or max_sample_age_seconds <= 0):
        raise ValueError("clock screening needs bounded node identities and a finite increasing window")
    limit = min(max_skew_seconds, (end-start)/10) if max_uncertainty_seconds is None else max_uncertainty_seconds
    if type(limit) not in (int,float) or not math.isfinite(limit) or limit <= 0:
        raise ValueError("clock uncertainty limit must be finite and positive")
    result: dict[str, Any] = {
        "reference": "prometheus_scrape_clock", "max_skew_seconds": max_skew_seconds,
        "max_sample_age_seconds": max_sample_age_seconds,
        "max_uncertainty_seconds": limit, "require_sync": require_sync, "nodes": {},
        "coverage": "sampled_screening_not_continuous_clock_proof",
    }
    for node in nodes:
        selector = f'job="telemetry",cluster="{escape_label(cluster)}",instance="{escape_label(node)}"'
        wall = f"node_time_seconds{{{selector}}}"
        expressions = {
            "offset_seconds": f"{wall} - timestamp({wall})",
            "sample_age_seconds": f"time() - timestamp({wall})",
            "sync_status": f"node_timex_sync_status{{{selector}}}",
            "ntp_offset_seconds": f"node_timex_offset_seconds{{{selector}}}",
            "maxerror_seconds": f"node_timex_maxerror_seconds{{{selector}}}",
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
        ntp_low, ntp_high = value("ntp_offset_seconds", "min"), value("ntp_offset_seconds", "max")
        error = value("maxerror_seconds", "max")
        entry["uncertainty_seconds"] = error if error is not None and error >= 0 else None
        entry["uncertainty_kind"] = "kernel_reported_maxerror_not_UTC_confidence"
        if low is None or high is None or age is None or ntp_low is None or ntp_high is None or error is None or error < 0 or (require_sync and sync is None):
            entry["status"] = "unknown"
            entry["issues"].append("missing_clock_evidence")
        if low is not None and high is not None and max(abs(low), abs(high)) > max_skew_seconds:
            entry["status"] = "unsafe"
            entry["issues"].append("scrape_relative_clock_offset")
        if age is not None and (age > max_sample_age_seconds or age < 0):
            entry["status"] = "unsafe"
            entry["issues"].append("stale_or_invalid_clock_sample")
        if age is not None and age > end-start:
            entry['status']='unsafe'
            entry['issues'].append('clock_sample_older_than_interval')
        if require_sync and sync is not None and sync < 1:
            entry["status"] = "unsafe"
            entry["issues"].append("clock_unsynchronized")
        if any(value(key, "sample_count") is None or value(key,"sample_count") < 2
               or value(key,"series_count") not in (None,1) for key in expressions if key != "sync_status" or require_sync):
            if entry["status"] == "aligned":
                entry["status"] = "unknown"
            entry["issues"].append("insufficient_or_ambiguous_clock_evaluations")
        # Production range summaries preserve each scrape source and parser
        # limitations. A sum of evaluations is not proof for one clock source.
        required = [entry.get(key) or {} for key in expressions if key != 'sync_status' or require_sync]
        identities = [stats.get('series_identities') for stats in required]
        identity_known = [value for value in identities if value is not None]
        partial = any(any((stats.get('query_result') or {}).get(field, 0) for field in
                      ('warning_count', 'info_count', 'discarded_sample_count', 'discarded_series_count'))
                      for stats in required)
        ambiguous = any(stats.get('min_series_sample_count', 2) < 2 for stats in required)
        inconsistent = bool(identity_known) and (len(identity_known) != len(required) or
            any(len(value) != 1 for value in identity_known) or
            any(value != identity_known[0] for value in identity_known) or
            any(any(identity.get(key) != expected for key, expected in
                    (('job', 'telemetry'), ('cluster', cluster), ('instance', node)))
                for value in identity_known for identity in value))
        if partial or ambiguous or inconsistent:
            if entry['status'] == 'aligned':
                entry['status'] = 'unknown'
            if partial:
                entry['issues'].append('partial_clock_query_response')
            if ambiguous or inconsistent:
                entry['issues'].append('clock_source_identity_unverified')
        entry['source_identity_quality'] = 'reported' if identity_known else 'not_reported'
        if error is not None and error >= 0 and error > limit:
            entry["status"] = "unsafe"
            entry["issues"].append("clock_uncertainty_exceeds_interval_budget")
        if ntp_low is not None and ntp_high is not None and max(abs(ntp_low),abs(ntp_high)) > limit:
            entry["status"] = "unsafe"
            entry["issues"].append("ntp_offset_exceeds_interval_budget")
        if low is not None and high is not None and high-low > limit:
            entry["status"] = "unsafe"
            entry["issues"].append("clock_offset_variation")
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
    producer_clock_nodes=tuple(producer_clock_nodes)
    nodes = sorted(set(nodes) | set(producer_clock_nodes) | ({producer_node} if producer_node else set()) |
                   ({config['monitoring_node']} if config.get('monitoring_node') else set()))
    screening = assess_clocks(query, cluster=cluster, nodes=nodes,
                              start=window['start'], end=window['end'],
                              max_skew_seconds=config.get('max_skew_seconds', 1),
                              max_sample_age_seconds=config.get('max_sample_age_seconds', 30),
                              max_uncertainty_seconds=min(config.get('max_uncertainty_seconds',config.get('max_skew_seconds',1)),(window['end']-window['start'])/10),
                              require_sync=len(nodes)>1 or config.get('require_sync', True))
    if 'time_alignment' not in window and not config.get('calibration_reference'):
        if len(nodes)>1 and not config.get('monitoring_node'):
            if screening['status']!='unsafe':
                screening['status']='unknown'
            screening['issue']='monitoring_clock_identity_unconfigured'
        return screening
    limit = min(float(config.get('max_skew_seconds', 1)), (window['end']-window['start'])/10)
    result = validate_alignment(window, config.get('calibration_reference'), max_uncertainty=limit)
    alignment = window.get('time_alignment', {})
    if not isinstance(alignment, dict) or alignment.get('node') != producer_node:
        result = {'status': 'unknown', 'issue': 'calibration_node_mismatch'}
    result.update(nodes={producer_node: {'status': result['status']}},
                  system_clock_screening=screening, scope='mapped_workload_to_prometheus_scrape_time')
    if producer_clock_nodes:
        extra = {**screening, 'nodes':{node:screening['nodes'][node] for node in producer_clock_nodes}}
        extra['status'] = ('unsafe' if any(v['status']=='unsafe' for v in extra['nodes'].values())
                           else 'unknown' if any(v['status']=='unknown' for v in extra['nodes'].values()) else 'aligned')
        result['producer_clock_screening'] = extra
        result['nodes'].update(extra['nodes'])
        if extra['status'] != 'aligned':
            result['status'] = extra['status']
    if len(nodes) > 1:
        # Mapping an application clock does not certify remote OS clocks.
        result['nodes'].update(screening['nodes'])
        if screening['status'] != 'aligned':
            result['status'] = screening['status']
        if not config.get('monitoring_node'):
            result['status'] = 'unknown' if result['status'] != 'unsafe' else 'unsafe'
            result['issue'] = 'monitoring_clock_identity_unconfigured'
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
