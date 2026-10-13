"""Diagnosis boundary contracts retain zero, identity and time provenance."""
import pytest

from xlayer_telemetry.analysis.observation_contracts import (
    EntityRef, EvidenceBundle, Observation, TimeWindow, usable_window,
)


@pytest.mark.parametrize('value', [0, {'max': 0, 'sample_count': 2}])
def test_zero_is_observed_and_absent_baseline_is_not_zero(value):
    packet = {'observations': [{'signal': 'gpu', 'current': value,
              'observation_scope': 'device', 'labels': {'node': 'n', 'gpu': '0'}}],
              'current_interval': {'start': 0, 'end': 1, 'accuracy': 'exact'},
              'clock_quality': {'status': 'unsafe'}}
    bundle = EvidenceBundle.decode(packet)
    assert bundle.current[0].status == 'observed'
    assert bundle.current[0].value == value
    assert bundle.baseline[0].value is None
    assert bundle.baseline[0].status == 'unknown'
    assert bundle.clock_quality['status'] == 'unsafe'
    assert bundle.window.usable


@pytest.mark.parametrize('window', [
    {'start': True, 'end': 2}, {'start': float('nan'), 'end': 2},
    {'start': 0, 'end': float('inf')}, {'start': 2, 'end': 1},
    {'start': None, 'end': 2}, {'start': 0, 'end': 1, 'time_alignment': {'uncertainty_seconds': -1}},
])
def test_invalid_windows_are_rejected(window):
    with pytest.raises(ValueError):
        TimeWindow.decode(window)
    assert not usable_window(window)


def test_legacy_unknown_window_and_calibration_are_lossless():
    assert not TimeWindow.decode({'start': None, 'end': None, 'accuracy': 'unknown'}).usable
    window = {'start': 1, 'end': 2, 'accuracy': 'calibrated', 'source': 'producer',
              'time_alignment': {'raw_window': {'start': 0, 'end': 1},
                                 'reference_id': 'ref', 'uncertainty_seconds': .1}}
    assert TimeWindow.decode(window).serialize() == window


def test_exact_entity_match_and_local_identity_namespace():
    a = EntityRef.decode('service', {'node': 'n', 'instance': 'a', 'engine': '0', '__name__': 'one'})
    same = EntityRef.decode('service', {'node': 'n', 'instance': 'a', 'engine': '0', '__name__': 'two'})
    b = EntityRef.decode('service', {'node': 'n', 'instance': 'b', 'engine': '0'})
    assert a.matches(same)
    assert not a.matches(b)
    assert a.namespace('engine') != b.namespace('engine')
    assert not a.matches(EntityRef.decode('device', dict(a.labels)))


def test_stale_value_remains_inspectable_with_reason():
    value = Observation.decode({'signal': 'gpu', 'current': 0,
        'sampling_quality': {'current': {'warnings': ['source_sample_before_interval']}}})
    assert value.value == 0 and value.status == 'stale'
    assert value.issues[0].code == 'source_sample_before_interval'


@pytest.mark.parametrize('status', ['error', 'unknown', 'no_data'])
def test_missing_states_are_distinct(status):
    observation = Observation.decode({'signal': 'gpu', 'current': None, 'observation_status': status})
    assert observation.status == status and observation.value is None


@pytest.mark.parametrize('value', [True, float('inf'), {'max': float('nan')}, {'sample_count': -1}])
def test_invalid_measurements_fail_at_boundary(value):
    with pytest.raises(ValueError):
        Observation.decode({'signal': 'gpu', 'current': value})


def test_source_query_and_entity_survive_without_quality_warnings():
    row = {'signal': 'gpu', 'current': 0, 'baseline': 90, 'source': 'prometheus',
           'query': 'measured_gpu', 'scope': 'device', 'labels': {'node': 'n', 'gpu': '0'},
           'sampling_quality': {'current': {'warnings': []}, 'baseline': {'warnings': []}}}
    bundle = EvidenceBundle.from_rows([row], window={'start': 10, 'end': 20},
                                     baseline_window={'start': 0, 'end': 10}, clock_quality={})
    for observation in (*bundle.current, *bundle.baseline):
        assert observation.source == 'prometheus' and observation.query == 'measured_gpu'
        assert dict(observation.entity.labels) == {'node': 'n', 'gpu': '0'}
        assert observation.issues == ()
    legacy = EvidenceBundle.from_report({'comparison': {'signals': [{'signal': 'gpu', 'current': 0}]}})
    assert legacy.current[0].source is None and legacy.current[0].query is None


@pytest.mark.parametrize('source', ['', ' ', 1, True, {}])
def test_source_identity_must_be_nonempty_text_when_present(source):
    with pytest.raises(ValueError, match='source must be nonempty text'):
        Observation.decode({'signal': 'gpu', 'current': 0, 'source': source})
