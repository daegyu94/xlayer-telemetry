"""Borrow canonical Mooncake queries without new telemetry or attribution."""
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, DEFAULT_QUERIES
from xlayer_telemetry.analysis.metric_queries import METRIC_PROFILES, PROFILE_SIGNALS, profile_queries
from xlayer_telemetry.operations import config


ROOT = Path(__file__).parents[1]
REFERENCES = {
    "mooncake_connector_rpc_p95_seconds": (60, "A"),
    "mooncake_dfs_read_p95_seconds": (66, "A"),
    "mooncake_dfs_write_p95_seconds": (66, "B"),
    "mooncake_dfs_write_staging_p95_seconds": (66, "C"),
    "mooncake_dfs_read_bytes_per_second": (64, "A"),
    "mooncake_dfs_read_errors_per_second": (67, "A"),
}


def borrow(settings=None):
    return profile_queries(settings or {"metric_profiles": ["mooncake"]}, "lab.prod")


def asset():
    return json.loads((ROOT / "examples/dashboards/agent-rl-stages.json").read_text())


def panels(items):
    for row in items:
        yield row
        yield from panels(row.get("panels", []))


def test_profile_is_six_borrowed_queries_with_literal_scope_and_preserved_guards():
    queries = borrow()
    assert set(queries) == set(REFERENCES)
    sources = {p["id"]: p for p in panels(asset()["panels"])}
    for signal, (panel_id, ref) in REFERENCES.items():
        original = next(t["expr"] for t in sources[panel_id]["targets"] if t["refId"] == ref)
        expected = original.replace('cluster=~"$cluster"', 'cluster="{cluster}"')
        expected = expected.replace('node=~"$node"', 'node="{rollout_node}"')
        expected = expected.replace('$engine', '.*').replace('$__rate_interval', '1m')
        assert queries[signal] == expected
        assert '$' not in queries[signal] and 'job="native"' in queries[signal]
    assert '/ 1000000' in queries["mooncake_dfs_read_p95_seconds"]
    assert 'le="+Inf"' in queries["mooncake_dfs_read_p95_seconds"]
    assert 'operation, status' in queries["mooncake_connector_rpc_p95_seconds"]
    assert PROFILE_SIGNALS["mooncake_dfs_read_errors_per_second"].unit == "keys/s"
    assert PROFILE_SIGNALS["mooncake_dfs_read_bytes_per_second"].statistic == "mean"


def test_default_profiles_do_not_read_dashboard_assets(monkeypatch):
    def unavailable():
        raise AssertionError("optional asset read")
    monkeypatch.setattr(config, "assets_root", unavailable)
    engine = DiagnosticEngine({"prometheus": {"url": "http://unused"}})
    assert set(engine._queries("lab")) == set(DEFAULT_QUERIES)
    assert len(profile_queries({"metric_profiles": ["host"]}, "lab")) == 6


@pytest.mark.parametrize("kind", ["missing", "macro", "unit", "duplicate", "oversize", "malformed-unit"])
def test_invalid_canonical_contract_fails_only_selected_profile(tmp_path, monkeypatch, kind):
    destination = tmp_path / "examples/dashboards/agent-rl-stages.json"
    destination.parent.mkdir(parents=True)
    document = asset()
    row = next(p for p in panels(document["panels"]) if p["id"] == 60)
    if kind == "macro":
        row["targets"][0]["expr"] += ' + $unknown'
    if kind == "unit":
        row["fieldConfig"]["defaults"]["unit"] = "ms"
    if kind == "duplicate":
        row["targets"].append(dict(row["targets"][0]))
    if kind == "malformed-unit":
        row["fieldConfig"] = None
    if kind != "missing":
        destination.write_text(json.dumps(document) if kind != "oversize" else ' ' * (2 * 1024 * 1024 + 1))
    monkeypatch.setattr(config, "assets_root", lambda: tmp_path)
    assert profile_queries({"metric_profiles": []}, "lab") == {}
    with pytest.raises(ValueError):
        borrow()


def test_packaged_assets_follow_the_distribution_record(tmp_path, monkeypatch):
    prefix = tmp_path / "install"
    site = prefix / "lib/python3.12/site-packages"
    module = site / "xlayer_telemetry/operations/config.py"
    module.parent.mkdir(parents=True)
    module.touch()
    scripts = prefix / "share/xlayer-telemetry/scripts"
    scripts.mkdir(parents=True)
    (scripts / "verl_local.sh").touch()
    dashboard = scripts.parent / "examples/dashboards/agent-rl-stages.json"
    dashboard.parent.mkdir(parents=True)
    dashboard.write_text(json.dumps(asset()))
    info = site / "xlayer_telemetry-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text('Metadata-Version: 2.1\nName: xlayer-telemetry\nVersion: 0.1.0\n')
    (info / "RECORD").write_text('xlayer_telemetry/operations/config.py,,\n'
                                '../../../share/xlayer-telemetry/scripts/verl_local.sh,,\n'
                                '../../../share/xlayer-telemetry/examples/dashboards/agent-rl-stages.json,,\n')
    distribution = metadata.Distribution.at(info)
    monkeypatch.setattr(config, "__file__", str(module))
    monkeypatch.setattr(metadata, "distribution", lambda name: distribution)
    assert len(borrow()) == 6


class Series:
    def __init__(self, *, failure=False, replaced=False):
        self.calls = []
        self.failure, self.replaced = failure, replaced

    def query_range_detail(self, query, start, end, step):
        self.calls.append((query, start, end))
        if 'node_time_seconds' in query or 'node_timex' in query:
            value=1 if 'node_timex_sync_status' in query else 0
            import re
            current = dict(min=value,max=value,mean=value,last=value,sample_count=3)
            identity = dict(job='telemetry', cluster='lab.prod',
                            instance=re.search(r'instance="([^"]+)"', query).group(1))
            return {'aggregate':current,'series':[{'labels':identity,'stats':current}]}
        if "mooncake" not in query:
            return {"aggregate": None, "series": []}
        if self.failure and "mooncake_dfs_write_latency_us" in query:
            raise ValueError("synthetic partial source failure")
        before = end == 70
        labels = {"cluster": "lab.prod", "node": "rollout.prod", "instance": "client-a",
                  "component": "client-a", "client_mode": "real", "cluster_id": "store-a"}
        connector = "vllm:mooncake" in query
        if connector:
            labels.update(instance="engine-a", component="engine-a", engine="0",
                          model_name="model", operation="load_get", status="ok")
        if before and self.replaced:
            labels["instance"] = "replacement"
        values = [(labels, 1 if before else 3), ({**labels, "instance": "other"}, 100 if before else 2)]
        rows = [{"labels": entity, "stats": {"min": value, "max": value, "mean": value, "last": value}}
                for entity, value in values]
        return {"aggregate": {"min": 1, "max": 100, "mean": 50}, "series": rows}


def report(client):
    engine = DiagnosticEngine({"cluster": "lab.prod", "rollout_node": "rollout.prod",
                               "clock": {"enabled": False,"monitoring_node":"trainer"},
                               "prometheus": {"url": "http://unused", "metric_profiles": ["mooncake"]}},
                              prometheus=client)
    before = {"run_id": "r", "node": "trainer", "worker_id": "driver", "record_id": "before",
              "step": 1, "observed_at": 70, "step_duration_seconds": 10,
              "analysis_window": {"start": 60, "end": 70}}
    now = {**before, "record_id": "now", "step": 2, "observed_at": 100,
           "analysis_window": {"start": 90, "end": 100}}
    return engine.analyze(now, [before])


def test_same_entity_comparison_and_request_count_keep_units_without_new_rule():
    client = Series()
    result = report(client)
    rows = [r for r in result["comparison"]["signals"] if r["signal"] in REFERENCES]
    assert len(rows) == 6
    for row in rows:
        assert (row["current"], row["baseline"], row["labels"]["instance"]) == (3, 1, "engine-a" if "connector" in row["signal"] else "client-a")
        assert row["unit"] == PROFILE_SIGNALS[row["signal"]].unit
        assert row["scope"] == "shared-service"
        assert 'cluster="lab.prod"' in row["query"] and 'node="rollout.prod"' in row["query"]
    assert len([q for q, _, end in client.calls if "mooncake" in q and end == 100]) == 6
    assert len([q for q, _, end in client.calls if "mooncake" in q and end == 70]) == 6
    assert not result["candidates"]


def test_partial_failure_keeps_other_comparisons_and_matching_baseline_missing():
    result = report(Series(failure=True))
    assert len([r for r in result["comparison"]["signals"] if r["signal"] in REFERENCES]) == 5
    assert "prometheus:mooncake_dfs_write_p95_seconds:ValueError" in result["missing_sources"]
    changed = report(Series(replaced=True))
    assert all(r["baseline"] is None for r in changed["comparison"]["signals"] if r["signal"] in REFERENCES)


def test_profile_custom_override_stays_last_and_budget_is_unchanged():
    engine = DiagnosticEngine({"prometheus": {"url": "http://unused", "metric_profiles": ["mooncake"],
                                             "queries": {"mooncake_dfs_read_p95_seconds": "explicit_override"}}})
    assert engine._queries("lab")["mooncake_dfs_read_p95_seconds"] == "explicit_override"
    assert "query_budget_seconds" not in engine.config


def test_normal_or_missing_client_signal_is_neither_zero_filled_nor_a_storage_candidate():
    class Normal(Series):
        def query_range_detail(self, query, start, end, step):
            data = super().query_range_detail(query, start, end, step)
            if "mooncake_dfs_read_errors_total" in query:
                return {"aggregate": None, "series": []}
            for row in data["series"] if "mooncake" in query else []:
                row["stats"] = {key: 2 for key in ("min", "max", "mean", "last")}
            return data
    result = report(Normal())
    rows = [r for r in result["comparison"]["signals"] if r["signal"] in REFERENCES]
    assert len(rows) == 5
    assert all(r["delta"] == 0 for r in rows)
    assert "prometheus:mooncake_dfs_read_errors_per_second" in result["missing_sources"]
    assert not result["candidates"]


def test_borrowed_queries_with_real_promtool_literal_scope_idle_and_native_units(tmp_path):
    tool = os.environ.get("PROMTOOL") or shutil.which("promtool")
    if not tool:
        pytest.skip("set PROMTOOL to evaluate canonical Mooncake profile queries")
    queries = {name: expression.replace('{cluster}', 'lab.prod').replace('{rollout_node}', 'rollout.prod')
               for name, expression in borrow().items()}
    input_series, expected = [], {name: [] for name in REFERENCES}

    def identity(labels):
        return '{' + ','.join(f'{key}="{value}"' for key, value in labels.items()) + '}'

    def histogram(metric, labels, scale, *, idle=False):
        for bound, count in [(100 * scale, 10), (1000 * scale, 95), (10000 * scale, 100), ('+Inf', 100)]:
            input_series.append({'series': metric + '_bucket' + identity({**labels, 'le': str(bound)}),
                                 'values': f'0+{0 if idle else count}x4'})

    for endpoint, scale in (("client-a", 1), ("client-b", 2), ("idle", 0), ("wrong-cluster", 3), ("wrong-node", 4)):
        labels = {"job": "native", "telemetry_source": "mooncake", "cluster": "lab.prod",
                  "node": "rollout.prod", "instance": endpoint, "component": endpoint,
                  "client_mode": "real", "cluster_id": "store"}
        if endpoint == "wrong-cluster":
            labels["cluster"] = "labXprod"  # Regex scope would wrongly include it.
        if endpoint == "wrong-node":
            labels["node"] = "rolloutXprod"
        for operation in ("read", "write", "write_staging"):
            histogram(f"mooncake_dfs_{operation}_latency_us", labels, scale or 1, idle=not scale)
            # Deliberately no _count: current client histogram count lacks native
            # labels; canonical +Inf activity guard remains authoritative here.
            if endpoint in ("client-a", "client-b"):
                output = {k: v for k, v in labels.items() if k not in {"job", "telemetry_source"}}
                expected[f"mooncake_dfs_{operation}_p95_seconds"].append({"labels": identity(output), "value": scale * .001})
        for metric, signal, extra in [
            ("mooncake_dfs_read_bytes_total", "mooncake_dfs_read_bytes_per_second", {}),
            ("mooncake_dfs_read_errors_total", "mooncake_dfs_read_errors_per_second", {"error": "FILE_READ_FAIL"}),
        ]:
            input_series.append({"series": metric + identity({**labels, **extra}), 'values': f'0+{30 * scale}x4'})
            if endpoint in ("client-a", "client-b", "idle"):
                expected[signal].append({"labels": identity({**labels, **extra}), "value": scale})

    for engine, scale in (("0", 1), ("1", 2), ("idle", 0)):
        labels = {"job": "native", "telemetry_source": "vllm", "cluster": "lab.prod", "node": "rollout.prod",
                  "instance": "engine-a", "component": "rollout-a", "model_name": "model",
                  "engine": engine, "operation": "load_get", "status": "ok"}
        for bound, count in [(str(.001 * (scale or 1)), 10), (str(.01 * (scale or 1)), 95), (str(.1 * (scale or 1)), 100), ('+Inf', 100)]:
            input_series.append({'series': 'vllm:mooncake_store_operation_time_seconds_bucket' + identity({**labels, 'le': bound}),
                                 'values': f'0+{0 if scale == 0 else count}x4'})
        input_series.append({'series': 'vllm:mooncake_store_operation_time_seconds_count' + identity(labels),
                             'values': f'0+{100 if scale else 0}x4'})
        if scale:
            output = {k: v for k, v in labels.items() if k not in {"job", "telemetry_source"}}
            expected['mooncake_connector_rpc_p95_seconds'].append({"labels": identity(output), "value": scale * .01})
    fixture = {'evaluation_interval': '30s', 'fuzzy_compare': True, 'tests': [
        {'interval': '30s', 'input_series': input_series,
         'promql_expr_test': [{'expr': expression, 'eval_time': '2m', 'exp_samples': expected[name]}
                              for name, expression in queries.items()]}]}
    source = tmp_path / 'mooncake-profile.json'
    source.write_text(json.dumps(fixture))
    process = subprocess.run([tool, 'test', 'rules', str(source)], capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stdout + process.stderr
