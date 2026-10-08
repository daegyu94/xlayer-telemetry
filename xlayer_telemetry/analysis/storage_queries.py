"""Bounded 3FS collection-report series, not event spans or inferred rates.

Upstream TIMESTAMP is the producer's collection time truncated to seconds.
Default collection cadence is one second but is mutable and may overrun;
neither actual interval starts nor report sequence IDs are stored. Zero reset
reports may be suppressed. Returned rows cannot establish complete coverage.
"""
from __future__ import annotations

from collections.abc import Sequence
import math
from types import MappingProxyType

from ..measurements import finite_number


# Registry contract reference; the backend schema does not report its deployed
# source revision. Validate divergent deployments against their producer code.
SOURCE_REVISION = "22fca04564c7cc230fd8b9523b8b92864e1dad47"
DISTRIBUTION_LABELS = ("host", "tag", "mount_name", "instance", "io", "uid",
                       "method", "pod", "thread", "statusCode")
COUNTER_LABELS = tuple(key for key in DISTRIBUTION_LABELS if key != "method")

# Exact pinned declarations/call sites only. A table or name suffix is not a
# counter type contract. Per-user copies must not be summed with base metrics.
_CLIENT_SOURCE = "src/client/storage/StorageClientImpl.cc"
_SERVER_SOURCE = "src/storage/store/StorageTarget.cc"
_RESET_UNITS = {
    "storage_client.data_payload_bytes": ("bytes", _CLIENT_SOURCE),
    "storage_client.data_payload_bytes_per_user": ("bytes", _CLIENT_SOURCE),
    "storage_client.num_completed_ops": ("operations", _CLIENT_SOURCE),
    "storage_client.num_completed_ops_per_user": ("operations", _CLIENT_SOURCE),
    "storage_client.num_failed_ops": ("operations", _CLIENT_SOURCE),
    "storage_client.num_failed_ops_per_user": ("operations", _CLIENT_SOURCE),
    "storage_client.num_retried_ops": ("operations", _CLIENT_SOURCE),
    "storage_client.num_retried_ops_per_user": ("operations", _CLIENT_SOURCE),
    "storage_client.num_processed_chunks": ("chunks", _CLIENT_SOURCE),
    "storage_client.num_processed_chunks_per_user": ("chunks", _CLIENT_SOURCE),
    "storage.aio_read.count_per_disk": ("operations", _SERVER_SOURCE),
    "storage.aio_read.bytes_per_disk": ("bytes", _SERVER_SOURCE),
    "storage.aio_read.succ_bytes_per_disk": ("bytes", _SERVER_SOURCE),
    "storage.rdma_write.count": ("operations", "src/storage/aio/BatchReadJob.cc"),
    "storage.rdma_write.bytes": ("bytes", "src/storage/aio/BatchReadJob.cc"),
    "usrbio.piov.bw": ("bytes", "src/fuse/IoRing.cc"),
    "fuse.piov.bw": ("bytes", "src/fuse/FuseOps.cc"),
}
_GAUGE_UNITS = {
    "storage_client.num_update_channels.inuse": ("channels", "src/client/storage/UpdateChannelAllocator.cc"),
    "storage_client.num_update_channels.total": ("channels", "src/client/storage/UpdateChannelAllocator.cc"),
    "storage.aio_running_threads.count": ("threads", "src/storage/aio/AioReadWorker.cc"),
    "storage.target.used_size": ("bytes", _SERVER_SOURCE),
    "storage.target.reserved_size": ("bytes", _SERVER_SOURCE),
    "storage.target.unrecycled_size": ("bytes", _SERVER_SOURCE),
}
COUNTER_CONTRACTS = MappingProxyType({
    name: MappingProxyType({"kind": kind, "unit": unit, "source": source,
                           "source_revision": SOURCE_REVISION})
    for kind, table in (("reset_on_collect", _RESET_UNITS), ("gauge", _GAUGE_UNITS))
    for name, (unit, source) in table.items()
})


def _selection(client, start, end, metric_names, max_points):
    if (finite_number(start) is None or finite_number(end) is None
            or start >= end or end - start > 3600):
        raise ValueError("3FS series window must be increasing and at most one hour")
    if type(max_points) is not int or not 1 <= max_points <= 2000:
        raise ValueError("3FS series max_points must be 1..2000")
    if (not isinstance(metric_names, Sequence) or isinstance(metric_names, (str, bytes))
            or len(metric_names) > 16 or any(not isinstance(name, str) or not name
                or len(name.encode("utf-8")) > 256 or "\x00" in name for name in metric_names)):
        raise ValueError("3FS metric_names must contain at most 16 names of 1..256 bytes")
    where = client._where(start, end)
    if metric_names:
        where += " AND metricName IN (" + ", ".join(client._literal(name) for name in metric_names) + ")"
    return where, math.ceil(start), math.ceil(end)


def _rows(client, query, start, end, metric_names, max_points, labels):
    rows = client._query_rows(query)  # Uses the existing 8 MiB transport limit.
    if len(rows) > max_points:
        raise ValueError("3FS series exceeds point limit; narrow source/time/metric filters")
    seen = set()
    result = []
    for row in rows:
        if any(not isinstance(row.get(key), str) for key in labels):
            raise ValueError("invalid ClickHouse series identity")
        if metric_names and row["metricName"] not in metric_names:
            raise ValueError("ClickHouse series metric is outside the selected scope")
        timestamp = client._number(row.get("timestamp_seconds"), "timestamp_seconds", integer=True, nonnegative=True)
        if not start <= timestamp < end:
            raise ValueError("ClickHouse series timestamp is outside the query window")
        identity = (timestamp, row["metricName"], *(row[key] for key in labels))
        if identity in seen:
            raise ValueError("duplicate ClickHouse series identity")
        seen.add(identity)
        result.append((row, {"timestamp_seconds": timestamp, "metricName": row["metricName"],
                             "labels": {key: row[key] for key in labels}}))
    return result


def distribution_series(client, start, end, *, metric_names=(), max_points=2000):
    """Maximum reported p99 in one collection second/entity, never pooled p99."""
    where, first, stop = _selection(client, start, end, metric_names, max_points)
    if first == stop:
        return []
    identity = ", ".join(("TIMESTAMP", "metricName", *DISTRIBUTION_LABELS))
    query = (
        f"SELECT toUnixTimestamp(TIMESTAMP) AS timestamp_seconds, metricName, {', '.join(DISTRIBUTION_LABELS)}, "
        "sum(`count`) AS sample_count, sum(mean*`count`)/sum(`count`) AS weighted_mean, "
        "max(`max`) AS max_value, max(p99) AS max_observed_p99, count() AS report_count "
        f"FROM {client.database}.distributions WHERE {where} AND `count` > 0 "
        f"GROUP BY {identity} ORDER BY {identity} LIMIT {max_points + 1} FORMAT JSONEachRow"
    )
    result = []
    for raw, row in _rows(client, query, first, stop, metric_names, max_points, DISTRIBUTION_LABELS):
        for target, name in (("count", "sample_count"), ("weighted_mean", "weighted_mean"),
                             ("max", "max_value"), ("max_observed_p99", "max_observed_p99"),
                             ("report_count", "report_count")):
            integer = target in {"count", "report_count"}
            row[target] = client._number(raw.get(name), name, integer=integer, nonnegative=integer)
        if row["count"] <= 0 or row["report_count"] <= 0:
            raise ValueError("invalid nonpositive ClickHouse distribution report")
        result.append(row)
    return result


def counter_series(client, start, end, *, metric_names=(), max_points=2000):
    """Raw same-second reports; unknown/gauge values are never summed as IO."""
    if "method" in (client.filters or {}):
        raise ValueError("3FS counters have no method filter; distribution scope cannot be reused")
    where, first, stop = _selection(client, start, end, metric_names, max_points)
    if first == stop:
        return []
    reset_names = ", ".join(client._literal(name) for name in _RESET_UNITS)
    identity = ", ".join(("TIMESTAMP", "metricName", *COUNTER_LABELS))
    query = (
        f"SELECT toUnixTimestamp(TIMESTAMP) AS timestamp_seconds, metricName, {', '.join(COUNTER_LABELS)}, "
        "count() AS sample_count, min(val) AS min, max(val) AS max, "
        "if(count()=1,any(val),NULL) AS last, "
        f"if(metricName IN ({reset_names}),sum(val),NULL) AS observed_sum "
        f"FROM {client.database}.counters WHERE {where} "
        f"GROUP BY {identity} ORDER BY {identity} LIMIT {max_points + 1} FORMAT JSONEachRow"
    )
    result = []
    for raw, row in _rows(client, query, first, stop, metric_names, max_points, COUNTER_LABELS):
        count = client._number(raw.get("sample_count"), "sample_count", integer=True, nonnegative=True)
        if count <= 0:
            raise ValueError("invalid nonpositive ClickHouse counter report count")
        low, high = (client._number(raw.get(key), key, integer=True) for key in ("min", "max"))
        if low > high:
            raise ValueError("invalid ClickHouse counter extrema")
        last = client._number(raw.get("last"), "last", integer=True) if count == 1 else None
        if count == 1 and (low != last or high != last):
            raise ValueError("single counter report has inconsistent values")
        contract = COUNTER_CONTRACTS.get(row["metricName"])
        kind = contract["kind"] if contract else None
        observed_sum = client._number(raw.get("observed_sum"), "observed_sum", integer=True, nonnegative=True) if kind == "reset_on_collect" else None
        if kind == "reset_on_collect" and low < 0:
            raise ValueError("negative known IO reset report")
        if observed_sum is not None and not low * count <= observed_sum <= high * count:
            raise ValueError("known reset report sum is inconsistent with extrema/count")
        row.update(sample_count=count, min=low, max=high, last=last,
                   ambiguous_sample=count > 1, value=last, observed_sum=observed_sum,
                   kind=kind, unit=contract["unit"] if contract else None,
                   source_revision=contract["source_revision"] if contract else None)
        result.append(row)
    return result
