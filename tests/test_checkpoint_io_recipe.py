"""Existing SDK can record explicit bytes without asserting physical I/O."""
import json
import pytest

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


@pytest.mark.parametrize('size', [0, 4096, 65536])
@pytest.mark.parametrize('fail_second', [False, True])
def test_two_checkpoint_producers_keep_logical_bytes_identity_and_failure(tmp_path, monkeypatch, size, fail_second):
    """CPU file I/O + actual SDK, without a physical/shared-storage claim."""
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    import threading
    original=Path.write_bytes
    rendezvous=threading.Barrier(2)
    def write(path, payload):
        if path.name=='checkpoint.bin':
            rendezvous.wait(timeout=5)
            if fail_second and path.parent.name=='job-b':
                raise OSError('injected checkpoint write failure')
        return original(path,payload)
    monkeypatch.setattr(Path,'write_bytes',write)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures={run:executor.submit(record,tmp_path/run,run_id=run,payload=b'x'*size) for run in ('job-a','job-b')}
        for run,future in futures.items():
            if fail_second and run=='job-b':
                with pytest.raises(OSError,match='injected checkpoint'):future.result(timeout=10)
            else:future.result(timeout=10)
    for run in ('job-a','job-b'):
        root=tmp_path/run
        spans=[row for path in (root/'telemetry-events').glob('*.jsonl') for row in json_objects(path)]
        assert spans and {row['run_id'] for row in spans}=={run}
        assert all(row['attributes']['logical_bytes']==size for row in spans)
        if fail_second and run=='job-b':
            assert len(spans)==1 and spans[0]['status']=='error'
            assert not list((root/'telemetry-metrics').glob('*.json'))
            continue
        snapshot=json.loads(next((root/'telemetry-metrics').glob('*.json')).read_text())
        assert snapshot['run_id']==run
        values={metric['name']:metric['value'] for metric in snapshot['samples']}
        assert values['checkpoint_size_bytes']==size
        assert all(row['status']=='ok' for row in spans)
        for operation,direction in [('save','write'),('load','read')]:
            assert values[f'checkpoint_{operation}_time_seconds']>0
            assert abs(values[f'checkpoint_{direction}_throughput_bytes_per_second']*
                       values[f'checkpoint_{operation}_time_seconds']-size)<1e-7
        assert all('checkpoint_uri' not in metric.get('labels',{}) for metric in snapshot['samples'])
