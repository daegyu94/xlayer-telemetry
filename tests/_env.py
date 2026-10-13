"""Test subprocess environments inherit the suite's isolated settings."""

import os

from xlayer_telemetry.operations.config import KEYS


_PROXY_KEYS = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
_HARNESS_KEYS = {"XLAYER_TEST_CLICKHOUSE_CONTAINER", "XLAYER_TEST_CLICKHOUSE_URL"}


def external_setting(name: str) -> bool:
    upper = name.upper()
    return (upper not in _HARNESS_KEYS and
            (upper in _PROXY_KEYS or upper.startswith(("TELEMETRY_", "XLAYER_")) or upper in KEYS))


def clean_env(**extra: str) -> dict[str, str]:
    """Copy the environment after the autouse fixture removes external settings."""
    return {**os.environ, **extra}
