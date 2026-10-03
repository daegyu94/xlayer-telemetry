"""Inspect registered subsystems without a training run or diagnosis execution."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import urlopen

from .prometheus import escape_label
from .source_discovery import build_file_discovery


def explore_url(base: str, selector: str) -> str:
    pane = {"A": {"datasource": "telemetry-prometheus", "queries": [{
        "refId": "A", "expr": selector, "instant": True, "range": False,
        "datasource": {"uid": "telemetry-prometheus", "type": "prometheus"},
    }], "range": {"from": "now-15m", "to": "now"}}}
    return base.rstrip('/') + '/explore?' + urlencode({
        'schemaVersion': 1, 'panes': json.dumps(pane, separators=(',', ':'))})


def parse_target_response(payload: dict) -> list[dict]:
    """Reject unavailable/malformed discovery instead of reporting missing targets."""
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise ValueError("unsuccessful target response")
    data = payload.get("data")
    targets = data.get("activeTargets") if isinstance(data, dict) else None
    if not isinstance(targets, list) or any(
        not isinstance(t, dict) or not isinstance(t.get("labels"), dict)
        or any(not isinstance(k, str) or not isinstance(v, str) for k, v in t["labels"].items())
        for t in targets
    ):
        raise ValueError("invalid activeTargets")
    return targets


def inspect_sources(groups: list[dict], prometheus: str, grafana: str, cluster: str,
                    *, timeout: float = 5) -> dict:
    """Fetch once; the same pure summary is used by aggregate CLI health."""
    targets, error = [], None
    try:
        with urlopen(prometheus.rstrip('/') + '/api/v1/targets?state=active', timeout=timeout) as response:
            body = response.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise ValueError("target response exceeds limit")
        targets = parse_target_response(json.loads(body))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Backend error strings may contain URLs or credentials.
        error = type(exc).__name__
    return summarize_sources(groups, targets, grafana, cluster, backend_error=error)


def summarize_sources(groups: list[dict], targets: list[dict], grafana: str,
                      cluster: str, *, backend_error: str | None = None) -> dict:
    """Match stable source identities against one discovery snapshot, without I/O."""
    keys = ("job", "cluster", "telemetry_source", "component", "instance")
    index = {}
    for target in targets:
        labels = target.get("labels", {})
        index.setdefault(tuple(labels.get(key) for key in keys), []).append(target)
    rows = []
    for group in groups:
        labels = group['labels']
        # Server relabeling always assigns CLUSTER_NAME, overriding file labels.
        identity = {'job': labels.get('job', 'native'), 'cluster': cluster,
                    'telemetry_source': labels['telemetry_source'],
                    'component': labels['component'], 'instance': labels.get('instance', group['targets'][0])}
        matches = index.get(tuple(identity[key] for key in keys), [])
        status = ('unavailable' if backend_error else 'not_discovered' if not matches else
                  'up' if all(t.get('health') == 'up' for t in matches) else
                  'down' if any(t.get('health') == 'down' for t in matches) else 'unknown')
        selector = '{' + ','.join(f'{k}="{escape_label(v)}"' for k, v in identity.items()) + '}'
        rows.append({**identity, 'status': status, 'scope': 'endpoint/shared-service',
                     'last_scrape': [t.get('lastScrape') for t in matches],
                     'metrics_url': explore_url(grafana, selector)})
    return {'backend_error': backend_error, 'sources': rows}


def inspect_threefs(config: dict, *, seconds: float = 300, now: float | None = None) -> dict:
    if not math.isfinite(seconds) or not 0 < seconds <= 86400:
        raise ValueError('window must be between 0 and 86400 seconds')
    from .analysis.diagnostics import ThreeFSClient
    settings = config.get('threefs')
    if not settings:
        return {'status': 'not_configured'}
    queried_at = time.time() if now is None else now
    settle = settings.get('settle_seconds', 30)
    end = queried_at - settle
    client = ThreeFSClient(settings['url'], database=settings.get('database', '3fs'),
                          filters=settings.get('filters'),
                          timeout=float(settings.get('timeout_seconds', 5)),
                          user_env=settings.get('user_env', 'THREEFS_CLICKHOUSE_USER'),
                          password_env=settings.get('password_env', 'THREEFS_CLICKHOUSE_PASSWORD'))
    rows = client.query_window(end - seconds, end)
    counters, missing = [], []
    try:
        counters = client.query_counters(end - seconds, end)
        counter_status = 'observed' if counters else 'no_data'
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        # Optional table/schema failures must not discard available latency
        # evidence. Never return backend error text containing credentials/URLs.
        counter_status = 'unavailable'
        missing.append('threefs:counters:' + type(exc).__name__)

    def freshness(items):
        times = [row['last_observed_at'] for row in items if row.get('last_observed_at') is not None]
        latest = max(times) if times else None
        return {'last_observed_at': latest,
                'source_age_seconds': queried_at - latest if latest is not None else None}

    return {'status': 'observed' if rows or counters else 'no_data', 'scope': 'shared-service',
            'start': end - seconds, 'end': end, 'queried_at': queried_at, 'settle_seconds': settle,
            'filters': settings.get('filters', {}),
            'note': 'max_observed_p99 is not global p99; units are producer-defined.',
            'metrics': rows, 'counters': counters, 'counter_status': counter_status,
            'counter_note': 'Raw sampled values with producer-defined units and reset/gauge semantics; no rate, delta or operation total is inferred. Equal-second samples have no finer last-value ordering.',
            'freshness': {'distributions': freshness(rows), 'counters': freshness(counters)},
            'missing_sources': missing}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path)
    parser.add_argument('--diagnostics-config', type=Path)
    parser.add_argument('--prometheus', default='http://127.0.0.1:19090')
    parser.add_argument('--grafana', default='http://127.0.0.1:13000')
    parser.add_argument('--cluster', default='training-cluster')
    parser.add_argument('--threefs', action='store_true', help='Query configured 3FS distributions and raw counters')
    parser.add_argument('--window-seconds', type=float, default=300)
    args = parser.parse_args()
    try:
        if args.threefs:
            if not args.diagnostics_config:
                parser.error('--threefs requires --diagnostics-config')
            from .analysis.diagnostics import load_config
            result = inspect_threefs(load_config(args.diagnostics_config), seconds=args.window_seconds)
        else:
            groups = build_file_discovery(json.loads(args.sources.read_text())) if args.sources else []
            result = inspect_sources(groups, args.prometheus, args.grafana, args.cluster) if groups else {
                'sources': [], 'status': 'not_configured', 'next_action': 'Set TELEMETRY_SOURCES_FILE.'}
            result['note'] = 'Up means scrape success, not workload health. Native sources are not run-scoped.'
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get('backend_error'):
            raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f'Subsystem inspection failed ({type(exc).__name__}); check source/config and backend connectivity.\n')


if __name__ == '__main__':
    main()
