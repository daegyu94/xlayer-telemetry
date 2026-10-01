"""Measure cold/warm JSONL parsing with bounded caches on local CPU fixtures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from xlayer_telemetry.analysis.diagnostics import load_history, tool_span_window
from xlayer_telemetry.analysis.jsonl_cache import JSONLCache


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
    assert cache.stats()['parsed_bytes'] == before, 'unchanged file was reparsed'
    return {'records':count,'file_bytes':path.stat().st_size,
            'uncached_seconds':round(uncached,6),'cold_seconds':round(cold_seconds,6),
            'warm_seconds':round(warm_seconds,6),'warm_parsed_bytes':0,'cache':cache.stats()}


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
        events = root/'agent-events.jsonl'
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
                     'tool_spans':measure(lambda cache:tool_span_window(root,'fixture',0,count,
                                         cache=cache),events,count)})
    record = {'data_origin':'synthetic','scope':'local JSONL parse/query cost, each file measured separately',
              'measurements':rows,'limitations':['No network queries or full diagnosis latency measured',
              'Cache still iterates retained records; this is not an indexed time-window lookup',
              'Overflow uses full scans; memory budgets are shared across cached files in an engine']}
    (args.output/'summary.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record,indent=2))


if __name__ == '__main__':
    main()
