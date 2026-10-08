"""Budgets are enforced in both CLI config and generated Prometheus jobs."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from xlayer_telemetry.operations.config import ConfigError, defaults, load_config, validate_collection_budgets

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize('key,value', [
    ('PROMETHEUS_SAMPLE_LIMIT', '0'), ('PROMETHEUS_SAMPLE_LIMIT', '1000001'),
    ('PROMETHEUS_TARGET_LIMIT', '-1'), ('PROMETHEUS_BODY_SIZE_LIMIT_MB', '1MB'),
    ('NATIVE_SCRAPE_INTERVAL_SECONDS', 'nan'), ('NATIVE_SCRAPE_TIMEOUT_SECONDS', '6'),
    ('GPU_PROCESS_METRICS', 'true'), ('GPU_MAX_PROCESSES', '0'),
])
def test_collection_budgets_reject_invalid_values(key, value):
    with pytest.raises(ConfigError, match=key):
        validate_collection_budgets(defaults() | {key: value})


def test_toml_and_legacy_config_preserve_collection_budgets(tmp_path):
    custom = {'PROMETHEUS_SAMPLE_LIMIT': '50000', 'NATIVE_SCRAPE_INTERVAL_SECONDS': '10',
              'NATIVE_SCRAPE_TIMEOUT_SECONDS': '8', 'GPU_PROCESS_METRICS': '1', 'GPU_MAX_PROCESSES': '32'}
    for suffix in ('toml', 'conf'):
        path = tmp_path / f'config.{suffix}'
        path.write_text(('[telemetry]\n' if suffix == 'toml' else '') +
                        '\n'.join(f'{key}={json.dumps(value)}' for key, value in custom.items()))
        config, _ = load_config(path)
        assert all(config[key] == value for key, value in custom.items())


def test_launcher_generates_bounded_host_and_native_scrapes(tmp_path):
    sources = tmp_path / 'sources.json'
    sources.write_text(json.dumps({'schema_version': 1, 'sources': [
        {'name': 'dcgm-node', 'kind': 'dcgm', 'target': 'node:9400', 'labels': {'node': 'node'}}]}))
    output = tmp_path / 'server'
    env = os.environ | {'SERVER_CONFIG_ONLY': '1', 'OUTPUT_DIR': str(output),
        'TELEMETRY_TARGETS': 'node=127.0.0.1',
        'TELEMETRY_SOURCES_FILE': str(sources), 'PROMETHEUS_SAMPLE_LIMIT': '12000',
        'NATIVE_SCRAPE_INTERVAL_SECONDS': '10', 'NATIVE_SCRAPE_TIMEOUT_SECONDS': '8'}
    result = subprocess.run(['bash', str(ROOT / 'scripts/run_telemetry.sh'), 'server'],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    text = (output / 'prometheus.yml').read_text()
    for fragment in ('sample_limit: 12000', 'target_limit: 1024', 'body_size_limit: 16MB',
                     'label_limit: 40', 'label_name_length_limit: 128', 'label_value_length_limit: 1024',
                     'scrape_interval: 2s', 'scrape_timeout: 2s',
                     'scrape_interval: 10s', 'scrape_timeout: 8s'):
        assert fragment in text
    bad = subprocess.run(['bash', str(ROOT / 'scripts/run_telemetry.sh'), 'server'],
                         env=env | {'OUTPUT_DIR': str(tmp_path / 'bad'), 'PROMETHEUS_SAMPLE_LIMIT': '0'},
                         capture_output=True, text=True, timeout=10)
    assert bad.returncode == 2
    assert not (tmp_path / 'bad/prometheus.yml').exists()
