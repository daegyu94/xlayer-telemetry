"""Explicit deterministic collection-report fixtures; not a real 3FS backend."""
import math

from ..analysis.storage_series import collect_storage_series


def synthetic_storage_series(current_window, baseline_window, *, slow=True):
    labels={name:'' for name in ('host','tag','mount_name','instance','io','uid','method','pod','thread','statusCode')}
    labels.update(host='synthetic-storage',instance='batchRead',method='read')
    class Source:
        def query_distribution_series(self,start,end,**kwargs):
            stamps=list(range(math.ceil(start),math.ceil(end)))
            spike=stamps[len(stamps)//2] if stamps else None
            return [{'timestamp_seconds':stamp,'metricName':'storage_client.overall_latency','labels':dict(labels),
                     'count':4,'weighted_mean':8000000,'max':42000000 if slow and start==current_window['start'] and stamp==spike else 10000000,
                     'max_observed_p99':40000000 if slow and start==current_window['start'] and stamp==spike else 8000000,
                     'report_count':1} for i,stamp in enumerate(stamps) if i%7!=4]
        def query_counter_series(self,start,end,**kwargs):
            counter_labels={k:v for k,v in labels.items() if k!='method'}
            return [{'timestamp_seconds':stamp,'metricName':'storage_client.data_payload_bytes','labels':counter_labels,
                     'sample_count':1,'min':65536,'max':65536,'last':65536,'value':65536,'observed_sum':65536,
                     'ambiguous_sample':False,'kind':'reset_on_collect','unit':'bytes'}
                    for stamp in range(math.ceil(start),math.ceil(end)) if stamp%5!=2]
    result=collect_storage_series(Source(),current_window,baseline_window,
        settings={'enabled':True,'distribution_metrics':['storage_client.overall_latency'],
                  'distribution_units':{'storage_client.overall_latency':'ns'},
                  'counter_metrics':['storage_client.data_payload_bytes'],'max_points':500},
        clock_quality={'status':'unknown'},queried_at=current_window['end'])
    result['data_origin']='synthetic'
    return result
