import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("demo_coverage", ROOT / "examples/investigation/validate_demo_coverage.py")
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)


def test_query_interpolation_preserves_log_templates_and_escapes_literals():
    query = coverage.render_query('metric{cluster=~"$cluster",node=~"${source_node:regex}",run=~"$run_id"}[${__rate_interval}]',
                                  cluster="demo-b300", run_id="run.1", observer_node="gpu-0")
    assert 'cluster=~"demo\\\\-b300"' in query
    assert 'run=~"run\\\\.1"' in query
    assert query.endswith('[1m]')
    with pytest.raises(ValueError, match="Unknown dashboard"):
        coverage.render_query('$unrecognized', cluster="c", run_id="r", observer_node="n")


def test_log_literal_filters_and_prometheus_label_replacement_are_not_variables():
    query=coverage.render_query('logs |= ${log_search:doublequote} |= ${log_severity:doublequote}',cluster='c',run_id='r',observer_node='n')
    assert query=='logs |= "" |= ""'
    assert coverage.render_query('label_replace(metric{},"node","$1","resource","(.+)")',cluster='c',run_id='r',observer_node='n').endswith('"$1","resource","(.+)")')


@pytest.mark.parametrize("finite,with_loki,expected", [(True, True, 0), (False, True, 1), (True, False, 1)])
def test_coverage_never_reports_nan_or_skipped_loki_as_full_success(tmp_path, monkeypatch, finite, with_loki, expected):
    dashboards = tmp_path / "dashboards"
    dashboards.mkdir()
    panels = [{"id": 1, "type": "row", "panels": [
        {"title": "metric", "datasource": {"uid": "telemetry-prometheus"}, "targets": [{"refId": "A", "expr": "metric{}"}]},
        {"title": "logs", "datasource": {"uid": "telemetry-loki"}, "targets": [{"refId": "A", "expr": '{signal="event"}'}]},
    ]}]
    (dashboards / "demo.json").write_text(json.dumps({"uid": "demo", "panels": panels}))
    def get(url, path, params):
        return [{"value": [100, "1" if finite else "NaN"]}] if "prom" in url else [{"values": [["100", "event"]]}]
    monkeypatch.setattr(coverage, "get", get)
    args = SimpleNamespace(dashboard_dir=dashboards, cluster="c", run_id="r", observer_node="n",
                           prometheus_url="http://prom", loki_url="http://loki" if with_loki else None,
                           lookback_seconds=3600, output=tmp_path / "report.json")
    assert coverage.validate(args) == expected
    report = json.loads(args.output.read_text())
    assert report["all_queries_covered"] == (expected == 0)
    if not with_loki:
        assert report["checks"][1]["status"] == "not_validated"
