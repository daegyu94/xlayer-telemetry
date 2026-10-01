"""Bounded backend requests and per-analysis failure isolation."""
import io
from urllib.error import HTTPError

import pytest

from xlayer_telemetry.analysis.query_budget import QueryBudget, QueryBudgetExceeded, BackendUnavailable
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, load_config
from xlayer_telemetry.prometheus import PrometheusClient


def test_deadline_stops_new_requests_and_is_monotonic():
    now = [0.0]
    class Source:
        calls = 0
        def query_range(self, *args):
            self.calls += 1
            now[0] += 2
            return {"mean": 1}
    source = Source()
    budget = QueryBudget(3, clock=lambda: now[0])
    client = budget.wrap(source, "prometheus")
    client.query_range(); client.query_range()
    with pytest.raises(QueryBudgetExceeded): client.query_range()
    assert source.calls == 2
    assert budget.summary()["sources"]["prometheus"]["skipped"] == 1


@pytest.mark.parametrize("code,blocked", [(400, False), (422, False), (429, True), (503, True)])
def test_query_errors_do_not_always_open_backend_circuit(code, blocked):
    class Source:
        def query_range(self):
            raise HTTPError("http://test", code, "failure", {}, io.BytesIO())
    budget = QueryBudget()
    client = budget.wrap(Source(), "prometheus")
    with pytest.raises(HTTPError): client.query_range()
    with pytest.raises(BackendUnavailable if blocked else HTTPError): client.query_range()
    assert budget.summary()["sources"]["prometheus"]["attempted"] == (1 if blocked else 2)


def test_real_client_timeout_is_clamped_without_mutating_shared_client(monkeypatch):
    from xlayer_telemetry import prometheus
    seen = []
    def response(request, timeout):
        seen.append(timeout)
        return {"status":"success", "data":{"result":[]}}
    monkeypatch.setattr(prometheus, "_read_json", response)
    now = [10.0]
    budget = QueryBudget(3, clock=lambda: now[0])
    source = PrometheusClient("http://test", 5)
    client = budget.wrap(source, "prometheus", configurable_timeout=True)
    now[0] = 12
    client.query_range("up", 1, 2, 1)
    assert seen == [1]
    assert source.timeout == 5


def test_unavailable_prometheus_does_not_block_threefs_and_next_analysis_recovers():
    class Prom:
        calls = 0
        broken = True
        def query_range(self, *args):
            self.calls += 1
            if self.broken: raise ConnectionRefusedError()
            return {"mean": 50, "max": 50}
    class Storage:
        calls = 0
        def query_window(self, *args):
            self.calls += 1
            return []
    prom, storage = Prom(), Storage()
    engine = DiagnosticEngine({"prometheus":{"url":"http://unused"}}, prometheus=prom,threefs=storage)
    report = engine.analyze(None, [])
    assert prom.calls == 1 and storage.calls == 2
    assert report["query_execution"]["sources"]["prometheus"]["skipped"] > 0
    assert any("BackendUnavailable" in item for item in report["missing_sources"])
    prom.broken = False
    assert engine.analyze(None, [])["evidence"]
    assert prom.calls > 1


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf"), True])
def test_invalid_budget_rejected_before_workload(tmp_path, seconds):
    import json
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"schema_version":1, "prometheus":{"url":"http://test"},
                               "query_budget_seconds":seconds}))
    with pytest.raises(ValueError, match="query_budget_seconds"):
        load_config(path)


def test_real_stalled_backend_consumes_one_timeout_not_one_per_query():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    import time
    release = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            release.wait(2)
            try:
                self.send_response(200); self.end_headers()
                self.wfile.write(b'{"status":"success","data":{"result":[]}}')
            except OSError:
                pass
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        engine = DiagnosticEngine({'query_budget_seconds':.1,
            'prometheus':{'url':f'http://127.0.0.1:{server.server_port}', 'timeout_seconds':5}})
        started = time.monotonic()
        report = engine.analyze(None, [])
        assert time.monotonic() - started < 2
        assert report['query_execution']['sources']['prometheus']['attempted'] == 1
        assert report['query_execution']['sources']['prometheus']['skipped'] >= 10
        assert report['verdict'] == 'insufficient_data'
    finally:
        release.set(); server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_current_storage_evidence_survives_baseline_timeout():
    row = {'metricName':'read_latency','count':3,'max_observed_p99':2}
    class Storage:
        calls = 0
        def query_window(self, *args):
            self.calls += 1
            if self.calls == 2: raise TimeoutError()
            return [row]
    class Prom:
        def query_range(self,*args): return None
    report = DiagnosticEngine({'prometheus':{'url':'http://unused'}},
        prometheus=Prom(),threefs=Storage()).analyze(None,[])
    assert report['evidence']['threefs_distributions'] == [row]
    assert 'threefs:TimeoutError' in report['missing_sources']


def test_missing_baseline_is_explicit_without_discarding_current_data():
    class Prom:
        def query_range(self, query, start, end, step):
            return {'mean':50, 'max':50} if start >= 20 else None
    base = {'record_id':'base','run_id':'r','node':'n','worker_id':'0','boundary_scope':'rl_step',
            'observed_at':10,'step_duration_seconds':10,'analysis_window':{'start':0,'end':10}}
    current = base | {'record_id':'current','observed_at':30,'analysis_window':{'start':20,'end':30}}
    report = DiagnosticEngine({'prometheus':{'url':'http://unused'}},prometheus=Prom()).analyze(current,[base])
    assert report['evidence']
    assert 'prometheus:gpu_utilization_percent:baseline_no_data' in report['missing_sources']
