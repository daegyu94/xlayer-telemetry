"""Existing SDK can record explicit bytes without asserting physical I/O."""
import json

from examples.application.checkpoint_io import record
from xlayer_telemetry.fileio import json_objects


def test_cpu_checkpoint_records_actual_payload_duration_and_outcome(tmp_path):
    root=tmp_path/'checkpoint'
    record(root,payload=b'known logical bytes')
    snapshot=json.loads(next((root/'telemetry-metrics').glob('*.json')).read_text())
    values={metric['name']:metric['value'] for metric in snapshot['samples']}
    assert values['checkpoint_size_bytes']==19
    assert values['checkpoint_save_time_seconds']>0 and values['checkpoint_load_time_seconds']>0
    assert abs(values['checkpoint_write_throughput_bytes_per_second']*values['checkpoint_save_time_seconds']-19)<1e-9
    spans=list(json_objects(next((root/'telemetry-events').glob('*.jsonl'))))
    assert {span['name'] for span in spans}=={'checkpoint.save','checkpoint.load'}
    assert all(span['status']=='ok' and span['attributes']['logical_bytes']==19 for span in spans)
    assert all('checkpoint_uri' not in metric.get('labels',{}) for metric in snapshot['samples'])
