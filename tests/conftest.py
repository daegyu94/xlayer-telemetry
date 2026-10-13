"""Keep lifecycle/config regression fixtures out of the developer's environment."""

import os
import pytest

from tests._env import external_setting


def pytest_configure(config):
    # Some HTTP clients capture proxy handlers while test modules import.
    config._xlayer_external_environment = {
        name: os.environ.pop(name) for name in tuple(os.environ) if external_setting(name)
    }


def pytest_unconfigure(config):
    os.environ.update(getattr(config, "_xlayer_external_environment", {}))


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in tuple(os.environ):
        if external_setting(name):
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch, isolated_environment):
    """Default paths remain exercised, but every test owns its writable HOME.

    Tests may override HOME again when checking specific config behavior.
    Subprocesses inherit the same isolation; production defaults are unchanged.
    """
    # Use a sibling fixture directory: many tests assert their tmp_path stays empty.
    home = tmp_path_factory.mktemp("xlayer-home")
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture(autouse=True)
def short_logger_settle(monkeypatch, isolated_environment):
    """Lifecycle fixtures do not model production Ray/FUSE tail gaps."""
    monkeypatch.setenv("TELEMETRY_LOGGER_SETTLE_SECONDS", ".05")
    monkeypatch.setenv("TELEMETRY_LOGGER_MAX_WAIT_SECONDS", ".3")
