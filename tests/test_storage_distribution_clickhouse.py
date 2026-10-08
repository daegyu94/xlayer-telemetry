"""Optional real ClickHouse SQL regression on an explicitly isolated container.

Set XLAYER_TEST_CLICKHOUSE_CONTAINER to an owned disposable ClickHouse container.
Only the fixture's uniquely named in-memory database is created and removed.
"""

import json
import os
import subprocess
import uuid

import pytest

from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine, ThreeFSClient


CONTAINER = os.environ.get("XLAYER_TEST_CLICKHOUSE_CONTAINER")
pytestmark = pytest.mark.skipif(not CONTAINER, reason="isolated ClickHouse container not configured")
LABELS = ("host", "tag", "mount_name", "instance", "io", "uid", "method", "pod", "thread", "statusCode")


@pytest.fixture
def storage():
    database = "xlayer_review_" + uuid.uuid4().hex

    def execute(query, data=None):
        return subprocess.run(["docker", "exec", "-i", CONTAINER, "clickhouse-client", "--multiquery", "--query", query],
                              input=data, capture_output=True, text=True, check=True, timeout=15).stdout

    execute("CREATE DATABASE " + database)
    try:
        execute(f"CREATE TABLE {database}.distributions (TIMESTAMP DateTime, metricName String, "
                + ", ".join(key + " String" for key in LABELS)
                + ", `count` Float64, mean Float64, `max` Float64, p99 Float64) ENGINE Memory")

        class Client(ThreeFSClient):
            def _query_rows(self, query):
                return [json.loads(line) for line in execute(query).splitlines()]

        def insert(stamp, p99, *, count=3, host="a", method="read"):
            row = {key: "" for key in LABELS}
            row.update(TIMESTAMP=stamp, metricName="client_read_latency", host=host, method=method,
                       count=count, mean=p99, max=p99, p99=p99)
            execute(f"INSERT INTO {database}.distributions FORMAT JSONEachRow", json.dumps(row) + "\n")

        yield Client("http://unused", database=database), insert, execute
    finally:
        execute("DROP DATABASE " + database)


def test_zero_count_extrema_and_freshness_do_not_contaminate_active_entity(storage):
    client, insert, execute = storage
    insert(100, 10)
    insert(101, 999, count=0)
    old = json.loads(execute(f"SELECT sum(`count`) AS sample_count, max(p99) AS p99 FROM {client.database}.distributions FORMAT JSONEachRow"))
    assert old == {"sample_count": 3, "p99": 999}  # Reproduce the previous SQL's retained contributor.
    row, = client.query_window(99, 103)
    assert row["count"] == 3 and row["max_observed_p99"] == row["max"] == row["weighted_mean"] == 10
    assert row["first_observed_at"] == row["last_observed_at"] == 100
    assert row["labels"]["host"] == "a"


def test_storage_diagnosis_matches_actual_sql_entities_instead_of_metric_only(storage):
    client, insert, _ = storage
    insert(100, 10)
    insert(100, 1000, host="b", method="write")
    insert(200, 20)
    insert(200, 100, host="b", method="write")
    insert(201, 999, count=0)
    class NoMetrics:
        def query_range(self, *args):
            return None
    def step(stamp):
        return {"record_id": str(stamp), "run_id": "fixture", "node": "node", "worker_id": "worker",
                "observed_at": stamp+3, "step_duration_seconds": 5,
                "analysis_window": {"start": stamp-1, "end": stamp+3, "accuracy": "approximate"}}
    class AlignedClocks(NoMetrics):
        def query_range(self,query,*args):
            if 'node_time_seconds' in query or 'node_timex' in query:
                value=1 if 'sync_status' in query else .001
                return dict(min=value,max=value,mean=value,last=value,sample_count=3)
            return None
    report = DiagnosticEngine({"cluster":"synthetic-clocks","clock":{"monitoring_node":"monitor"},
                               "prometheus": {"url": "http://unused"},
                               "threefs":{"url":"http://unused","clock_nodes":["a","b"]}},
                              prometheus=AlignedClocks(), threefs=client).analyze(step(200), [step(100)])
    selected, = [row for row in report["comparison"]["signals"] if row["signal"] == "threefs_p99_latency"]
    assert selected["current"] == 20 and selected["baseline"] == 10
    assert selected["labels"]["host"] == "a" and selected["labels"]["method"] == "read"
    finding, = [item for item in report["findings"] if item["component"] == "3fs"]
    elevated, = finding["signals"]["metrics"]
    assert elevated["ratio"] == 2 and elevated["labels"]["host"] == "a"


def test_actual_sql_rejects_more_than_the_entity_budget(storage):
    client, _, execute = storage
    execute(f"INSERT INTO {client.database}.distributions (TIMESTAMP,metricName,host,`count`,mean,`max`,p99) "
            "SELECT toDateTime(100),'read_latency',toString(number),1,1,1,1 FROM numbers(1001)")
    with pytest.raises(ValueError, match="1000 entity"):
        client.query_window(99, 103)
