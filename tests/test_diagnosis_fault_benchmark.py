"""Keep the oracle outside production diagnosis input and test real engine paths."""

from examples.research.performance_diagnosis_benchmark import evaluate, graph_fixture
from xlayer_telemetry.analysis.execution_graph import build_graph
from xlayer_telemetry.analysis.trajectory_path import critical_path


def test_robust_cohort_reduces_false_strong_signals_without_losing_injected_patterns():
    result = evaluate(repeats=3)
    old, new = result['existing'], result['robust']
    assert old['scenarios']['noisy_tail']['false_positive'] == 3
    assert new['scenarios']['noisy_tail']['false_positive'] == 0
    assert new['scenarios']['short_cohort']['abstained'] == 3
    for kind in ('normal', 'gpu_wait', 'network_wait', 'host_memory', 'multi_job', 'workload_shift', 'stale', 'missing'):
        assert new['scenarios'][kind]['exact'] == 3, (kind, new['scenarios'][kind])
    assert new['queries'] == old['queries']


def test_linear_depth_does_not_need_recursion_or_unbounded_history():
    result = critical_path(build_graph(graph_fixture(4096), run_id='run'), ('trace', '0'))
    assert result['status'] == 'observed_path'
    assert result['observed_path_seconds'] == 4095
