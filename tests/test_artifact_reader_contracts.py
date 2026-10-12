"""Keep byte limits, codecs and empty-vs-unreadable snapshot behavior."""
import json

import pytest

from xlayer_telemetry.fileio import read_json_object
from xlayer_telemetry.operations.runs import _object
from xlayer_telemetry.show_run import _load


@pytest.mark.parametrize('extra', [-1, 0, 1])
def test_snapshot_limit_counts_bytes_and_preserves_measured_zero(tmp_path, extra):
    path = tmp_path / 'snapshot.json'
    body = json.dumps({'name': '측정', 'value': 0}, ensure_ascii=False).encode()
    path.write_bytes(body)
    result = _object(path, max_bytes=len(body) + extra)
    assert result == ({} if extra < 0 else {'name': '측정', 'value': 0})


@pytest.mark.parametrize('body', [b'[]', b'{"partial":', b'\xff', b'[' * 10000 + b'0' + b']' * 10000])
def test_unreadable_or_non_object_input_keeps_each_reader_fallback(tmp_path, body):
    path = tmp_path / 'snapshot.json'; path.write_bytes(body)
    assert read_json_object(path) is None
    assert _object(path) == {} and _load(path) is None


@pytest.mark.parametrize('codec', ['utf-8-sig', 'utf-16'])
def test_existing_binary_reader_codec_does_not_change_text_reader_policy(tmp_path, codec):
    path = tmp_path / 'snapshot.json'
    path.write_bytes('{"value":0}'.encode(codec))
    assert _object(path) == {'value': 0}
    assert _load(path) is None


def test_empty_object_is_distinct_from_missing_snapshot(tmp_path):
    path = tmp_path / 'snapshot.json'
    assert _load(path) is None
    path.write_text('{}')
    assert _load(path) == {}
