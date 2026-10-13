"""A fresh textfile scrape must not refresh a stopped GPU producer."""
from urllib.parse import parse_qs, urlparse
import os
import shutil
import subprocess

import pytest

from xlayer_telemetry.analysis.evidence_quality import check_source, quality, timestamp_query, source_for_entity
from xlayer_telemetry.prometheus import PrometheusClient


@pytest.mark.parametrize('metric', [
    'telemetry_gpu_utilization_percent', 'telemetry_gpu_memory_used_bytes',
    'telemetry_gpu_memory_total_bytes', 'telemetry_gpu_power_watts',
    'telemetry_gpu_temperature_celsius', 'telemetry_gpu_sm_clock_mhz',
    'telemetry_gpu_process_memory_bytes',
])
def test_gpu_source_query_preserves_device_and_scope(metric):
    selector = metric+'{cluster="lab",nodename="worker",gpu="1"}'
    gauge = ('telemetry_gpu_process_sample_timestamp_seconds' if 'process_memory' in metric
             else 'telemetry_gpu_sample_timestamp_seconds')
    assert timestamp_query(selector) == gauge+' + on(cluster, instance, job) group_right() (0 * '+selector+')'


def test_cached_gpu_scrapes_do_not_invent_new_producer_samples(monkeypatch):
    labels = {'cluster': 'lab', 'instance': 'worker', 'job': 'telemetry',
              'nodename': 'worker', 'gpu': '1', 'gpu_uuid': 'GPU-one'}
    def payload(self, request):
        query = parse_qs(urlparse(request.full_url).query)['query'][0]
        # Exporter scrapes at 100/102/104, but the producer stopped at 90.
        values = ['100', '102', '104'] if query.startswith('timestamp(') else ['90']*3
        return {'status': 'success', 'data': {'resultType': 'matrix', 'result': [
            {'metric': labels, 'values': [[stamp, value] for stamp, value in zip([100,102,104],values)]}]}}
    monkeypatch.setattr(PrometheusClient, '_load_payload', payload)
    source = check_source(PrometheusClient('http://unused'), 'telemetry_gpu_utilization_percent{nodename="worker"}',
                          100, 104, 2, per_entity=True)
    assert source['last_source_timestamp'] == 90
    assert source['observed_source_samples'] == 0
    assert source['series'][0]['labels'] == labels
    assert source_for_entity(source, labels)['last_source_timestamp'] == 90
    result = quality('telemetry_gpu_utilization_percent{nodename="worker"}',100,104,2,{},source=source)
    assert result['source_age_seconds'] == 14
    assert 'source_sample_before_interval' in result['warnings']
    assert 'fewer_than_two_observed_source_samples' in result['warnings']


def test_missing_producer_timestamp_remains_unknown(monkeypatch):
    monkeypatch.setattr(PrometheusClient, '_load_payload', lambda *args: {
        'status': 'success', 'data': {'resultType': 'matrix', 'result': []}})
    source = check_source(PrometheusClient('http://unused'), 'telemetry_gpu_utilization_percent',100,104,2)
    assert source['last_source_timestamp'] is None
    assert quality('telemetry_gpu_utilization_percent',100,104,2,{},source=source)['freshness'] == 'unknown'


def test_field_quality_uses_its_own_producer_time_and_preserves_state_identity():
    selector = 'telemetry_gpu_field_status{cluster="lab",gpu="0",field="memory.used",state="unsupported"}'
    assert timestamp_query(selector) == ('telemetry_gpu_field_sample_timestamp_seconds'
        ' + on(cluster, instance, job) group_right() (0 * '+selector+')')


def test_real_field_quality_panel_hides_stale_metadata_despite_fresh_device(tmp_path):
    import json
    from pathlib import Path
    promtool = os.environ.get('PROMTOOL') or shutil.which('promtool')
    if not promtool:
        pytest.skip('real promtool not configured')
    root = Path(__file__).parents[1]
    dashboard = json.loads((root/'examples/dashboards/compute-communication.json').read_text())
    expression = next(p for p in dashboard['panels'] if p['id']==133)['targets'][0]['expr']
    expression = expression.replace('$gpu','.*').replace('$cluster','lab').replace('$node','worker')
    labels = '{cluster="lab",instance="worker",job="telemetry",nodename="worker",gpu="0",field="memory.used",state="unsupported"}'
    spec=tmp_path/'quality-panel.yml'
    spec.write_text('''rule_files: []
evaluation_interval: 1s
tests:
  - interval: 1s
    input_series:
      - series: 'telemetry_gpu_field_status'''+labels+''''
        values: '1+0x60'
      - series: 'telemetry_gpu_field_sample_timestamp_seconds{cluster="lab",instance="worker",job="telemetry",nodename="worker"}'
        values: '0+0x60'
      - series: 'telemetry_gpu_sample_timestamp_seconds{cluster="lab",instance="worker",job="telemetry",nodename="worker"}'
        values: '0+1x60'
    promql_expr_test:
      - expr: '''+repr(expression)+'''
        eval_time: 15s
        exp_samples:
          - labels: 'telemetry_gpu_field_status'''+labels+''''
            value: 1
      - expr: '''+repr(expression)+'''
        eval_time: 31s
        exp_samples: []
''')
    result=subprocess.run([promtool,'test','rules',str(spec)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr


def test_real_promql_keeps_gpu_identity_and_rejects_other_cluster_job_or_exporter(tmp_path):
    promtool = os.environ.get('PROMTOOL') or shutil.which('promtool')
    if not promtool:
        pytest.skip('real promtool not configured')
    expression = timestamp_query('telemetry_gpu_utilization_percent')
    spec = tmp_path/'source-time.yml'
    spec.write_text('''rule_files: []
evaluation_interval: 1s
tests:
  - interval: 1s
    input_series:
      - series: 'telemetry_gpu_utilization_percent{cluster="lab",instance="worker",job="telemetry",gpu="0",gpu_uuid="GPU-A"}'
        values: '0+0x20'
      - series: 'telemetry_gpu_utilization_percent{cluster="lab",instance="worker",job="telemetry",gpu="1",gpu_uuid="GPU-B"}'
        values: '70+0x20'
      - series: 'telemetry_gpu_utilization_percent{cluster="other",instance="worker",job="telemetry",gpu="0"}'
        values: '90+0x20'
      - series: 'telemetry_gpu_utilization_percent{cluster="lab",instance="worker",job="other",gpu="0"}'
        values: '90+0x20'
      - series: 'telemetry_gpu_utilization_percent{cluster="lab",instance="other",job="telemetry",gpu="0"}'
        values: '90+0x20'
      - series: 'telemetry_gpu_sample_timestamp_seconds{cluster="lab",instance="worker",job="telemetry"}'
        values: '12+0x20'
    promql_expr_test:
      - expr: '''+repr(expression)+'''
        eval_time: 15s
        exp_samples:
          - labels: '{cluster="lab",instance="worker",job="telemetry",gpu="0",gpu_uuid="GPU-A"}'
            value: 12
          - labels: '{cluster="lab",instance="worker",job="telemetry",gpu="1",gpu_uuid="GPU-B"}'
            value: 12
''')
    result = subprocess.run([promtool,'test','rules',str(spec)],capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stdout+result.stderr
