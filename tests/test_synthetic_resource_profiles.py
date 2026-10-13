"""Synthetic source diversity, consistency and replay through exporter samples."""
import json
from pathlib import Path

import pytest

from xlayer_telemetry.demos.live import Demo
from xlayer_telemetry.demos.scenario import make_scenario
from xlayer_telemetry.metrics.prometheus import format_gauges

ROOT = Path(__file__).resolve().parents[1]


def capture(demo, monkeypatch, elapsed, *, wall=None):
    monkeypatch.setattr('xlayer_telemetry.demos.live.time.monotonic', lambda: demo.started+elapsed)
    monkeypatch.setattr('xlayer_telemetry.demos.live.time.time', lambda: wall if wall is not None else 1000+elapsed)
    endpoints=[*demo.gpu['gpu_nodes'], *demo.storage['storage_nodes'], *demo.storage.get('metadata_nodes', []), 'dcgm']
    result={}
    for endpoint in endpoints:
        samples=demo.metrics(endpoint)
        assert format_gauges(samples)
        for sample in samples:
            if sample.name.startswith(('node_', 'telemetry_gpu_', 'DCGM_')):
                assert 'run_id' not in sample.labels
                result[(endpoint, sample.name, tuple(sorted(sample.labels.items())))]=sample.value
    return result


def metric(rows, node, name, **labels):
    return rows[(node, name, tuple(sorted(labels.items())))]


def rate(first, last, node, name, seconds=2, **labels):
    return (metric(last,node,name,**labels)-metric(first,node,name,**labels))/seconds


def disks(first, last, demo):
    result={}
    for node in demo.storage['storage_nodes']:
        for index in range(demo.storage['ssds_per_node']):
            device=f'nvme{index}n1'
            values={key:rate(first,last,node,name,device=device) for key,name in {
                'read':'node_disk_read_bytes_total', 'write':'node_disk_written_bytes_total',
                'reads':'node_disk_reads_completed_total', 'writes':'node_disk_writes_completed_total',
                'read_time':'node_disk_read_time_seconds_total', 'write_time':'node_disk_write_time_seconds_total',
                'busy':'node_disk_io_time_seconds_total', 'weighted':'node_disk_io_time_weighted_seconds_total',
            }.items()}
            values['read_latency']=values['read_time']/values['reads']
            values['write_latency']=values['write_time']/values['writes']
            values['queue']=metric(last,node,'node_disk_io_now',device=device)
            result[(node,device)]=values
    return result


def test_storage_device_diversity_and_regression_latency_are_observable(tmp_path,monkeypatch):
    path=tmp_path/'scenario.json'
    path.write_text(json.dumps(make_scenario(start=1000,run_id='phase-demo',node='gpu-node-0')))
    demo=Demo(ROOT/'examples/live-demo',scenario_state=path)
    demo.started=0.
    normal=disks(capture(demo,monkeypatch,2,wall=1002),capture(demo,monkeypatch,4,wall=1004),demo)
    regression=disks(capture(demo,monkeypatch,42,wall=1042),capture(demo,monkeypatch,44,wall=1044),demo)
    assert len(normal)==len(regression)==32
    assert len({row['read'] for row in normal.values()})==32
    assert len({row['read']/row['write'] for row in normal.values()})>16
    assert len({row['read_latency'] for row in normal.values()})>16
    assert len({row['queue'] for row in regression.values()})>4
    for identity,before in normal.items():
        after=regression[identity]
        assert after['read']>before['read']>0
        assert after['busy']>before['busy']>0
        assert after['read_latency']>before['read_latency']>0
        assert after['write_latency']>before['write_latency']>0
        assert after['queue']>before['queue']>=0
        for row in (before,after):
            assert 0<=row['busy']<=1
            assert row['weighted']>=row['busy']
            assert row['read']/row['reads']==pytest.approx(256*1024)
            assert row['write']/row['writes']==pytest.approx(256*1024)


def test_gpu_metrics_and_native_fixture_share_a_bounded_device_model(monkeypatch):
    demo=Demo(ROOT/'examples/live-demo');demo.started=0.
    busy=capture(demo,monkeypatch,1)
    quiet=capture(demo,monkeypatch,41)  # data-wait lowers GPU activity
    names=('telemetry_gpu_utilization_percent','telemetry_gpu_power_watts',
           'telemetry_gpu_temperature_celsius','telemetry_gpu_sm_clock_mhz',
           'telemetry_gpu_memory_used_bytes','telemetry_gpu_process_memory_bytes')
    for name in names:
        values=[value for (_,metric_name,_),value in busy.items() if metric_name==name]
        assert len(values)==32 and len(set(values))>16
    for node in demo.gpu['gpu_nodes']:
        for rank in range(demo.gpu['gpus_per_node']):
            gpu=str(rank)
            for name in names[:4]:
                assert metric(busy,node,name,gpu=gpu)>metric(quiet,node,name,gpu=gpu)
            used=metric(busy,node,'telemetry_gpu_memory_used_bytes',gpu=gpu)
            capacity=metric(busy,node,'telemetry_gpu_memory_total_bytes',gpu=gpu)
            process=metric(busy,node,'telemetry_gpu_process_memory_bytes',gpu=gpu,pid=str(9000+rank),gpu_uuid=f'DEMO-{node}-{rank}')
            assert 0<=process<=used<=capacity
    for name in ('DCGM_FI_DEV_GPU_UTIL','DCGM_FI_PROF_PIPE_TENSOR_ACTIVE','DCGM_FI_PROF_DRAM_ACTIVE',
                 'DCGM_FI_PROF_PCIE_RX_BYTES','DCGM_FI_PROF_PCIE_TX_BYTES','DCGM_FI_DEV_FB_USED'):
        values=[v for (_,n,_),v in busy.items() if n==name]
        assert len(set(values))==8
    for rank in range(8):
        labels={'gpu':str(rank),'UUID':f'SYNTHETIC-DCGM-{rank}'}
        used=metric(busy,'dcgm','DCGM_FI_DEV_FB_USED',**labels)
        free=metric(busy,'dcgm','DCGM_FI_DEV_FB_FREE',**labels)
        assert used+free==pytest.approx(288*1024)  # Native MiB, sampler bytes.
        for name in ('DCGM_FI_PROF_PIPE_TENSOR_ACTIVE','DCGM_FI_PROF_DRAM_ACTIVE'):
            assert 0<=metric(busy,'dcgm',name,**labels)<=1
        assert metric(busy,'dcgm','DCGM_FI_DEV_XID_ERRORS',**labels)==0


def test_gpu_differences_remain_visible_after_panel_rounding():
    from xlayer_telemetry.demos.profiles import gpu_profile
    for load in (8,61,94,100):
        for node in range(4):
            patterns=[]
            for elapsed in (0,4,12,24):
                values=[gpu_profile(elapsed,node,4,gpu,load)['utilization'] for gpu in range(8)]
                assert all(0<=value<=100 for value in values)
                assert len({round(value) for value in values})>=3
                assert max(values)-min(values)>=load*.15
                patterns.append(values)
            assert all(len({pattern[gpu] for pattern in patterns})>1 for gpu in range(8))


def test_role_specific_host_network_and_rdma_rates_keep_native_invariants(monkeypatch):
    demo=Demo(ROOT/'examples/live-demo');demo.started=0.
    first,last=capture(demo,monkeypatch,1),capture(demo,monkeypatch,3)
    nodes=[*demo.gpu['gpu_nodes'],*demo.storage['storage_nodes'],*demo.storage['metadata_nodes']]
    received=[];waits=[];cpu_busy=[]
    capacity=demo.gpu['network']['bandwidth_gbps']*1e9/8
    for node in nodes:
        rx=rate(first,last,node,'node_network_receive_bytes_total',device='roce0')
        tx=rate(first,last,node,'node_network_transmit_bytes_total',device='roce0')
        assert 0<=rx<=capacity and 0<=tx<=capacity
        assert rx==pytest.approx(rate(first,last,node,'node_infiniband_port_data_received_bytes_total',device='mlx5_0',port='1'))
        assert tx==pytest.approx(rate(first,last,node,'node_infiniband_port_data_transmitted_bytes_total',device='mlx5_0',port='1'))
        received.append(rx)
        waits.append(rate(first,last,node,'node_infiniband_port_transmit_wait_total',device='mlx5_0',port='1'))
        for cpu in range(4):
            modes={mode:rate(first,last,node,'node_cpu_seconds_total',cpu=str(cpu),mode=mode)
                   for mode in ('idle','user','system','iowait','steal')}
            assert sum(modes.values())==pytest.approx(1)
            assert all(0<=v<=1 for v in modes.values())
            if cpu==0:cpu_busy.append(1-modes['idle'])
        for total,free in (('node_memory_MemTotal_bytes','node_memory_MemAvailable_bytes'),
                           ('node_memory_SwapTotal_bytes','node_memory_SwapFree_bytes')):
            assert 0<=metric(last,node,free)<=metric(last,node,total)
    assert len(set(received))==len(nodes)
    assert len(set(waits))==len(nodes)
    assert len(set(cpu_busy))==len(nodes)
    pressured=capture(demo,monkeypatch,41)
    after=capture(demo,monkeypatch,43)
    new_waits=[rate(pressured,after,n,'node_infiniband_port_transmit_wait_total',device='mlx5_0',port='1') for n in nodes]
    assert all(a>b for a,b in zip(new_waits,waits))
    # Differences model local resource observation, not endpoint-to-endpoint traffic.
    assert len({metric(last,n,'node_memory_MemTotal_bytes') for n in nodes})==3


def test_resource_profiles_replay_and_counters_never_reverse(monkeypatch):
    def replay(started):
        demo=Demo(ROOT/'examples/live-demo');demo.started=started
        return [capture(demo,monkeypatch,elapsed) for elapsed in (1,3,41,43,80,90)]
    original=replay(100.)
    assert original==replay(500.)
    for before,after in zip(original,original[1:]):
        for key,value in after.items():
            if key[1].endswith('_total'):
                assert value>=before[key]


def test_zero_io_has_no_fabricated_queue_or_latency_work(monkeypatch):
    from xlayer_telemetry.demos.live import _phase
    demo=Demo(ROOT/'examples/live-demo')
    _,base=_phase(1)
    values={**base,'read':0,'write':0,'busy':0}
    for now in (demo.started+1,demo.started+3):
        for node in demo.storage['storage_nodes']:
            samples=demo._storage_node(node,now,'idle',values)
            assert all(s.value==0 for s in samples if s.name.startswith('node_disk_'))


def test_storage_profiles_through_real_prometheus_scrape_and_canonical_query():
    """Reuse CI's Prometheus binary; private ports/state and bounded cleanup."""
    import os
    import shutil
    import socket
    import subprocess
    import sys
    import tempfile
    import time
    from urllib.parse import urlencode
    from urllib.request import urlopen
    from xlayer_telemetry.demos.live import prometheus_config
    from xlayer_telemetry.fileio import atomic_write_text

    tool=Path(os.environ['PROMTOOL']).with_name('prometheus') if os.environ.get('PROMTOOL') else None
    executable=str(tool) if tool and tool.is_file() else shutil.which('prometheus')
    if not executable:
        pytest.skip('real Prometheus binary not configured')

    def port():
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0))
            return listener.getsockname()[1]

    exporter_port,prometheus_port=port(),port()
    while prometheus_port==exporter_port:prometheus_port=port()
    processes=[]
    with tempfile.TemporaryDirectory(prefix='xltel-resource-profiles-') as temporary:
        root=Path(temporary);state=root/'scenario.json';config=root/'prometheus.yml'
        demo=Demo(ROOT/'examples/live-demo')
        config.write_text(prometheus_config(demo,f'127.0.0.1:{exporter_port}','profile-test').replace(
            'scrape_interval: 2s','scrape_interval: 250ms'))
        atomic_write_text(state,json.dumps(make_scenario(start=time.time()-2,run_id='phase-demo',node='gpu-node-0')))
        log=(root/'processes.log').open('wb')
        try:
            for command in ([sys.executable,'-m','xlayer_telemetry.demos.live','--listen',f'127.0.0.1:{exporter_port}',
                             '--scenario-state',str(state)],
                            [executable,f'--config.file={config}',f'--storage.tsdb.path={root / "data"}',
                             f'--web.listen-address=127.0.0.1:{prometheus_port}']):
                processes.append(subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=log))

            def query(expression):
                url=f'http://127.0.0.1:{prometheus_port}/api/v1/query?'+urlencode({'query':expression})
                with urlopen(url,timeout=2) as response:
                    payload=json.load(response)
                assert payload['status']=='success' and not payload.get('warnings'), payload
                return payload['data']['result']

            def ready(expression,predicate):
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    assert all(p.poll() is None for p in processes), (root/'processes.log').read_text()[-4000:]
                    try:
                        rows=query(expression)
                        if predicate(rows):return rows
                    except OSError:
                        pass
                    time.sleep(.1)
                pytest.fail(f'synthetic scrape/query did not become ready: {expression}')

            # Read the actual panel query and replace only test filter/window values.
            dashboard=json.loads((ROOT/'examples/dashboards/data-storage.json').read_text())
            expression=next(target['expr'] for panel in dashboard['panels'] for target in panel.get('targets',[])
                            if 'rate(node_disk_read_time_seconds_total' in target.get('expr',''))
            expression=expression.replace('$cluster','profile-test').replace('$node','storage-node-.*').replace(
                '$device','nvme.*').replace('$__rate_interval','2s')
            normal=ready(expression,lambda rows:len(rows)==32)
            expected={(row['metric']['nodename'],row['metric']['device']) for row in normal}
            assert len(expected)==32 and len({float(row['value'][1]) for row in normal})>16
            assert all(row['metric']['data_origin']=='synthetic' and 'run_id' not in row['metric'] for row in normal)
            normal_values={(row['metric']['nodename'],row['metric']['device']):float(row['value'][1]) for row in normal}
            atomic_write_text(state,json.dumps(make_scenario(start=time.time()-42,run_id='phase-demo',node='gpu-node-0')))
            regression=ready(expression,lambda rows:len(rows)==32 and all(
                float(row['value'][1])>normal_values[(row['metric']['nodename'],row['metric']['device'])]*2 for row in rows))
            assert {(r['metric']['nodename'],r['metric']['device']) for r in regression}==expected
            assert all(float(row['value'][1])>0 for row in regression)
            targets=ready('up{cluster="profile-test"}',lambda rows:len(rows)==19)
            assert all(float(row['value'][1])==1 for row in targets)
        finally:
            for process in reversed(processes):
                if process.poll() is None:process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=5)
            log.close()
