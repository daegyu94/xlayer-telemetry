"""Two visible Runs are evidence; one Run, partial responses and foreign labels are not."""
import importlib.util
from pathlib import Path

import pytest

ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('verify_live_runs', ROOT/'examples/multinode/verify_live_runs.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
QUALITY={'scope':'query','warning_count':0,'info_count':0,'discarded_sample_count':0,'discarded_series_count':0}


@pytest.mark.parametrize('minimum,warning,samples,expected', [(2,0,6,'observed_overlap'),
    (1,0,6,'not_established'),(2,1,6,'not_established'),(2,0,2,'not_established')])
def test_overlapping_query_visibility_preserves_zero_and_partial_evidence(minimum,warning,samples,expected):
    class Client:
        def query_range_detail(self, expression, *args):
            if expression.startswith('count('):
                return {'aggregate':{'min':minimum,'max':2,'sample_count':samples},'series':[], 'result_quality':QUALITY}
            run='A' if 'run_id="A"' in expression else 'B'
            return {'series':[{'labels':{'run_id':run,'cluster':'lab','job':'telemetry',
                    'node':'n','producer':'verl','role':'trainer','worker_id':'driver'},
                    'stats':{'last':0,'min':0,'max':0,'sample_count':3}}],
                    'result_quality':QUALITY|{'warning_count':warning}}
    report=module.verify(Client(),'lab',['A','B'],100,110)
    assert report['status']==expected
    assert all(row['series'][0]['stats']['last']==0 for row in report['observations'])
    assert 'Not proof' in report['limitations'][0]


def test_foreign_run_response_is_rejected_instead_of_counted():
    class Client:
        def query_range_detail(self,*args):
            return {'series':[{'labels':{'run_id':'foreign'}}],'result_quality':QUALITY}
    with pytest.raises(ValueError, match='identity'):
        module.verify(Client(),'lab',['A','B'],100,110)


@pytest.mark.parametrize('runs,start,end,step', [(['A','A'],100,110,2), (['A','B'],110,100,2),
    (['A','B'],100,float('inf'),2),(['A','B'],100,3701,2),(['A','B'],100,110,0)])
def test_invalid_window_or_identity_never_queries(runs,start,end,step):
    class Client:
        def query_range_detail(self,*args):pytest.fail('invalid input queried backend')
    with pytest.raises(ValueError):module.verify(Client(),'lab',runs,start,end,step)
