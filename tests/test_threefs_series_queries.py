"""Collection-report series do not invent spans, gauge totals, or missing zeros."""
import io
import json

import pytest

from xlayer_telemetry.analysis import diagnostics
from xlayer_telemetry.analysis.query_budget import QueryBudget, QueryBudgetExceeded, BackendUnavailable


DIST = ("host", "tag", "mount_name", "instance", "io", "uid", "method", "pod", "thread", "statusCode")
COUNTER = tuple(name for name in DIST if name != "method")
RESET = "storage_client.data_payload_bytes"
GAUGE = "storage_client.num_update_channels.inuse"


def row(kind="counter", **values):
    labels = DIST if kind == "distribution" else COUNTER
    result = {"metricName": RESET, "timestamp_seconds": "91", **dict.fromkeys(labels, "")}
    result.update(host="client-a", instance="batchRead")
    result.update({"sample_count": "2", "min": 5, "max": 7, "last": 7, "observed_sum": 12}
                  if kind == "counter" else {"sample_count": "3", "weighted_mean": 10,
                                             "max_value": 20, "max_observed_p99": 19, "report_count": "1"})
    result.update(values)
    return result


def client(monkeypatch, rows):
    calls = []
    def read(request, timeout):
        calls.append((request.data.decode(), timeout))
        return io.BytesIO(('\n'.join(json.dumps(r) for r in rows)+'\n').encode())
    monkeypatch.setattr(diagnostics, "urlopen", read)
    return diagnostics.ThreeFSClient("http://unused"), calls


def test_distribution_series_retains_full_identity_report_count_and_integer_membership(monkeypatch):
    source, calls = client(monkeypatch, [row("distribution", metricName="read_latency")])
    result, = source.query_distribution_series(90.1, 91.1, metric_names=["read_latency"], max_points=2)
    assert result["timestamp_seconds"] == 91
    assert result["labels"] == {key: row("distribution")[key] for key in DIST}
    assert (result["count"], result["weighted_mean"], result["max"], result["max_observed_p99"], result["report_count"]) == (3, 10, 20, 19, 1)
    query = calls[0][0]
    assert "TIMESTAMP >= toDateTime(91)" in query and "TIMESTAMP < toDateTime(92)" in query
    assert "GROUP BY TIMESTAMP, metricName, " + ", ".join(DIST) in query
    assert "`count` > 0" in query and "LIMIT 3" in query
    assert "sum(`count`) AS sample_count" in query


def test_reset_same_second_sum_is_returned_but_last_value_is_ambiguous(monkeypatch):
    source, _ = client(monkeypatch, [row()])
    result, = source.query_counter_series(90, 92)
    assert result["observed_sum"] == 12  # 5+7 reset reports, not counter delta 7-5.
    assert result["min"] == 5 and result["max"] == 7
    assert result["ambiguous_sample"] and result["value"] is None and result["last"] is None
    assert result["kind"] == "reset_on_collect" and result["unit"] == "bytes"
    assert result["source_revision"] == "22fca04564c7cc230fd8b9523b8b92864e1dad47"


@pytest.mark.parametrize("name,kind", [(GAUGE, "gauge"), ("custom_bytes_total", None)])
def test_gauge_and_unknown_names_never_become_summed_counters(monkeypatch, name, kind):
    source, _ = client(monkeypatch, [row(metricName=name)])
    result, = source.query_counter_series(90, 92)
    assert result["kind"] == kind
    assert result["observed_sum"] is None
    assert result["value"] is None and result["last"] is None


def test_single_report_measured_zero_is_not_no_data(monkeypatch):
    source, _ = client(monkeypatch, [row(metricName=GAUGE, sample_count=1, min=0, max=0, last=0)])
    result, = source.query_counter_series(90, 92)
    assert result["value"] == 0 and result["last"] == 0
    assert not result["ambiguous_sample"] and result["observed_sum"] is None


@pytest.mark.parametrize("method", ["query_distribution_series", "query_counter_series"])
def test_subsecond_no_integer_timestamp_and_empty_source_have_no_zero_fill(monkeypatch, method):
    source, calls = client(monkeypatch, [])
    assert getattr(source, method)(90.1, 90.9) == []
    assert calls == []
    assert getattr(source, method)(90, 91) == []


@pytest.mark.parametrize("kwargs", [{"max_points": 0}, {"max_points": 2001}, {"max_points": True},
                                    {"metric_names": "x"}, {"metric_names": [""]},
                                    {"metric_names": ["x"*257]}, {"metric_names": ["x"]*17}])
def test_series_input_budgets_are_checked_before_query(monkeypatch, kwargs):
    source, calls = client(monkeypatch, [])
    with pytest.raises(ValueError):
        source.query_counter_series(90, 92, **kwargs)
    assert not calls


@pytest.mark.parametrize("window", [(0, 3601), (1, 1), (2, 1), (0, float("inf")), (True, 2)])
def test_series_windows_are_finite_increasing_and_at_most_one_hour(monkeypatch, window):
    source, calls = client(monkeypatch, [])
    with pytest.raises(ValueError):
        source.query_distribution_series(*window)
    assert not calls


def test_metric_name_values_are_sql_literals_not_interpolated_identifiers(monkeypatch):
    source, calls = client(monkeypatch, [])
    value = "x' OR 1=1 --\\path"
    source.query_counter_series(90, 92, metric_names=[value])
    assert "metricName IN ('x\\' OR 1=1 --\\\\path')" in calls[0][0]


@pytest.mark.parametrize("method,kind", [("query_distribution_series", "distribution"), ("query_counter_series", "counter")])
def test_series_limit_overflow_is_an_error_not_silent_truncation(monkeypatch, method, kind):
    source, _ = client(monkeypatch, [row(kind), row(kind)])
    with pytest.raises(ValueError, match="point limit"):
        getattr(source, method)(90, 92, max_points=1)


def test_counter_method_filter_is_rejected_not_discarded(monkeypatch):
    source, calls = client(monkeypatch, [])
    source.filters = {"method": "read"}
    with pytest.raises(ValueError, match="method"):
        source.query_counter_series(90, 92)
    assert not calls


@pytest.mark.parametrize("invalid", [{"timestamp_seconds": 99}, {"host": None}, {"sample_count": 0},
                                      {"sample_count": 1, "last": None}, {"sample_count": "bad"}])
def test_invalid_counter_data_does_not_make_an_observation(monkeypatch, invalid):
    source, _ = client(monkeypatch, [row(**invalid)])
    with pytest.raises(ValueError):
        source.query_counter_series(90, 92)


@pytest.mark.parametrize("method", ["query_counter_series", "query_distribution_series", "query_counters"])
def test_all_storage_getters_consume_budget_and_stop_after_failure(method):
    class Broken:
        def query_counter_series(self, *args):
            raise ConnectionRefusedError()
        query_distribution_series = query_counter_series
        query_counters = query_counter_series
    budget = QueryBudget(30)
    wrapped = budget.wrap(Broken(), "threefs")
    with pytest.raises(ConnectionRefusedError):
        getattr(wrapped, method)(90, 92)
    with pytest.raises(BackendUnavailable):
        getattr(wrapped, method)(90, 92)
    assert budget.summary()["sources"]["threefs"] == {"attempted": 1, "failed": 1, "skipped": 1, "unavailable": True}


def test_counter_series_timeout_is_clamped_and_exhaustion_prevents_request(monkeypatch):
    source, calls = client(monkeypatch, [])
    now = [0]
    budget = QueryBudget(3, clock=lambda: now[0])
    wrapped = budget.wrap(source, "threefs", configurable_timeout=True)
    now[0] = 2
    wrapped.query_counter_series(90, 92)
    assert calls[0][1] == 1 and source.timeout == 5
    now[0] = 4
    with pytest.raises(QueryBudgetExceeded):
        wrapped.query_distribution_series(90, 92)


def test_separate_reset_reports_keep_values_and_observed_sum_without_counter_delta(monkeypatch):
    source, _ = client(monkeypatch, [row(sample_count=1, min=5, max=5, last=5, observed_sum=5),
                                    row(timestamp_seconds=92, sample_count=1, min=7, max=7, last=7, observed_sum=7)])
    reports = source.query_counter_series(90, 93)
    assert [item["value"] for item in reports] == [5, 7]
    assert sum(item["observed_sum"] for item in reports) == 12
    assert all("rate" not in item and "delta" not in item for item in reports)


def test_duplicate_same_time_identity_is_not_an_additional_collector_report(monkeypatch):
    source, _ = client(monkeypatch, [row(), row()])
    with pytest.raises(ValueError, match="duplicate"):
        source.query_counter_series(90, 92)


@pytest.mark.parametrize("invalid", [{"report_count": 0}, {"sample_count": 0},
                                      {"weighted_mean": None}, {"max_observed_p99": float("nan")}])
def test_distribution_missing_or_invalid_measurements_fail_without_zero_filling(monkeypatch, invalid):
    source, _ = client(monkeypatch, [row("distribution", **invalid)])
    with pytest.raises(ValueError):
        source.query_distribution_series(90, 92)


def test_known_reset_sum_cannot_conflict_with_returned_reports(monkeypatch):
    source, _ = client(monkeypatch, [row(observed_sum=99)])
    with pytest.raises(ValueError, match="sum is inconsistent"):
        source.query_counter_series(90, 92)


@pytest.mark.parametrize("method", ["query_distribution_series", "query_counter_series"])
def test_series_preserves_existing_eight_mib_transport_limit(monkeypatch, method):
    monkeypatch.setattr(diagnostics, "urlopen", lambda *args, **kwargs: io.BytesIO(b' ' * (8 * 1024 * 1024 + 1)))
    source = diagnostics.ThreeFSClient("http://unused")
    with pytest.raises(ValueError, match="8 MiB"):
        getattr(source, method)(90, 92)


def test_bad_distribution_does_not_open_circuit_or_discard_counter_source():
    class Partial:
        def query_distribution_series(self, *args):
            raise ValueError("invalid synthetic distribution body")
        def query_counter_series(self, *args):
            return [{"timestamp_seconds": 91}]
    budget = QueryBudget(30)
    wrapped = budget.wrap(Partial(), "threefs")
    with pytest.raises(ValueError):
        wrapped.query_distribution_series(90, 92)
    assert wrapped.query_counter_series(90, 92) == [{"timestamp_seconds": 91}]
    assert budget.summary()["sources"]["threefs"]["attempted"] == 2
    assert not budget.summary()["sources"]["threefs"]["unavailable"]
