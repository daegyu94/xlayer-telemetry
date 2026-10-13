"""Explicit deterministic collection-report fixtures; not a real 3FS backend."""
import math

from ..analysis.storage_series import collect_storage_series


def synthetic_storage_series(current_window, baseline_window, *, slow=True):
    labels={name:'' for name in ('host','tag','mount_name','instance','io','uid','method','pod','thread','statusCode')}
    labels.update(host='synthetic-storage',instance='batchRead',method='read')
    class Source:
        def query_distribution_series(self,start,end,**kwargs):
            stamps=list(range(math.ceil(start),math.ceil(end)))
            degraded=slow and start==current_window['start']
            return [{'timestamp_seconds':stamp,'metricName':'storage_client.overall_latency','labels':dict(labels),
                     'count':4,'weighted_mean':p99*.6*1000000,'max':(p99+2)*1000000,
                     'max_observed_p99':p99*1000000,
                     'report_count':1} for i,stamp in enumerate(stamps) if i%7!=4
                    for p99 in [8+(i%5)*.6+(26*math.sin(math.pi*i/max(1,len(stamps)-1))**2 if degraded else 0)]]
        def query_counter_series(self,start,end,**kwargs):
            counter_labels={k:v for k,v in labels.items() if k!='method'}
            payloads=[{'timestamp_seconds':stamp,'metricName':'storage_client.data_payload_bytes','labels':counter_labels,
                     'sample_count':1,'min':65536,'max':65536,'last':65536,'value':65536,'observed_sum':65536,
                     'ambiguous_sample':False,'kind':'reset_on_collect','unit':'bytes'}
                    for stamp in range(math.ceil(start),math.ceil(end)) if stamp%5!=2]
            # This queue name is an illustrative fixture, not a deployed 3FS contract.
            stamps=list(range(math.ceil(start),math.ceil(end)))
            degraded=slow and start==current_window['start']
            queues=[{'timestamp_seconds':stamp,'metricName':'demo.storage.pending_requests','labels':counter_labels,
                     'sample_count':1,'min':value,'max':value,'last':value,'value':value,
                     'observed_sum':None,'ambiguous_sample':False,'kind':'gauge','unit':'operations'}
                    for i,stamp in enumerate(stamps) if i%7!=4
                    for value in [2+i%3+(round(22*math.sin(math.pi*i/max(1,len(stamps)-1))**2) if degraded else 0)]]
            return payloads+queues
    result=collect_storage_series(Source(),current_window,baseline_window,
        settings={'enabled':True,'distribution_metrics':['storage_client.overall_latency'],
                  'distribution_units':{'storage_client.overall_latency':'ns'},
                  'counter_metrics':['storage_client.data_payload_bytes','demo.storage.pending_requests'],'max_points':500},
        clock_quality={'status':'unknown'},queried_at=current_window['end'])
    result['data_origin']='synthetic'
    return result
