"""Real SQL on a disposable container; no existing 3FS data is accessed."""
import json
import os
import subprocess
import uuid

import pytest

from xlayer_telemetry.analysis.diagnostics import ThreeFSClient

CONTAINER = os.environ.get('XLAYER_TEST_CLICKHOUSE_CONTAINER')
pytestmark = pytest.mark.skipif(not CONTAINER, reason='owned ClickHouse not configured')
LABELS = ('host','tag','mount_name','instance','io','uid','pod','thread','statusCode')


@pytest.fixture
def backend():
    database='xlayer_series_'+uuid.uuid4().hex
    def execute(query,data=None):
        return subprocess.run(['docker','exec','-i',CONTAINER,'clickhouse-client','--multiquery','--query',query],
            input=data,text=True,capture_output=True,check=True,timeout=15).stdout
    execute('CREATE DATABASE '+database)
    try:
        fields=', '.join(name+' String' for name in LABELS)
        execute(f'CREATE TABLE {database}.distributions (TIMESTAMP DateTime, metricName String, {fields}, method String, `count` Float64, mean Float64, `max` Float64,p99 Float64) ENGINE Memory')
        execute(f'CREATE TABLE {database}.counters (TIMESTAMP DateTime, metricName String, {fields}, val Int64) ENGINE Memory')
        class Client(ThreeFSClient):
            def _query_rows(self,query):
                return [json.loads(line) for line in execute(query).splitlines()]
        def insert(table,stamp,name,**values):
            row={key:'' for key in LABELS};row.update(TIMESTAMP=stamp,metricName=name,host='host-a',instance='batchRead',**values)
            if table=='distributions':row.setdefault('method','read')
            execute(f'INSERT INTO {database}.{table} FORMAT JSONEachRow',json.dumps(row)+'\n')
        yield Client('http://unused',database=database),insert,execute
    finally:
        execute('DROP DATABASE '+database)


def test_same_second_distributions_preserve_positive_weight_and_report_max(backend):
    client,insert,_=backend
    for count,mean,maximum,p99 in [(2,10,20,19),(1,4,15,11),(0,999,999,999)]:
        insert('distributions',91,'storage_client.overall_latency',count=count,mean=mean,max=maximum,p99=p99)
    row,=client.query_distribution_series(90.1,91.1)
    assert row['timestamp_seconds']==91 and row['count']==3 and row['weighted_mean']==8
    assert row['max_observed_p99']==19 and row['report_count']==2
    assert row['labels']['host']=='host-a' and row['labels']['method']=='read'
    assert client.query_distribution_series(91.1,91.8)==[]


def test_reset_reports_gauges_unknown_and_zero_have_distinct_real_sql_results(backend):
    client,insert,_=backend
    names=['storage_client.data_payload_bytes','storage_client.num_update_channels.inuse','custom-gauge']
    for name in names:
        for val in (5,7):insert('counters',91,name,val=val)
        insert('counters',94,name,val=0)
    rows=client.query_counter_series(90,95)
    for row in rows:
        if row['timestamp_seconds']==91:
            assert row['value'] is None and row['last'] is None and row['ambiguous_sample']
            assert row['observed_sum']==(12 if row['metricName']==names[0] else None)
        else:
            assert row['value']==0 and not row['ambiguous_sample']
    assert {row['timestamp_seconds'] for row in rows}=={91,94}


def test_series_overflow_rejects_without_silent_truncation(backend):
    client,insert,_=backend
    for stamp in range(100,104):insert('counters',stamp,'storage_client.data_payload_bytes',val=5)
    with pytest.raises(ValueError,match='point'):
        client.query_counter_series(99,105,max_points=3)
