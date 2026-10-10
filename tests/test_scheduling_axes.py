import json
from xlayer_telemetry.time_alignment import CalibrationCache
from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine,run_once,_prepare_batch
from tests.test_time_alignment import calibration


class Empty:
    def query_range_detail(self,*args):return {'aggregate':None,'series':[]}


def setup(tmp_path,settle=0):
    raw=[220.];mono=[50.]
    path=tmp_path/'clock.json'
    payload=calibration();payload.update(local_anchor=220,monotonic_anchor=50,offset_seconds=-120,valid_from=200,valid_until=220.5)
    path.write_text(json.dumps(payload))
    cache=CalibrationCache(path,node='gpu-a',wall_clock=lambda:raw[0],monotonic=lambda:mono[0],boot_id=lambda:'boot')
    window=cache.project(218,220)
    record={'schema_version':1,'record_type':'verl_step_observation','record_id':'id','run_id':'r','node':'gpu-a',
        'worker_id':'driver','boundary_scope':'rl_step','observed_at':220,'step_duration_seconds':2,'analysis_window':window}
    history=tmp_path/'history.jsonl';history.write_text(json.dumps(record)+'\n')
    engine=DiagnosticEngine({'run_id':'r','node':'gpu-a','prometheus':{'url':'unused'},'retry_seconds':60,'retry_interval_seconds':10,
        'threefs':{'settle_seconds':settle,'url':'http://unused'} if settle else {}},prometheus=Empty(),threefs=object() if settle else None,
        clock=lambda:raw[0],elapsed_clock=lambda:mono[0],time_calibration=cache)
    return engine,history,tmp_path/'diagnostics',raw,mono,path,payload


def renew(path,payload,raw,mono):
    payload.update(local_anchor=raw[0],monotonic_anchor=mono[0],valid_from=raw[0]-1,valid_until=raw[0]+60)
    path.write_text(json.dumps(payload))


def test_retry_does_not_expire_on_calibration_expiry_or_recovery(tmp_path):
    engine,history,output,raw,mono,path,payload=setup(tmp_path)
    assert engine.clock()==100
    assert run_once(engine,history,output,periodic_when_idle=False)==1
    raw[0]=221;mono[0]=51
    assert engine.clock()==221
    assert run_once(engine,history,output,periodic_when_idle=False)==0
    raw[0]=230;mono[0]=60
    assert run_once(engine,history,output,periodic_when_idle=False)==1
    assert json.loads((output/'latest.json').read_text())['analysis_status']=='provisional'
    raw[0]=231;mono[0]=61;renew(path,payload,raw,mono)
    assert engine.clock()==111
    assert run_once(engine,history,output,periodic_when_idle=False)==0
    raw[0]=280;mono[0]=110;renew(path,payload,raw,mono)
    assert run_once(engine,history,output,periodic_when_idle=False)==1
    assert json.loads((output/'latest.json').read_text())['analysis_status']=='final'


def test_settling_is_elapsed_time_across_expiry_and_recovery(tmp_path):
    engine,history,output,raw,mono,path,payload=setup(tmp_path,settle=30)
    assert not _prepare_batch(engine,history,output,periodic_when_idle=False)[0]['pending']
    raw[0]=221;mono[0]=51
    assert not _prepare_batch(engine,history,output,periodic_when_idle=False)[0]['pending']
    raw[0]=235;mono[0]=65;renew(path,payload,raw,mono)
    assert not _prepare_batch(engine,history,output,periodic_when_idle=False)[0]['pending']
    raw[0]=250;mono[0]=80
    assert len(_prepare_batch(engine,history,output,periodic_when_idle=False)[0]['pending'])==1
