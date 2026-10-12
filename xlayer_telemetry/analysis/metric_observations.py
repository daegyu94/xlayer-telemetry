"""Collect scoped Prometheus observations without making diagnosis decisions."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, NamedTuple

from ..prometheus import escape_label
from .evidence_quality import check_source, quality, result_quality_issues


class QueryObservation(NamedTuple):
    statistics: dict | None
    series: list[dict]
    sampling_quality: dict
    source_samples: dict


def collect_query(client, query: str, start: float, end: float, step: float, *,
                  check_freshness: bool = False) -> QueryObservation:
    if hasattr(client, "query_range_detail"):
        detail = client.query_range_detail(query, start, end, step)
        statistics, series, result = detail.get("aggregate"), detail["series"], detail.get("result_quality")
    else:
        statistics, series, result = client.query_range(query, start, end, step), [], None
    source = check_source(client, query, start, end, step, per_entity=True) if check_freshness else {}
    return QueryObservation(statistics, series,
                            quality(query, start, end, step, statistics, source=source, result=result), source)


@dataclass
class MetricObservations:
    evidence: dict = field(default_factory=dict)
    baseline: dict = field(default_factory=dict)
    executed_queries: dict = field(default_factory=dict)
    sampling_quality: dict = field(default_factory=dict)
    source_samples: dict = field(default_factory=dict)
    current_series: dict = field(default_factory=dict)
    baseline_series: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


def collect_metrics(client, queries: Mapping[str, Any], context: Mapping[str, str],
                    start: float, end: float, step: float, *, baseline_window: Mapping | None = None,
                    check_freshness: bool = False) -> MetricObservations:
    observations = MetricObservations()
    for name, template in queries.items():
        try:
            query = str(template)
            for key, value in context.items():
                query = query.replace("{" + key + "}", escape_label(value))
            observations.executed_queries[name] = query
            current = collect_query(client, query, start, end, step, check_freshness=check_freshness)
            observations.source_samples[name] = {'current': current.source_samples}
            observations.sampling_quality[name] = {'current': current.sampling_quality}
            observations.missing.extend(f"prometheus:{name}:current:{issue}"
                                        for issue in result_quality_issues(current.sampling_quality))
            if current.statistics is None:
                observations.missing.append("prometheus:" + name)
            else:
                observations.evidence[name] = current.statistics
                observations.current_series[name] = current.series
            if baseline_window is not None:
                prior = collect_query(client, query, float(baseline_window['start']),
                                      float(baseline_window['end']), step, check_freshness=check_freshness)
                observations.source_samples[name]['baseline'] = prior.source_samples
                observations.sampling_quality[name]['baseline'] = prior.sampling_quality
                observations.missing.extend(f"prometheus:{name}:baseline:{issue}"
                                            for issue in result_quality_issues(prior.sampling_quality))
                if prior.statistics is not None:
                    observations.baseline[name] = prior.statistics
                    observations.baseline_series[name] = prior.series
                else:
                    observations.missing.append(f"prometheus:{name}:baseline_no_data")
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            observations.missing.append(f"prometheus:{name}:{type(error).__name__}")
    return observations
