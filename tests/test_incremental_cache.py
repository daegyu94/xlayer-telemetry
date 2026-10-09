"""Cache correctness under append, rotation, partial writes and capacity limits."""
import json
from collections import deque
from pathlib import Path
import tracemalloc

import pytest

from xlayer_telemetry.analysis.jsonl_cache import JSONLCache, from_config
from xlayer_telemetry.analysis.diagnostics import tool_span_window


def test_cache_only_parses_new_complete_records_and_recovers_split_utf8(tmp_path):
    path = tmp_path / 'stream.jsonl'
    first = json.dumps({'value': 1}).encode() + b'\n'
    tail = json.dumps({'value': '한국어'}, ensure_ascii=False).encode() + b'\n'
    split = tail.index('한'.encode()) + 1
    path.write_bytes(first + tail[:split])
    cache = JSONLCache()
    assert list(cache.read(path)) == [{'value': 1}]
    parsed = cache.stats()['parsed_bytes']
    assert list(cache.read(path)) == [{'value': 1}]
    assert cache.stats()['parsed_bytes'] == parsed
    with path.open('ab') as stream:
        stream.write(tail[split:] + b'not-json\n' + b'{"value":3}\n')
    assert list(cache.read(path)) == [{'value': 1}, {'value': '한국어'}, {'value': 3}]
    assert cache.stats()['parsed_bytes'] == path.stat().st_size
    assert cache.stats()['cache_hits'] == 2


def test_cache_invalidates_truncate_replace_rewrite_and_delete(tmp_path):
    path = tmp_path / 'stream.jsonl'
    cache = JSONLCache()
    path.write_text('{"x":10}\n')
    assert list(cache.read(path)) == [{'x': 10}]
    path.write_text('{"x":20}\n')  # Same inode and length, different mtime/ctime.
    assert list(cache.read(path)) == [{'x': 20}]
    path.write_text('{"x":3}\n')
    assert list(cache.read(path)) == [{'x': 3}]
    replacement = tmp_path / 'new'
    replacement.write_text('{"x":40}\n')
    replacement.replace(path)
    assert list(cache.read(path)) == [{'x': 40}]
    path.write_text('{"x":500}\n{"x":600}\n')  # Larger rewrite is not an append.
    assert list(cache.read(path)) == [{'x': 500}, {'x': 600}]
    path.unlink()
    with pytest.raises(FileNotFoundError):
        list(cache.read(path))
    assert cache.stats()['cached_records'] == 0


def test_cache_overflow_preserves_all_evidence_and_bounds_retained_data(tmp_path):
    cache = JSONLCache(max_records=2, max_bytes=100, max_files=1)
    path = tmp_path / 'large.jsonl'
    path.write_text(''.join(json.dumps({'x': n})+'\n' for n in range(4)))
    assert len(list(cache.read(path))) == 4
    assert cache.stats()['cached_records'] == 0
    other = tmp_path / 'other.jsonl'
    other.write_text('{"x":1}\n')
    list(cache.read(other))
    path.write_text('{"x":2}\n')
    assert list(cache.read(path)) == [{'x': 2}]
    assert cache.stats()['cached_files'] == 1
    path.write_text(json.dumps({'x': 'a'*150})+'\n')
    assert len(list(cache.read(path))) == 1
    assert cache.stats()['cached_source_bytes'] == 0


def test_unchanged_cache_read_does_not_allocate_another_record_list(tmp_path):
    path = tmp_path / 'stream.jsonl'
    path.write_text('{"x":1}\n' * 50_000)
    cache = JSONLCache()
    deque(cache.read(path), maxlen=0)
    parsed = cache.stats()['parsed_bytes']
    tracemalloc.start()
    try:
        deque(cache.read(path), maxlen=0)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert cache.stats()['parsed_bytes'] == parsed
    # 50k copied references alone take about 400 KiB on a 64-bit runtime.
    # Leave ample room for the stream, sentinel reads and cache entry.
    assert peak < 100_000


def test_append_during_read_preserves_the_reader_snapshot(tmp_path):
    path = tmp_path / 'stream.jsonl'
    path.write_text('{"x":1}\n{"x":2}\n')
    cache = JSONLCache()
    assert list(cache.read(path)) == [{'x': 1}, {'x': 2}]
    reader = cache.read(path)
    assert next(reader) == {'x': 1}
    with path.open('ab') as stream:
        stream.write(b'{"x":3}\n')
    assert list(cache.read(path)) == [{'x': 1}, {'x': 2}, {'x': 3}]
    assert list(reader) == [{'x': 2}]


def test_cached_tool_lookup_preserves_tool_baseline_filter(tmp_path):
    path = tmp_path / 'agent-tools.jsonl'
    def span(tool, start, duration):
        return {'schema_version': 1, 'record_type': 'span', 'name': 'tool.call',
                'run_id': 'r', 'trace_id': tool, 'span_id': str(start), 'status': 'ok',
                'attributes': {'tool': tool}, 'start_time_unix_nano': start*10**9,
                'end_time_unix_nano': (start+duration)*10**9, 'duration_seconds': duration}
    path.write_text(''.join(json.dumps(row)+'\n' for row in [span('grep',1,2),span('pytest',5,4)]))
    cache = JSONLCache()
    assert tool_span_window(tmp_path,'r',0,10,cache=cache)['tool'] == 'pytest'
    parsed = cache.stats()['parsed_bytes']
    assert tool_span_window(tmp_path,'r',0,10,tool_name='grep',cache=cache)['max'] == 2
    assert cache.stats()['parsed_bytes'] == parsed


@pytest.mark.parametrize('settings', [{'max_records':0},{'enabled':'yes'},{'max_bytes':True}])
def test_cache_config_limits_are_validated(settings):
    with pytest.raises(ValueError):
        from_config({'jsonl_cache': settings})
