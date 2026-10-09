"""Bounded shared collection observations, not operation/phase attribution."""
from __future__ import annotations

from collections import defaultdict
import json
import math
from typing import Mapping

from ..measurements import finite_number as finite


def validate_series_settings(settings: Mapping) -> dict:
    if not isinstance(settings, Mapping) or set(settings) - {"enabled", "distribution_metrics", "counter_metrics", "distribution_units", "max_points", "host_clock_nodes"}:
        raise ValueError("invalid threefs.time_series settings")
    if type(settings.get("enabled", False)) is not bool:
        raise ValueError("threefs.time_series.enabled must be boolean")
    maximum = settings.get("max_points", 2000)
    if type(maximum) is not int or not 1 <= maximum <= 2000:
        raise ValueError("threefs.time_series.max_points must be 1..2000")
    for key in ("distribution_metrics", "counter_metrics"):
        names = settings.get(key, [])
        if (not isinstance(names, list) or len(names) > 16
                or any(not isinstance(name, str) or not name or len(name.encode()) > 256 for name in names)
                or len(set(names)) != len(names)):
            raise ValueError(f"threefs.time_series.{key} needs at most 16 unique metric names")
    units = settings.get("distribution_units", {})
    if not isinstance(units, Mapping) or any(not isinstance(key, str) or value not in {"ns", "us", "ms", "s", "bytes", "operations"} for key, value in units.items()):
        raise ValueError("threefs.time_series.distribution_units needs explicit supported units")
    clocks = settings.get("host_clock_nodes", {})
    if not isinstance(clocks, Mapping) or len(clocks) > 32 or any(not isinstance(key, str) or not key or not isinstance(value, str) or not value for key, value in clocks.items()):
        raise ValueError("threefs.time_series.host_clock_nodes needs explicit host/node identities")
    return dict(settings)


def _quality(window, rows, clock, settings, queried_at):
    hosts = sorted({row.get("labels", {}).get("host") for row in rows if row.get("labels", {}).get("host")})
    aliases = settings.get("host_clock_nodes", {})
    # A mapped workload clock says nothing about uncalibrated ClickHouse
    # producer timestamps. Only their raw OS/producer screening is usable.
    nodes = (clock.get("system_clock_screening", {}).get("nodes", {})
             if clock.get("scope") == "mapped_workload_to_prometheus_scrape_time" else clock.get("nodes", {}))
    nodes = {**nodes, **clock.get("producer_clock_screening", {}).get("nodes", {})}
    aligned = bool(hosts) and clock.get("status") == "aligned" and all(
        aliases.get(host) in nodes and nodes[aliases[host]].get("status") == "aligned" for host in hosts)
    issues = ["collection_interval_unknown", "zero_suppression_or_loss_not_distinguishable"]
    if window["end"]-window["start"] < 1:
        issues.append("interval_shorter_than_timestamp_resolution")
    if not aligned:
        issues.append("source_host_clock_alignment_unknown")
    if window.get("accuracy") not in {"exact", "calibrated"}:
        issues.append("approximate_or_unknown_workload_boundary")
    timestamps = [finite(row.get("timestamp_seconds")) for row in rows]
    timestamps = [stamp for stamp in timestamps if stamp is not None]
    last = max(timestamps) if timestamps else None
    return {"timestamp_resolution_seconds": 1, "timestamp_kind": "producer_collection_report_seconds",
            "collection_interval": "unknown", "coverage": "returned_reports_only",
            "phase_attribution": "not_established", "host_clock_coverage": "screened_aligned" if aligned else "unknown",
            "hosts": hosts, "clock_status": clock.get("status", "unknown"),
            "boundary_accuracy": window.get("accuracy", "unknown"),
            "last_report_timestamp_seconds": last,
            "report_age_seconds": queried_at-last if last is not None and queried_at >= last else None,
            "issues": issues}


def distribution_comparison_quality(current, baseline, current_window, baseline_window):
    """Compare exposure/population of reported extrema, not complete coverage.

    Inputs are one entity's second-level series or one window aggregate with
    report_count/observed_second_count. Operation sample counts are different.
    """
    result = {'complete_collection_coverage': 'unknown'}
    for role, rows, window in (('current', current, current_window), ('baseline', baseline, baseline_window)):
        start, end = finite(window.get('start')), finite(window.get('end'))
        valid = start is not None and end is not None and start < end
        result[role + '_interval_seconds'] = end - start if valid else None
        result[role + '_timestamp_slots'] = math.ceil(end) - math.ceil(start) if valid else None
        counts = [finite(row.get('report_count')) for row in rows]
        result[role + '_report_count'] = sum(counts) if counts and all(
            value is not None and value > 0 and value == int(value) for value in counts) else None
        stamps = [finite(row.get('timestamp_seconds')) for row in rows]
        if stamps and all(stamp is not None for stamp in stamps):
            seconds = len(set(stamps))
        else:
            seconds = finite(rows[0].get('observed_second_count')) if len(rows) == 1 else None
        result[role + '_reported_seconds'] = seconds if seconds is not None and seconds > 0 and seconds == int(seconds) else None
    durations = [result[role + '_interval_seconds'] for role in ('current', 'baseline')]
    if any(value is None for value in durations):
        status = 'report_window_unknown'
    elif min(durations) < 1:
        status = 'insufficient_timestamp_resolution'
    elif (not math.isclose(*durations, rel_tol=0, abs_tol=1e-6)
          or result['current_timestamp_slots'] != result['baseline_timestamp_slots']):
        status = 'different_report_window_exposure'
    elif any(result[role + field] is None for role in ('current', 'baseline')
             for field in ('_report_count', '_reported_seconds')):
        status = 'report_population_unknown'
    elif (result['current_report_count'] != result['baseline_report_count']
          or result['current_reported_seconds'] != result['baseline_reported_seconds']):
        status = 'different_report_population'
    else:
        status = 'shared_report_window'
    return {**result, 'status': status}


def collect_storage_series(client, current_window, baseline_window, *, settings, clock_quality, queried_at):
    settings = validate_series_settings(settings)
    result = {"enabled": settings.get("enabled", False), "max_points": settings.get("max_points", 2000),
              "distribution_units": dict(settings.get("distribution_units", {})),
              "interpretation": "shared collection context; not operation ownership, complete I/O totals or phase utilization",
              "comparison": {"eligible": False, "rows": []}}
    if not result["enabled"]:
        return result
    remaining = result["max_points"]
    for role, window in (("current", current_window), ("baseline", baseline_window)):
        if not isinstance(window, Mapping) or finite(window.get("start")) is None or finite(window.get("end")) is None or window["start"] >= window["end"]:
            result[role] = {"distribution_status": "missing_window", "counter_status": "missing_window", "distributions": [], "counters": []}
            continue
        entry = {"window": dict(window), "distributions": [], "counters": [], "errors": []}
        for category, method, metric_key in (("distributions", "query_distribution_series", "distribution_metrics"), ("counters", "query_counter_series", "counter_metrics")):
            status_key = "distribution_status" if category == "distributions" else "counter_status"
            names = settings.get(metric_key, [])
            if not names:
                entry[status_key] = "not_configured"
                continue
            if client is None:
                entry[status_key] = "not_configured"
                continue
            if remaining <= 0:
                entry[status_key] = "budget_exhausted"
                continue
            try:
                rows = getattr(client, method)(float(window["start"]), float(window["end"]), metric_names=names, max_points=remaining)
                if len(rows) > remaining:
                    raise ValueError("storage series point budget exceeded")
                entry[category] = rows
                remaining -= len(rows)
                entry[status_key] = "observed" if rows else "no_data"
            except (OSError, RuntimeError, ValueError, AttributeError) as error:
                entry[status_key] = "query_failed"
                entry["errors"].append(f"{category}:{type(error).__name__}")
        clock = clock_quality.get("baseline", {}) if role == "baseline" else clock_quality
        entry["quality"] = _quality(window, entry["distributions"]+entry["counters"], clock, settings, queried_at)
        result[role] = entry
    current, baseline = result.get("current", {}), result.get("baseline", {})
    eligible = all(entry.get("quality", {}).get("host_clock_coverage") == "screened_aligned" for entry in (current, baseline))
    result["comparison"]["eligible"] = eligible
    windows = [entry.get("window", {}) for entry in (current, baseline)]
    equal_exposure = all("start" in window and "end" in window for window in windows) and (
        math.isclose(windows[0]["end"]-windows[0]["start"], windows[1]["end"]-windows[1]["start"], abs_tol=1e-6)
        and math.ceil(windows[0]["end"])-math.ceil(windows[0]["start"]) == math.ceil(windows[1]["end"])-math.ceil(windows[1]["start"]))
    # Keep exact table+metric+producer population; window statistics are not
    # pooled quantiles or complete operation totals, even with aligned clocks.
    def groups(entry):
        grouped = defaultdict(list)
        for table in ("distributions", "counters"):
            for row in entry.get(table, []):
                key = (table, row["metricName"], tuple(sorted(row["labels"].items())))
                grouped[key].append(row)
        return grouped
    a, b = groups(current), groups(baseline)
    for key in sorted(a.keys() & b.keys()):
        table, metric, labels = key
        unit = settings.get("distribution_units", {}).get(metric) if table == "distributions" else a[key][0].get("unit")
        status = "shared_report_window" if eligible else "clock_unverified"
        comparison_quality = None
        if table == "distributions":
            values = [[finite(row.get("max_observed_p99")) for row in group] for group in (a[key], b[key])]
            statistic = "maximum reported p99, not pooled p99"
            if any(not group or any(value is None for value in group) for group in values):
                continue
            now, before = max(values[0]), max(values[1])
            comparison_quality = distribution_comparison_quality(a[key], b[key], windows[0], windows[1])
            if eligible:
                status = comparison_quality['status']
        elif a[key][0].get("kind") in {"reset_on_collect", "reset_after_collect", "interval_delta"}:
            values = [[finite(row.get("observed_sum")) for row in group] for group in (a[key], b[key])]
            statistic = "sum returned reset reports; incomplete I/O total"
            if any(not group or any(value is None for value in group) for group in values):
                continue
            now, before = sum(values[0]), sum(values[1])
            if eligible and not equal_exposure:
                status = "different_report_window_exposure"
        else:
            continue
        result["comparison"]["rows"].append({"metricName": metric, "labels": dict(labels), "table": table,
            "current": now, "baseline": before, "delta": now-before if status == "shared_report_window" else None,
            "delta_percent": 100*(now-before)/abs(before) if status == "shared_report_window" and before else None,
            "unit": unit, "statistic": statistic, "comparison_status": status,
            **({'comparison_quality': comparison_quality} if comparison_quality is not None else {}),
            "host_clock_coverage": "screened_aligned" if eligible else "unknown"})
    return result


def project_storage_series(series, common):
    rows = []
    for role in ("current", "baseline"):
        entry = series.get(role, {})
        quality = entry.get("quality", {})
        for table in ("distributions", "counters"):
            for point in entry.get(table, []):
                reset = point.get("kind") in {"reset_on_collect", "reset_after_collect", "interval_delta"}
                value = point.get("max_observed_p99") if table == "distributions" else point.get("observed_sum") if reset else point.get("value")
                unit = point.get("unit")
                if table == "distributions":
                    unit = series.get("distribution_units", {}).get(point["metricName"])
                entity = ",".join(f"{key}={value}" for key, value in sorted(point["labels"].items()))
                rows.append({**common, "row_kind": "storage_sample", "window_role": role,
                    "source_table": table, "metric_name": point["metricName"], "signal": point["metricName"],
                    "sample_timestamp_ms": point["timestamp_seconds"]*1000,
                    "sample_value": value, "unit": unit, "entity": entity, "source_host": point["labels"].get("host"),
                    "series_key": json.dumps([table, point['metricName'], sorted(point['labels'].items())], ensure_ascii=False, separators=(",", ":")),
                    "observation_scope": "shared-service",
                    "observation_type": "collection_report", "timestamp_resolution_seconds": 1,
                    "collection_interval": "unknown", "host_clock_coverage": quality.get("host_clock_coverage", "unknown"),
                    "phase_attribution": "not_established", "counter_kind": point.get("kind"),
                    "quality_issues": ", ".join(quality.get("issues", [])),
                    "statistic": "maximum reported p99" if table == "distributions" else "sum returned reset reports" if reset else "raw gauge/unknown report",
                    "report_count": point.get("report_count", point.get("sample_count")),
                    "ambiguous_sample": point.get("ambiguous_sample", False)})
                rows[-1]["plot_eligible"] = value is not None and (reset or not point.get("ambiguous_sample", False))
    return rows


def apply_collection_limits(candidates):
    """Keep demo and production evidence subject to the same source limits."""
    for candidate in candidates:
        if any(str(item.get("signal", "")).startswith("threefs_") for item in candidate.get("evidence", [])):
            missing = candidate.setdefault("missing_evidence", [])
            issue = "threefs_collection_interval_and_complete_operation_coverage"
            if issue not in missing:
                missing.append(issue)
            if candidate["state"] == "strong_signal":
                candidate["state"] = "supporting_signal"


def project_storage_summary(series, common):
    if not series.get("enabled"):
        return []
    rows = []
    for role in ("current", "baseline"):
        entry = series.get(role, {})
        quality = entry.get("quality", {})
        rows.append({**common, "row_kind": "storage_series_status", "window_role": role,
            "distribution_status": entry.get("distribution_status", "missing_window"),
            "counter_status": entry.get("counter_status", "missing_window"),
            "point_count": len(entry.get("distributions", []))+len(entry.get("counters", [])),
            "clock_status": quality.get("clock_status", "unknown"),
            "host_clock_coverage": quality.get("host_clock_coverage", "unknown"),
            "collection_interval": "unknown", "coverage": "returned_reports_only",
            "collection_interval_seconds": None,
            "quality_issues": ", ".join(quality.get("issues", [])+entry.get("errors", [])),
            "phase_attribution": "not_established"})
    for row in series.get("comparison", {}).get("rows", []):
        comparison_quality = row.get('comparison_quality', {})
        rows.append({**common, "row_kind": "storage_comparison", "metric_name": row["metricName"],
            "source_table": row["table"], "current": row["current"], "baseline": row["baseline"],
            "delta_percent": row["delta_percent"], "unit": row["unit"], "statistic": row["statistic"],
            "comparison_status": row["comparison_status"],
            "comparison_quality": json.dumps(comparison_quality, separators=(',', ':')),
            **{key: comparison_quality.get(key) for key in ('current_report_count', 'baseline_report_count',
                'current_reported_seconds', 'baseline_reported_seconds', 'current_interval_seconds', 'baseline_interval_seconds')},
            "clock_status": row.get("host_clock_coverage", "screened_aligned" if row["comparison_status"] == "shared_report_window" else "unknown"),
            "host_clock_coverage": row.get("host_clock_coverage", "screened_aligned" if row["comparison_status"] == "shared_report_window" else "unknown"),
            "quality_issues": "collection_interval_unknown, returned_reports_only" + (
                ', ' + comparison_quality['status'] if comparison_quality.get('status') not in {None, 'shared_report_window'} else ''),
            "entity": ",".join(f"{key}={value}" for key, value in sorted(row["labels"].items())),
            "observation_scope": "shared-service", "phase_attribution": "not_established"})
    return rows
