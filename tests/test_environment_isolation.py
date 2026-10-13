"""External settings never become test defaults; test-owned values still pass through."""

import os

from tests._env import clean_env


def test_suite_environment_and_explicit_subprocess_values(monkeypatch):
    assert all(name not in os.environ for name in
               ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                "TELEMETRY_HOME", "XLAYER_CONFIG", "PROMETHEUS_URL"))
    assert os.environ["TELEMETRY_LOGGER_SETTLE_SECONDS"] == ".05"
    monkeypatch.setenv("TELEMETRY_HOME", "test-owned")
    assert clean_env(HTTP_PROXY="http://127.0.0.1:1")["TELEMETRY_HOME"] == "test-owned"
    assert clean_env(HTTP_PROXY="http://127.0.0.1:1")["HTTP_PROXY"] == "http://127.0.0.1:1"
