#!/usr/bin/env python3
"""Read-only sampled visibility evidence for two application Runs; no workload control."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from xlayer_telemetry.analysis.query_budget import QueryBudget
from xlayer_telemetry.analysis.evidence_quality import result_quality_issues
from xlayer_telemetry.operations.config import load_config
from xlayer_telemetry.measurements import finite_number
from xlayer_telemetry.prometheus import _DeadlinePrometheusClient, escape_label


def verify(client, cluster, run_ids, start, end, step=2):
    if (len(run_ids) != 2 or any(not isinstance(run, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', run) for run in run_ids) or len(set(run_ids)) != 2
            or not isinstance(cluster, str) or not cluster
            or any(finite_number(value) is None for value in (start, end, step))
            or not 0 <= start < end or not 1 <= step <= 300 or not step <= end - start <= 3600):
        raise ValueError('two distinct Run IDs, cluster and a finite 1..3600s window with step 1..300s are required')
    selectors = [f'training_step{{job="telemetry",cluster="{escape_label(cluster)}",run_id="{escape_label(run)}"}}'
                 for run in run_ids]
    observations, issues = [], []
    for run, expression in zip(run_ids, selectors):
        detail = client.query_range_detail(expression, start, end, step)
        issues.extend(result_quality_issues({'query_result': detail.get('result_quality')}))
        rows = detail['series']
        if not rows:
            issues.append(f'{run}:no_application_series')
        for row in rows:
            labels = row.get('labels', {})
            if (labels.get('run_id') != run or labels.get('cluster') != cluster
                    or labels.get('job') != 'telemetry'):
                raise ValueError('backend returned application identity outside the selected scope')
            if any(not isinstance(labels.get(key), str) or not labels[key]
                   for key in ('node', 'producer', 'role', 'worker_id')):
                issues.append(f'{run}:producer_identity_incomplete')
        observations.append({'run_id': run, 'query': expression, 'series': rows,
                             'result_quality': detail.get('result_quality')})
    expression = f'count(count by (run_id) ({selectors[0]} or {selectors[1]}))'
    overlap = client.query_range_detail(expression, start, end, step)
    issues.extend(result_quality_issues({'query_result': overlap.get('result_quality')}))
    stats = overlap.get('aggregate') or {}
    expected_evaluations = math.floor((end - start) / step) + 1
    if stats.get('sample_count', 0) != expected_evaluations:
        issues.append('incomplete_visibility_evaluations')
    visible = (not issues and stats.get('min') == stats.get('max') == 2)
    return {'schema_version': 1, 'status': 'observed_overlap' if visible else 'not_established',
            'cluster': cluster, 'run_ids': list(run_ids),
            'window': {'start': start, 'end': end, 'query_step_seconds': step,
                       'expected_evaluations': expected_evaluations},
            'observations': observations, 'simultaneous_visibility': {'query': expression, 'stats': overlap.get('aggregate')},
            'issues': sorted(set(issues)),
            'scope': 'application series visible at sampled Prometheus query evaluations',
            'limitations': ['Not proof of simultaneous workload progress, complete scrapes, clock alignment or model weights.',
                            'A visible series may be a retained sample; compare Run logger/artifacts and recorded lifecycle separately.',
                            'Native model/engine labels alone do not establish Run ownership.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--run-id', action='append', required=True)
    parser.add_argument('--start', type=float, required=True, help='Unix seconds on the Prometheus reference clock')
    parser.add_argument('--end', type=float, required=True)
    parser.add_argument('--step', type=float, default=2)
    args = parser.parse_args()
    try:
        config, _ = load_config(args.config)
        client = QueryBudget(15).wrap(_DeadlinePrometheusClient(config['PROMETHEUS_URL']),
                                      'prometheus', configurable_timeout=True)
        report = verify(client, config['CLUSTER_NAME'], args.run_id, args.start, args.end, args.step)
    except (OSError, RuntimeError, ValueError) as error:
        report = {'schema_version': 1, 'status': 'query_failed', 'error_type': type(error).__name__,
                  'scope': 'application visibility evidence unavailable'}
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report['status'] == 'observed_overlap' else 1


if __name__ == '__main__':
    raise SystemExit(main())
