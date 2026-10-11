"""Exercise the same fault schedule used by the Docker CPU integration entrypoint."""
from datetime import datetime,timezone
import json

import pytest
from examples.multinode.validate_cpu_contracts import exercise_native_faults


def run_fixture(tmp_path, failure=None):
    fault=tmp_path/'fault.json';sequence=[];wall=[100.]
    def mode():
        current=json.loads(fault.read_text())
        name=next(iter(current),'normal')
        if not sequence or sequence[-1]!=name:sequence.append(name)
        return name
    def blocked_recovery(name):
        return failure=='stale_recovery' and name=='normal' and 'delay' in sequence and 'error' not in sequence
    def up(node,job='telemetry'):
        if node=='node-a':return True
        name=mode()
        return name in {'normal','missing'} and not blocked_recovery(name)
    def query(expr):return [1] if mode()=='missing' else [1,2]
    def now():wall[0]+=2;return wall[0]
    def target():
        name=mode();error={'normal':'','missing':'','delay':'context deadline exceeded',
                          'error':'server returned HTTP status 503 Service Unavailable','sample_limit':'sample limit exceeded'}[name]
        stamp=wall[0]+1
        if name=='error' and failure=='stale_error':stamp=wall[0]-1
        if name=='error' and failure=='wrong_error':error='context deadline exceeded'
        if blocked_recovery(name):stamp=wall[0]-1;error='context deadline exceeded'
        return {'lastScrape':datetime.fromtimestamp(stamp,timezone.utc).isoformat(),
                'lastError':error,'health':'up' if up('node-b') else 'down'}
    def wait(callback):
        for _ in range(3):
            result=callback()
            if result:return result
        raise TimeoutError('A fresh scrape with the required outcome never arrived')
    return lambda:exercise_native_faults(fault,query,up,target,wait=wait,now=now),sequence


@pytest.mark.parametrize('failure',['stale_error','wrong_error','stale_recovery'])
def test_old_down_or_wrong_error_cannot_advance_the_fault_schedule(tmp_path,failure):
    exercise,sequence=run_fixture(tmp_path,failure)
    with pytest.raises(TimeoutError):exercise()
    assert 'sample_limit' not in sequence
    if failure=='stale_recovery':assert 'error' not in sequence


def test_fault_schedule_requires_recovery_and_records_each_new_scrape(tmp_path):
    exercise,sequence=run_fixture(tmp_path)
    result=exercise()
    assert sequence==['normal','missing','normal','delay','normal','error','normal','sample_limit','normal']
    assert len(result)==9
    assert [row['mode'] for row in result]==sequence
    assert all(row['last_scrape'] and row['last_error'] for row in result if row['mode'] in {'delay','error','sample_limit'})
