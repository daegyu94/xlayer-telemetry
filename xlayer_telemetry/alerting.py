"""Generate opt-in Grafana alert rules for the monitoring server."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


_MOUNTPOINT = re.compile(r"^/[A-Za-z0-9_./-]*$")
_DATASOURCE_UID = "telemetry-prometheus"
_EXPRESSION_UID = "__expr__"
_RULE_UIDS = (
    "xlayer-node-collector-down",
    "xlayer-gpu-sample-stale",
    "xlayer-filesystem-low-space",
)


def _rule(
    uid: str,
    title: str,
    query: str,
    pending: str,
    severity: str,
    summary: str,
    dashboard_uid: str,
    panel_id: int,
) -> dict:
    return {
        "uid": uid,
        "title": title,
        "condition": "C",
        "data": [
            {
                "refId": "A",
                "datasourceUid": _DATASOURCE_UID,
                "relativeTimeRange": {"from": 300, "to": 0},
                "model": {
                    "datasource": {"type": "prometheus", "uid": _DATASOURCE_UID},
                    "editorMode": "code",
                    "expr": query,
                    "instant": True,
                    "range": False,
                    "intervalMs": 1000,
                    "maxDataPoints": 43200,
                    "refId": "A",
                },
            },
            {
                "refId": "B",
                "datasourceUid": _EXPRESSION_UID,
                "relativeTimeRange": {"from": 0, "to": 0},
                "model": {
                    "datasource": {"type": _EXPRESSION_UID, "uid": _EXPRESSION_UID},
                    "expression": "A",
                    "type": "reduce",
                    "reducer": "last",
                    "settings": {"mode": "dropNN"},
                    "intervalMs": 1000,
                    "maxDataPoints": 43200,
                    "refId": "B",
                },
            },
            {
                "refId": "C",
                "datasourceUid": _EXPRESSION_UID,
                "relativeTimeRange": {"from": 0, "to": 0},
                "model": {
                    "datasource": {"type": _EXPRESSION_UID, "uid": _EXPRESSION_UID},
                    "expression": "B",
                    "type": "threshold",
                    "conditions": [{
                        "evaluator": {"params": [0.5], "type": "gt"},
                        "operator": {"type": "and"},
                        "query": {"params": ["C"]},
                        "reducer": {"params": [], "type": "last"},
                        "type": "query",
                    }],
                    "intervalMs": 1000,
                    "maxDataPoints": 43200,
                    "refId": "C",
                },
            },
        ],
        "dashboardUid": dashboard_uid,
        "panelId": panel_id,
        "noDataState": "OK",
        "execErrState": "Error",
        "for": pending,
        "annotations": {"summary": summary},
        "labels": {"service": "xlayer-telemetry", "severity": severity},
        "isPaused": False,
    }


def build_rules(mountpoint: str = "/", free_percent: int = 10) -> dict:
    """Return Grafana provisioning data for one monitoring server."""
    if not _MOUNTPOINT.fullmatch(mountpoint) or ".." in mountpoint.split("/"):
        raise ValueError("ALERT_MOUNTPOINT must be an absolute path using letters, digits, _, ., /, or -")
    if not 1 <= free_percent <= 99:
        raise ValueError("ALERT_FREE_PERCENT must be between 1 and 99")

    target_down = 'up{job="telemetry"} == bool 0'
    gpu_sample = 'telemetry_gpu_sample_timestamp_seconds{job="telemetry"}'
    gpu_stale = (
        f'((time() - {gpu_sample} > bool 60) or on(cluster, instance) '
        f'(up{{job="telemetry"}} == 1 unless on(cluster, instance) {gpu_sample})) '
        'and on(cluster, instance) (up{job="telemetry"} == 1)'
    )
    filesystem = f'{{job="telemetry",mountpoint="{mountpoint}",fstype!~"tmpfs|overlay"}}'
    free_fraction = (
        f'(node_filesystem_avail_bytes{filesystem} / '
        f'node_filesystem_size_bytes{filesystem}) < bool {free_percent / 100:g}'
    )
    return {
        "apiVersion": 1,
        "groups": [{
            "orgId": 1,
            "name": "xlayer-telemetry-operations",
            "folder": "XLayer Telemetry",
            "interval": "30s",
            "rules": [
                _rule(
                    "xlayer-node-collector-down", "Node collector unavailable",
                    target_down, "1m", "critical",
                    "Telemetry collector is unreachable on {{ $labels.cluster }}/{{ $labels.instance }}.",
                    "telemetry-overview", 1,
                ),
                _rule(
                    "xlayer-gpu-sample-stale", "GPU samples missing or stale",
                    gpu_stale, "1m", "warning",
                    "GPU samples are missing or over 60s old on {{ $labels.cluster }}/{{ $labels.instance }}.",
                    "telemetry-overview", 4,
                ),
                _rule(
                    "xlayer-filesystem-low-space", "Filesystem free space low",
                    free_fraction, "5m", "warning",
                    f"Free space is below {free_percent}% on {mountpoint} at "
                    "{{ $labels.cluster }}/{{ $labels.instance }}.",
                    "xlayer-data-storage", 5,
                ),
            ],
        }],
    }


def build_delete_rules() -> dict:
    """Remove the generated rules when alerting is turned off."""
    return {
        "apiVersion": 1,
        "deleteRules": [{"orgId": 1, "uid": uid} for uid in _RULE_UIDS],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mountpoint", default="/")
    parser.add_argument("--free-percent", type=int, default=10)
    parser.add_argument("--disabled", action="store_true")
    args = parser.parse_args()
    try:
        payload = build_delete_rules() if args.disabled else build_rules(args.mountpoint, args.free_percent)
    except ValueError as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
