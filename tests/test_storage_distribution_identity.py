"""3FS distribution comparisons must retain producer identity and contributors."""
import io
import json

import pytest

from xlayer_telemetry.analysis import diagnostics


LABELS = ("host", "tag", "mount_name", "instance", "io", "uid", "method",
          "pod", "thread", "statusCode")


def distribution(value, *, host="a", method="read", name="client_read_latency", legacy=False):
    row = {"metricName": name, "count": 3, "max_observed_p99": value,
           "weighted_mean": value}
    if not legacy:
        row["labels"] = {key: "" for key in LABELS}
        row["labels"].update(host=host, method=method)
    return row


class EmptyPrometheus:
    def query_range(self, *args):
        return None


class Windows:
    def __init__(self, current, baseline):
        self.rows = iter((current, baseline))

    def query_window(self, *args):
        return next(self.rows)


def report(current, baseline, *, request_size=False, verified=False):
    records = [{"record_id": str(i), "run_id": "r", "worker_id": "w", "node": "n",
                "step": i, "observed_at": i * 10, "step_duration_seconds": 5,
                "analysis_window": {"start": i * 10 - 5, "end": i * 10}}
               for i in (1, 2)]
    config = {"prometheus": {"url": "http://unused"}}
    if request_size:
        config["threefs"] = {"url": "http://unused", "request_size_metric": "request_bytes"}
    class Clocks(EmptyPrometheus):
        def query_range(self,query,*args):
            if 'node_time_seconds' in query or 'node_timex' in query:
                value=1 if 'sync_status' in query else .001
                return dict(min=value,max=value,mean=value,last=value,sample_count=3)
            return None
    if verified:
        config.update(cluster='synthetic-clocks',clock={'monitoring_node':'monitor'})
        config['threefs']={**config.get('threefs',{}),'url':'http://unused','clock_nodes':['a','b']}
    engine = diagnostics.DiagnosticEngine(config, prometheus=Clocks() if verified else EmptyPrometheus(),
                                          threefs=Windows(current, baseline))
    return engine.analyze(records[1], records[:1])


def test_distribution_sql_keeps_all_entities_and_only_positive_contributors(monkeypatch):
    captured = []
    labels = {key: "" for key in LABELS}
    labels.update(host="a", method="read")
    raw = {"metricName": "read_latency", **labels, "sample_count": "3",
           "max_value": 10, "max_observed_p99": 9}
    def load(request, **kwargs):
        captured.append(request.data.decode())
        return io.BytesIO((json.dumps(raw) + "\n").encode())
    monkeypatch.setattr(diagnostics, "urlopen", load)
    row, = diagnostics.ThreeFSClient("http://unused").query_window(1, 2)
    assert row["labels"] == labels
    assert not (set(row) & set(LABELS))
    query, = captured
    assert "GROUP BY metricName, " + ", ".join(LABELS) in query
    assert "`count` > 0" in query
    assert "LIMIT 1001" in query
    assert "max(p99) AS max_observed_p99" in query


@pytest.mark.parametrize("before", [distribution(1, host="b"), distribution(1, method="write"),
                                   distribution(1, legacy=True)])
def test_different_distribution_identity_never_produces_latency_comparison(before):
    result = report([distribution(20)], [before])
    assert not [r for r in result["comparison"]["signals"] if r["signal"] == "threefs_p99_latency"]
    assert not [r for r in result["findings"] if r["component"] == "3fs"]


def test_multi_entity_comparison_selects_same_entity_and_projects_full_identity():
    current = [distribution(20), distribution(100, host="b")]
    before = [distribution(10), distribution(1000, host="b")]
    result = report(current, before, verified=True)
    row, = [r for r in result["comparison"]["signals"] if r["signal"] == "threefs_p99_latency"]
    assert (row["current"], row["baseline"]) == (20, 10)
    assert row["labels"] == {"metricName": "client_read_latency", **current[0]["labels"]}
    finding, = [r for r in result["findings"] if r["component"] == "3fs"]
    elevated, = finding["signals"]["metrics"]
    assert elevated["labels"]["host"] == "a"
    projected, = [r for r in diagnostics._investigation_rows(result)
                  if r.get("signal") == "threefs_p99_latency"]
    assert "host=a" in projected["entity"] and "method=read" in projected["entity"]


def test_legacy_distribution_rows_remain_comparable_only_to_legacy():
    result = report([distribution(20, legacy=True)], [distribution(10, legacy=True)])
    row, = [r for r in result["comparison"]["signals"] if r["signal"] == "threefs_p99_latency"]
    assert row["labels"] == {"metricName": "client_read_latency"}
    assert row["current"] == 20 and row["baseline"] == 10


def test_legacy_current_cannot_join_explicit_baseline():
    result = report([distribution(20, legacy=True)], [distribution(1)])
    assert not [r for r in result["comparison"]["signals"] if r["signal"] == "threefs_p99_latency"]
    assert "threefs:baseline_entity_match" in result["missing_sources"]


def test_duplicate_entity_rows_do_not_last_write_win():
    result = report([distribution(20), distribution(100)], [distribution(10)])
    assert not [r for r in result["comparison"]["signals"] if r["signal"] == "threefs_p99_latency"]
    assert not [r for r in result["findings"] if r["component"] == "3fs"]


def test_request_size_matches_current_baseline_and_selected_latency_entity():
    current = [distribution(20), distribution(100, name="request_bytes", host="b"),
               distribution(2048, name="request_bytes")]
    before = [distribution(10), distribution(50, name="request_bytes", host="b"),
              distribution(4096, name="request_bytes")]
    result = report(current, before, request_size=True)
    row, = [r for r in result["comparison"]["signals"] if r["signal"] == "storage_request_bytes"]
    assert (row["current"], row["baseline"]) == (2048, 4096)
    assert row["labels"]["host"] == "a"


def test_request_size_different_entities_remain_missing():
    result = report([distribution(1024, name="request_bytes")],
                    [distribution(10000, name="request_bytes", host="b")], request_size=True)
    assert not [r for r in result["comparison"]["signals"] if r["signal"] == "storage_request_bytes"]
    assert "threefs:request_size_entity_match" in result["missing_sources"]


def test_partial_identity_is_rejected_instead_of_becoming_legacy(monkeypatch):
    monkeypatch.setattr(diagnostics.ThreeFSClient, "_query_rows", lambda *args: [
        {"metricName": "read_latency", "host": "a", "count": 3, "max_observed_p99": 10}])
    with pytest.raises(ValueError, match="distribution identity"):
        diagnostics.ThreeFSClient("http://unused").query_window(1, 2)


def test_unlabeled_but_annotated_custom_row_cannot_join_legacy():
    current = distribution(20, legacy=True)
    current["host"] = "a"
    result = report([current], [distribution(1, legacy=True)])
    assert not [r for r in result["comparison"]["signals"] if r["signal"] == "threefs_p99_latency"]


def test_request_size_ambiguous_cohort_does_not_pick_first_entity():
    rows = [distribution(100, name="request_bytes"), distribution(200, name="request_bytes", host="b")]
    result = report(rows, rows, request_size=True)
    assert not [r for r in result["comparison"]["signals"] if r["signal"] == "storage_request_bytes"]


def test_distribution_entity_limit_remains_bounded(monkeypatch):
    monkeypatch.setattr(diagnostics.ThreeFSClient, "_query_rows", lambda *args: [
        {"metricName": "latency", "host": str(i), **{key: "" for key in LABELS if key != "host"}}
        for i in range(1001)])
    with pytest.raises(ValueError, match="1000"):
        diagnostics.ThreeFSClient("http://unused").query_window(1, 2)
