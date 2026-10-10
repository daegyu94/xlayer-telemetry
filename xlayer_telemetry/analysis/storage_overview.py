"""Reuse collected storage evidence; source availability is not backend identity."""
from __future__ import annotations

from .evidence_quality import RESOLUTION_BLOCKERS, correlation_quality_issues
from .metric_queries import METRIC_PROFILES, PROFILE_SIGNALS


def storage_overview(comparison, queries, missing, *, unsafe_clock, threefs_configured, threefs_rows, profiles=(), sampling_quality=None):
    rows = {row['signal']: row for row in comparison.get('signals', [])}
    signals = []
    configured = {name for profile in profiles for name in METRIC_PROFILES.get(profile, {})}
    for name, spec in PROFILE_SIGNALS.items():
        if not name.startswith('mooncake_') or name not in configured and name not in queries:
            continue
        row = rows.get(name, {})
        qualities = row.get('sampling_quality') or (sampling_quality or {}).get(name, {})
        current_quality = qualities.get('current', {})
        issues = sorted(set(correlation_quality_issues(current_quality)))
        status = 'observed'
        if name not in queries:
            status = 'not_configured'
        elif row.get('current') is None and 'current' not in qualities and any(item.startswith('prometheus:' + name + ':') and
                ('Error' in item or 'budget_exhausted' in item) and ':baseline_' not in item for item in missing):
            status = 'query_failed'
        elif row.get('current') is None:
            status = 'no_data'
        elif 'source_sample_before_interval' in issues:
            status = 'stale'
        elif 'source_timestamp_in_future' in issues:
            status = 'timestamp_invalid'
        elif unsafe_clock:
            status = 'clock_unverified'
        elif RESOLUTION_BLOCKERS.intersection(issues):
            status = 'insufficient_sampling'
        baseline_status = ('observed' if row.get('baseline') is not None else
            'no_data' if f'prometheus:{name}:baseline_no_data' in missing else
            'incomparable' if f'prometheus:{name}:baseline_entity_match' in missing else
            'query_failed' if 'current' in qualities and any(item.startswith(f'prometheus:{name}:') and
                ('Error' in item or 'budget_exhausted' in item) for item in missing) else 'unavailable')
        signals.append({'baseline_status': baseline_status, 'signal': name, 'current': row.get('current'), 'baseline': row.get('baseline'),
            'delta_percent': row.get('delta_percent') if status == 'observed' else None,
            'comparison_status': row.get('comparison_status', 'not_reported'),
            'scope': row.get('scope', spec.scope), 'unit': spec.unit, 'statistic': spec.statistic,
            'entity': row.get('labels', {}), 'status': status, 'quality_issues': issues,
            'layer': 'connector' if 'connector' in name else 'master_memory' if 'master_' in name else 'dfs_client'})
    threefs_failed = any(item.startswith('threefs:') and ('Error' in item or 'budget_exhausted' in item) for item in missing)
    return {'backend': {'status': 'not_reported', 'adapter': None},
        'operation_attribution': 'not_established', 'signals': signals,
        'threefs': {'status': 'not_configured' if not threefs_configured else 'observed' if threefs_rows else 'query_failed' if threefs_failed else 'no_data',
                    'scope': 'shared-service', 'relationship_to_mooncake': 'not_established',
                    'capabilities': ['reported_distribution', 'reset_reports', 'collection_series']}}
