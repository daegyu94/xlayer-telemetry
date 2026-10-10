"""Bounded application-duration uncertainty, without backend queries or causes."""

from __future__ import annotations

import math
import statistics
from itertools import islice

from ..measurements import finite_number


def settings(policy):
    config = policy.get('robust', {})
    if not isinstance(config, dict) or set(config) - {'enabled', 'cohort_size', 'minimum_cohort', 'z_threshold', 'relative_floor'}:
        raise ValueError('baseline.robust has unsupported settings')
    result = {'enabled': False, 'cohort_size': 31, 'minimum_cohort': 5,
              'z_threshold': 3.0, 'relative_floor': .05, **config}
    if type(result['enabled']) is not bool:
        raise ValueError('baseline.robust.enabled must be boolean')
    if any(type(result[key]) is not int or not 5 <= result[key] <= 31 for key in ('cohort_size', 'minimum_cohort')):
        raise ValueError('baseline.robust cohort limits must be integers between 5 and 31')
    if result['minimum_cohort'] > result['cohort_size']:
        raise ValueError('minimum_cohort exceeds cohort_size')
    if finite_number(result['z_threshold']) is None or not 1 <= result['z_threshold'] <= 10:
        raise ValueError('baseline.robust.z_threshold must be between 1 and 10')
    if finite_number(result['relative_floor']) is None or not .001 <= result['relative_floor'] <= .5:
        raise ValueError('baseline.robust.relative_floor must be between .001 and .5')
    return result


def assess(current, values, *, minimum_cohort=5, z_threshold=3., relative_floor=.05):
    """MAD is a dispersion estimate, not a probability that a cause is correct.

    The order-statistic interval covers the population median under independent,
    identically distributed observations. Time-correlated steps may violate this
    assumption; it is explicitly conditional, never a calibrated diagnosis score.
    """
    settings({'robust': {'minimum_cohort': minimum_cohort, 'z_threshold': z_threshold, 'relative_floor': relative_floor}})
    scanned = list(islice(values, 32))
    if len(scanned) > 31:
        raise ValueError('robust summary exceeds 31 observations')
    values = sorted(float(value) for value in scanned if finite_number(value) is not None and value >= 0)
    n = len(values)
    median = statistics.median(values) if values else None
    mad = statistics.median(abs(value - median) for value in values) if values else None
    scale = max(1.4826 * mad, relative_floor * abs(median), 1e-9) if values else None
    score = (current - median) / scale if finite_number(current) is not None and scale is not None else None
    interval = None
    for k in range(1, n // 2 + 1):
        coverage = 1 - 2 * sum(math.comb(n, j) for j in range(k)) / 2**n
        if coverage >= .9:
            interval = {'low': values[k - 1], 'high': values[n - k], 'coverage': coverage,
                        'assumption': 'independent_identically_distributed_cohort'}
    return {'count': n, 'median': median, 'mad': mad, 'scale_seconds': scale,
            'robust_z': score, 'median_interval': interval,
            'status': 'insufficient_cohort' if n < minimum_cohort or score is None else
                      'shift_observed' if abs(score) > z_threshold else 'within_variation'}


def duration_quality(current, cohort, policy, signal_reader):
    config = settings(policy)
    if not config['enabled']:
        return None
    now = signal_reader(current, {}, [])
    references = [signal_reader(item, {}, []) for item in cohort]
    signals = {name: assess(value, [row[name] for row in references if name in row],
        minimum_cohort=config['minimum_cohort'], z_threshold=config['z_threshold'],
        relative_floor=config['relative_floor']) for name, value in now.items()}
    if not policy.get('match_fields'):
        for row in signals.values():
            row['status'] = 'workload_unverified'
    return {'method': 'median_mad', 'cohort_count': len(cohort), 'signals': signals,
            'confidence_kind': 'conditional_dispersion_not_causal_probability'}


def validate_summary(row):
    fields = {'count', 'median', 'mad', 'scale_seconds', 'robust_z', 'median_interval', 'status'}
    if (not isinstance(row, dict) or set(row) != fields or type(row['count']) is not int
            or not 0 <= row['count'] <= 31 or row['status'] not in
            {'insufficient_cohort', 'workload_unverified', 'shift_observed', 'within_variation'}):
        raise ValueError('invalid robust baseline summary')
    if any(row[key] is not None and finite_number(row[key]) is None
           for key in ('median', 'mad', 'scale_seconds', 'robust_z')):
        raise ValueError('robust baseline statistics must be finite or null')
    interval = row['median_interval']
    if interval is not None and (not isinstance(interval, dict)
            or set(interval) != {'low', 'high', 'coverage', 'assumption'}
            or any(finite_number(interval.get(key)) is None for key in ('low', 'high', 'coverage'))
            or interval['low'] > interval['high'] or not 0 <= interval['coverage'] <= 1
            or interval['assumption'] != 'independent_identically_distributed_cohort'):
        raise ValueError('invalid conditional median interval')


def apply_limits(candidates, quality):
    if quality is None:
        return
    for candidate in candidates:
        limitations = []
        for evidence in candidate.get('evidence', []):
            row = quality['signals'].get(evidence['signal'])
            if row is None or evidence.get('baseline') is None:
                continue
            evidence['robust_baseline'] = row
            if row['status'] != 'shift_observed':
                limitations.append('robust_baseline:' + evidence['signal'] + ':' + row['status'])
        candidate['missing_evidence'].extend(limitations)
        if limitations and candidate['state'] == 'strong_signal':
            candidate['state'] = 'supporting_signal'
        candidate['signal_strength'] = candidate['state']
