"""Exercise default subsystem evidence against Prometheus' actual evaluator."""
import json
import os
import shutil
import subprocess

import pytest

from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine


def test_default_resource_queries_with_real_promtool(tmp_path):
    tool = os.environ.get("PROMTOOL") or shutil.which("promtool")
    if not tool:
        pytest.skip("set PROMTOOL to evaluate subsystem queries")
    queries = DiagnosticEngine({"prometheus": {"url": "http://unused"}})._queries("lab")
    def render(name):
        query = queries[name]
        for key, value in {"cluster": "lab", "compute_node": "gpu", "rollout_node": "rollout"}.items():
            query = query.replace("{" + key + "}", value)
        return query

    rows = []
    for cluster, session, node, name, value in (
        ("lab", "s1", "head", "a", 2), ("lab", "s1", "trainer", "b", 2),
        ("lab", "s2", "head", "a", 1), ("other", "s1", "head", "a", 100),
    ):
        rows.append({"series": 'ray_tasks{cluster="%s",SessionName="%s",node="%s",Name="%s",'
                     'job="native",telemetry_source="ray",State="PENDING_NODE_ASSIGNMENT"}'
                     % (cluster, session, node, name), "values": str(value)})
    for gpu, used, total in (("0", 8, 16), ("1", 0, 16), ("2", 1, 0)):
        for kind, value in (("used", used), ("total", total)):
            rows.append({"series": 'telemetry_gpu_memory_%s_bytes{cluster="lab",job="telemetry",'
                         'nodename="gpu",gpu="%s"}' % (kind, gpu), "values": str(value)})
    fixture = {"evaluation_interval": "1m", "tests": [{"input_series": rows, "promql_expr_test": [
        {"expr": render("ray_pending_tasks"), "eval_time": "0m", "exp_samples": [
            {"labels": '{cluster="lab",SessionName="s1"}', "value": 4},
            {"labels": '{cluster="lab",SessionName="s2"}', "value": 1}]},
        {"expr": render("gpu_memory_usage_ratio"), "eval_time": "0m", "exp_samples": [
            {"labels": '{cluster="lab",job="telemetry",nodename="gpu",gpu="0"}', "value": .5},
            {"labels": '{cluster="lab",job="telemetry",nodename="gpu",gpu="1"}', "value": 0}]},
    ]}]}
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(fixture))
    result = subprocess.run([tool, "test", "rules", str(path)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_custom_ray_query_is_preserved():
    query = 'sum(ray_tasks{State="PENDING_ARGS_AVAIL"})'
    engine = DiagnosticEngine({"prometheus": {"url": "http://unused", "queries": {"ray_pending_tasks": query}}})
    assert engine._queries("lab")["ray_pending_tasks"] == query
