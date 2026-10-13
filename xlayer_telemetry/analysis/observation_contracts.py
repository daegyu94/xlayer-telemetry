"""Validated diagnosis values; JSON remains the public artifact format."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from ..measurements import finite_number
from .evidence_quality import (INVALID_SOURCE_TIME, correlation_quality_issues, quality,
                               selected_quality, source_for_entity, validate_quality)

ObservationStatus = Literal['observed', 'no_data', 'error', 'stale', 'unknown']
STATISTICS = frozenset({'min', 'mean', 'max', 'last', 'sample_count',
                        'sampled_increase', 'max_series_delta'})


def assess_entity(query: str | None, window: Mapping[str, Any], step: float,
                  stats: Mapping[str, Any], labels: Mapping[str, str], *,
                  source: Mapping[str, Any], result: Mapping[str, Any] | None,
                  prior_quality: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Assess one selected entity; aggregate-only legacy quality stays explicit."""
    assessment = quality(query, window['start'], window['end'], step, stats,
                         source=source_for_entity(source, labels), result=result) if query is not None else (prior_quality or {})
    return selected_quality(assessment, stats)


@dataclass(frozen=True)
class Scope:
    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError('observation scope must be nonempty text')


@dataclass(frozen=True)
class EntityRef:
    scope: Scope
    labels: tuple[tuple[str, str], ...] = ()

    @classmethod
    def decode(cls, scope: str, labels: Mapping[str, str]) -> EntityRef:
        if not isinstance(labels, Mapping) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in labels.items()):
            raise ValueError('entity labels must be text pairs')
        return cls(Scope(scope), tuple(sorted(labels.items())))

    def matches(self, other: EntityRef) -> bool:
        # Metric names differ across signals; every resource label must match.
        return self.scope == other.scope and {
            k: v for k, v in self.labels if k != '__name__'
        } == {k: v for k, v in other.labels if k != '__name__'}

    def namespace(self, label: str) -> tuple[str | None, ...]:
        values = dict(self.labels)
        result: tuple[str | None, ...] = (values.get('cluster'), values.get('node', values.get('nodename')))
        if label in {'engine', 'engine_id', 'vllm_endpoint'}:
            result += tuple(values.get(k) for k in ('instance', 'component', 'model_name', 'model'))
        if label in {'worker', 'worker_id', 'rank'}:
            result += tuple(values.get(k) for k in ('run_id', 'role', 'producer'))
        return (*result, values.get(label))


@dataclass(frozen=True)
class TimeWindow:
    start: float | None
    end: float | None
    accuracy: str
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def decode(cls, value: Mapping[str, Any]) -> TimeWindow:
        if not isinstance(value, Mapping):
            raise ValueError('time window must be an object')
        start, end = value.get('start'), value.get('end')
        if any(v is not None and finite_number(v) is None for v in (start, end)):
            raise ValueError('time window bounds must be finite or null')
        if (start is None) != (end is None) or (start is not None and start >= end):
            raise ValueError('time window needs ordered paired bounds')
        alignment = value.get('time_alignment') or {}
        if not isinstance(alignment, Mapping):
            raise ValueError('time alignment must be an object')
        uncertainty = alignment.get('uncertainty_seconds')
        if uncertainty is not None and (finite_number(uncertainty) is None or uncertainty < 0):
            raise ValueError('time uncertainty must be finite and nonnegative')
        return cls(start, end, str(value.get('accuracy', 'unspecified')), dict(value))

    @property
    def usable(self) -> bool:
        return self.start is not None and self.end is not None and self.accuracy not in {
            'unknown', 'clock_discontinuity'}

    def serialize(self) -> dict[str, Any]:
        return dict(self.provenance)


def usable_window(value: Any) -> bool:
    try:
        return TimeWindow.decode(value).usable
    except ValueError:
        return False


@dataclass(frozen=True)
class Issue:
    code: str
    source: str = 'unknown'
    window: str = 'current'


def validate_measurement(value: Any) -> None:
    if value is None or finite_number(value) is not None:
        return
    if not isinstance(value, Mapping) or set(value) - STATISTICS:
        raise ValueError('measurement must be a finite number, null or supported numeric statistics')
    if any(v is not None and finite_number(v) is None for v in value.values()):
        raise ValueError('measurement statistics must be finite numbers or null')
    count = value.get('sample_count')
    if count is not None and (count < 0 or count % 1):
        raise ValueError('sample_count must be a nonnegative integer')


def has_measurement(value: Any) -> bool:
    return finite_number(value) is not None or (
        isinstance(value, Mapping) and (value.get('sample_count') is None or value['sample_count'] > 0)
        and any(finite_number(value.get(key)) is not None for key in STATISTICS - {'sample_count'}))


@dataclass(frozen=True)
class Observation:
    signal: str
    entity: EntityRef
    value: Any
    status: ObservationStatus
    unit: str | None = None
    statistic: str | None = None
    query: str | None = None
    issues: tuple[Issue, ...] = ()
    sampling_quality: Mapping[str, Any] = field(default_factory=dict)
    comparison_status: str | None = None
    source: str | None = None

    @classmethod
    def decode(cls, row: Mapping[str, Any], window: str = 'current') -> Observation:
        signal = row.get('signal')
        if not isinstance(signal, str) or not signal.strip():
            raise ValueError('observation signal must be nonempty text')
        value = row.get(window)
        validate_measurement(value)
        validate_quality(row.get('sampling_quality'))
        warnings = (row.get('sampling_quality') or {}).get(window, {}).get('warnings', [])
        status: ObservationStatus = 'observed' if has_measurement(value) else (
            'no_data' if window in row else 'unknown')
        declared = row.get('observation_status') if window == 'current' else None
        if declared is not None:
            if declared not in {'observed', 'no_data', 'error', 'stale', 'unknown'}:
                raise ValueError('invalid observation status')
            if declared == 'observed' and not has_measurement(value):
                raise ValueError('observed status needs a measurement')
            status = declared
        if INVALID_SOURCE_TIME.intersection(warnings):
            status = 'stale'
        for key in ('unit', 'window_statistic', 'query', 'source'):
            if row.get(key) is not None and (not isinstance(row[key], str) or not row[key].strip()):
                raise ValueError(f'observation {key} must be nonempty text')
        return cls(signal, EntityRef.decode(row.get('observation_scope', row.get('scope', 'unknown')),
                                           row.get('labels', {})), value, status,
                   row.get('unit'), row.get('window_statistic'), row.get('query'),
                   tuple(Issue(w, str(row.get('source', 'unknown')), window) for w in warnings),
                   dict((row.get('sampling_quality') or {}).get(window, {})),
                   row.get('comparison_status'), row.get('source'))


@dataclass(frozen=True)
class EvidenceBundle:
    current: tuple[Observation, ...]
    baseline: tuple[Observation, ...]
    window: TimeWindow | None
    baseline_window: TimeWindow | None
    clock_quality: Mapping[str, Any]
    issues: tuple[Issue, ...] = ()

    @property
    def timing_usable(self) -> bool:
        return self.clock_quality.get('status', 'unchecked') in {'aligned', 'unchecked'} and (
            self.clock_quality.get('baseline', {}).get('status', 'aligned') in {'aligned', 'unchecked'})

    def limiting_issues_at(self, index: int) -> tuple[Issue, ...]:
        sampling = tuple(Issue(issue, observations[index].signal, role)
                         for role, observations in (('current', self.current), ('baseline', self.baseline))
                         for issue in correlation_quality_issues(observations[index].sampling_quality))
        current = self.current[index]
        if current.comparison_status not in {None, 'not_reported', 'shared_report_window'}:
            sampling += (Issue('comparison_withheld:' + str(current.comparison_status), current.signal, 'baseline'),)
        return sampling

    def limiting_issues(self, signal: str, entity: EntityRef | None = None) -> tuple[Issue, ...]:
        return tuple(issue for index, observation in enumerate(self.current)
                     if observation.signal == signal and (entity is None or observation.entity.matches(entity))
                     for issue in self.limiting_issues_at(index))

    def limits_at(self, index: int) -> tuple[str, ...]:
        return tuple(dict.fromkeys(f'{issue.window}:{issue.source}:{issue.code}'
                                   for issue in self.limiting_issues_at(index)))

    def limits(self, signal: str, entity: EntityRef | None = None) -> tuple[str, ...]:
        return tuple(dict.fromkeys(f'{issue.window}:{issue.source}:{issue.code}'
                                   for issue in self.limiting_issues(signal, entity)))

    def available_signals(self, role: str = 'current') -> dict[str, Any]:
        return {observation.signal: observation.value
                for observation in (self.current if role == 'current' else self.baseline)
                if observation.status == 'observed'}

    @classmethod
    def from_rows(cls, rows: list[Mapping[str, Any]], *, window: Mapping[str, Any] | None,
                  baseline_window: Mapping[str, Any] | None,
                  clock_quality: Mapping[str, Any], missing: list[str] | None = None) -> EvidenceBundle:
        return cls.decode({'observations': rows, 'current_interval': window,
                           'baseline_interval': baseline_window, 'clock_quality': clock_quality,
                           'missing_sources': missing or []})

    @classmethod
    def from_report(cls, report: Mapping[str, Any]) -> EvidenceBundle:
        comparison = report.get('comparison', {})
        return cls.from_rows(comparison.get('signals', []), window=report.get('analysis_window'),
                             baseline_window=comparison.get('baseline_interval'),
                             clock_quality=report.get('clock_quality', {'status': 'unchecked'}),
                             missing=report.get('missing_sources', []))

    @classmethod
    def decode(cls, packet: Mapping[str, Any]) -> EvidenceBundle:
        rows = packet.get('observations', [])
        window, baseline = packet.get('current_interval'), packet.get('baseline_interval')
        return cls(tuple(Observation.decode(r) for r in rows),
                   tuple(Observation.decode(r, 'baseline') for r in rows),
                   TimeWindow.decode(window) if window is not None else None,
                   TimeWindow.decode(baseline) if baseline is not None else None,
                   packet.get('clock_quality', {'status': 'unchecked'}),
                   tuple(Issue(str(code)) for code in packet.get('missing_sources', [])))
