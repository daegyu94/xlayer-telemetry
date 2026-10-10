"""Independent JSONL producer and saved-catalog reader during cleanup/rotation."""
import json
import multiprocessing
from pathlib import Path

from tests.test_run_explorer import saved
from xlayer_telemetry.operations import runs
from xlayer_telemetry.step_history import StepHistoryWriter


def producer(path, stop, ready):
    path=Path(path); i=0
    while not stop.is_set():
        path.unlink(missing_ok=True)
        writer=StepHistoryWriter(path,run_id='a',node='gpu-a',worker_id='driver')
        writer.append({'step':i,'data':{'perf/time_per_step':1,'timing_s/gen':.5,'perf/total_num_tokens':100}})
        ready.set()
        with path.open('a') as stream: stream.write('{incomplete')
        i+=1


def test_real_writer_rotation_cannot_interrupt_peer_catalog_or_publish(tmp_path):
    saved(tmp_path/'runs','a'); saved(tmp_path/'runs','b')
    context=multiprocessing.get_context('spawn')
    stop,ready=context.Event(),context.Event()
    child=context.Process(target=producer,args=(str(tmp_path/'runs/a/telemetry-events/verl-steps.jsonl'),stop,ready))
    child.start()
    try:
        assert ready.wait(5)
        for _ in range(25):
            result=runs.catalog(tmp_path/'runs')
            peer=next(row for row in result['runs'] if row['run_id']=='b')
            assert peer['average_step']==10 and peer['quality']=='complete'
            runs.publish(tmp_path/'runs',tmp_path/'dashboards')
            data=json.loads((tmp_path/'dashboards/xlayer-run-catalog.json').read_text())
            assert any(row['run_id']=='b' for row in data['xlayerRunCatalog']['runs'])
    finally:
        stop.set(); child.join(5)
        if child.is_alive(): child.terminate(); child.join(5)
    assert child.exitcode==0
