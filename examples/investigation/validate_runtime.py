"""Measure cold/warm JSONL parsing with bounded caches on local CPU fixtures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import time
import tracemalloc

from xlayer_telemetry.analysis.diagnostics import load_history, tool_span_window
from xlayer_telemetry.analysis.jsonl_cache import JSONLCache
from xlayer_telemetry.metrics.textfile import SnapshotCache, collect_snapshots


def measure_snapshots(root, count=1000, polls=5):
    root.mkdir(parents=True, exist_ok=True)
    for worker in range(count):
        (root / f"{worker}.json").write_text(json.dumps({
            "schema_version": 2, "run_id": "fixture", "node": "cpu", "producer": "app",
            "role": "worker", "worker_id": str(worker), "observed_at": 100,
            "samples": [{"name": f"signal_{i}", "value": i, "labels": {"phase": "rollout"}}
                        for i in range(64)]}))
    def run(cache, counters):
        times = []
        for _ in range(polls):
            started = time.perf_counter()
            values = collect_snapshots([root], [], cache=cache, counters=counters, now=101)
            times.append(time.perf_counter()-started)
        return values, statistics.median(times)
    reads, hits = {}, {}
    original, uncached = run(None, reads)
    cache = SnapshotCache()
    started = time.perf_counter()
    cold = collect_snapshots([root], [], cache=cache, counters=hits, now=101)
    cold_seconds = time.perf_counter()-started
    cached, warm = run(cache, hits)
    assert original == cold == cached
    assert hits['snapshot_reads'] == count and hits['snapshot_cache_hits'] == count*polls
    # Measure the cache's allocation separately, outside timed polling loops.
    allocation_cache = SnapshotCache()
    tracemalloc.start()
    collect_snapshots([root], [], cache=allocation_cache, now=101)
    retained, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {'files': count, 'polls': polls, 'signals_per_file': 64,
            'uncached_median_seconds': round(uncached, 6), 'cold_seconds': round(cold_seconds, 6),
            'warm_median_seconds': round(warm, 6), 'uncached_file_reads': reads['snapshot_reads'],
            'warm_file_reads': hits['snapshot_reads']-count, 'warm_cache_hits': hits['snapshot_cache_hits'],
            'cache_retained_traced_bytes': retained,
            'limitations': ['Local filesystem fixture; no training throughput claim',
                           'Traced Python allocations are not process RSS',
                           'Directory/stat calls, validation and Prometheus formatting still run']}


def measure(load, path, count):
    start = time.perf_counter()
    original = load(None)
    uncached = time.perf_counter()-start
    cache = JSONLCache()
    start = time.perf_counter()
    cold = load(cache)
    cold_seconds = time.perf_counter()-start
    before = cache.stats()['parsed_bytes']
    start = time.perf_counter()
    warm = load(cache)
    warm_seconds = time.perf_counter()-start
    assert original == cold == warm, 'cache changed query results'
    warm_parsed_bytes = cache.stats()['parsed_bytes'] - before
    return {'records':count,'file_bytes':path.stat().st_size,
            'uncached_seconds':round(uncached,6),'cold_seconds':round(cold_seconds,6),
            'warm_seconds':round(warm_seconds,6),'warm_parsed_bytes':warm_parsed_bytes,
            'warm_parse_reused':warm_parsed_bytes == 0,'cache':cache.stats()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--records',type=int,nargs='+',default=[10_000,100_000])
    args = parser.parse_args()
    if any(count < 1 or count > 100_000 for count in args.records):
        parser.error('--records must be between 1 and the default cache limit of 100000')
    args.output.mkdir(parents=True,exist_ok=True)
    rows = []
    for count in args.records:
        root = args.output/str(count)
        root.mkdir(exist_ok=True)
        history = root/'history.jsonl'
        events_dir = root/'telemetry-events'
        events_dir.mkdir(exist_ok=True)
        events = events_dir/'agent-events.jsonl'
        with history.open('w') as records, events.open('w') as spans:
            for step in range(count):
                records.write(json.dumps({'schema_version':1,'record_type':'verl_step_observation',
                    'record_id':str(step),'run_id':'fixture','node':'cpu','worker_id':'driver',
                    'observed_at':step+1,'step_duration_seconds':1})+'\n')
                spans.write(json.dumps({'schema_version':1,'record_type':'span','name':'tool.call',
                    'run_id':'fixture','status':'ok','trace_id':str(step),'span_id':str(step),
                    'attributes':{'tool':'pytest'},'start_time_unix_nano':step*10**9,
                    'end_time_unix_nano':(step+1)*10**9,'duration_seconds':1})+'\n')
        rows.append({'history':measure(lambda cache:load_history(history,cache=cache),history,count),
                     'tool_spans':measure(lambda cache:tool_span_window(events_dir,'fixture',0,count,
                                         cache=cache),events,count)})
    record = {'data_origin':'synthetic','scope':'local JSONL and snapshot read/parse cost',
              'measurements':rows,'snapshots':measure_snapshots(args.output/'snapshots'),
              'limitations':['No network queries or full diagnosis latency measured',
              'Cache still iterates retained records; this is not an indexed time-window lookup',
              'Overflow uses full scans; memory budgets are shared across cached files in an engine']}
    (args.output/'summary.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record,indent=2))


if __name__ == '__main__':
    main()
