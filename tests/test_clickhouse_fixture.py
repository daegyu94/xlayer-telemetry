"""The native test harness stays on an explicitly owned loopback backend."""
import pytest

from tests import _clickhouse
from tests._env import external_setting


def test_native_backend_setting_survives_environment_isolation():
    assert not external_setting('XLAYER_TEST_CLICKHOUSE_URL')
    assert external_setting('XLAYER_CONFIG')


@pytest.mark.parametrize('url', ['http://example.com:8123', 'https://127.0.0.1:8123'])
def test_fixture_rejects_non_loopback_or_wrong_protocol(monkeypatch, url):
    monkeypatch.setattr(_clickhouse, 'URL', url)
    with pytest.raises(ValueError, match='owned loopback'):
        _clickhouse.execute('SELECT 1')
