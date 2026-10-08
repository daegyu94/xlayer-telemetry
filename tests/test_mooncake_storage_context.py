"""Additional existing storage signals keep their client/master populations."""
import pytest

from xlayer_telemetry.analysis.metric_queries import profile_queries, PROFILE_SIGNALS


def test_storage_profile_reuses_write_key_operations_and_explicit_master_scope():
    queries = profile_queries({"metric_profiles": ["mooncake_storage"], "mooncake_master_node": "cache.node"}, "cluster")
    assert len(queries) == 8
    assert 'mooncake_dfs_write_bytes_total' in queries['mooncake_dfs_write_bytes_per_second']
    assert 'mooncake_dfs_read_ops_total' in queries['mooncake_dfs_read_keys_per_second']
    assert 'mooncake_dfs_write_errors_total' in queries['mooncake_dfs_write_errors_per_second']
    assert 'master_allocated_bytes' in queries['mooncake_master_allocated_bytes']
    assert 'node="cache.node"' in queries['mooncake_master_allocated_bytes']
    assert 'master_put_start_failures_total' in queries['mooncake_master_admission_failures_per_second']
    assert PROFILE_SIGNALS['mooncake_dfs_write_keys_per_second'].unit == 'keys/s'
    assert PROFILE_SIGNALS['mooncake_master_admission_failures_per_second'].unit == 'requests/s'


def test_missing_master_context_does_not_guess_rollout_node_or_issue_master_queries():
    queries = profile_queries({"metric_profiles": ["mooncake_storage"]}, "cluster")
    assert len(queries) == 5
    assert not any('master_' in name for name in queries)
    assert '{rollout_node}' in queries['mooncake_dfs_write_bytes_per_second']


def test_original_mooncake_profile_still_has_six_queries():
    assert len(profile_queries({"metric_profiles": ['mooncake']}, 'cluster')) == 6


@pytest.mark.parametrize('node',[None,False,''])
def test_explicit_master_identity_is_validated(node):
    with pytest.raises(ValueError):
        profile_queries({'metric_profiles':['mooncake_storage'],'mooncake_master_node':node},'cluster')
