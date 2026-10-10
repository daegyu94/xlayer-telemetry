"""Documented default TOML is consumed by the actual configuration loader."""
import re
from pathlib import Path

from xlayer_telemetry.operations.config import KEYS, load_config

ROOT = Path(__file__).parents[1]


def test_documented_alert_toml_loads_as_boolean_settings(tmp_path, monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    text = (ROOT / 'docs/monitoring-reference.md').read_text().split('## Enable Grafana Alerts', 1)[1]
    snippets = re.findall(r'```toml\n(.*?)```', text, re.S)
    assert snippets, 'The default TOML path needs an executable alert configuration example'
    path = tmp_path / 'alerts.toml'
    path.write_text(snippets[0])
    config, _ = load_config(path)
    assert config['ENABLE_ALERTS'] == '1'
    assert config['ENABLE_GPU_METRICS'] == '0'


def test_documented_remote_status_override_preserves_node_toml(tmp_path, monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    text = (ROOT / 'docs/multi-node.md').read_text().split('### Remote node에서 상태 조회', 1)[1]
    url = re.search(r'PROMETHEUS_URL=(http://127\.0\.0\.1:\d+)', text).group(1)
    assert '-L 127.0.0.1:29090:127.0.0.1:19090' in text
    path = tmp_path / 'node.toml'
    original = '[telemetry]\nPROMETHEUS_URL = "http://127.0.0.1:19090"\n'
    path.write_text(original)
    monkeypatch.setenv('PROMETHEUS_URL', url)
    assert load_config(path)[0]['PROMETHEUS_URL'] == url
    assert path.read_text() == original
