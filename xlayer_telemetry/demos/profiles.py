"""Deterministic demo profiles, not hardware models or resource attribution.

Native metric names/units live in the exporter. These small calculations only
vary synthetic load; no service/device path or cross-host traffic is inferred.
"""
from __future__ import annotations

import math


GIB = 1024**3


def wave(elapsed, node_index, device_index=0):
    return math.sin(elapsed/(4+.4*node_index)+device_index+1.3*node_index)


def gpu_profile(elapsed, node_index, node_count, device_index, load):
    wobble=wave(elapsed,node_index,device_index)
    scale=1-.16*node_index/max(1,node_count-1)
    utilization=max(0,min(100,load*(scale+.02*wobble)))
    used=(88+.70*utilization+.7*device_index+1.5*node_index+.6*wobble)*GIB
    capacity=288*GIB
    used=max(0,min(capacity,used))
    return {'utilization':utilization, 'power':180+9*utilization+2*device_index,
        'temperature':38+.28*utilization+.2*device_index+.3*node_index,
        'clock':1200+11*utilization+2*device_index,
        'memory_used':used, 'memory_total':capacity, 'process_memory':used*.9,
        'tensor_activity':max(0,min(1,.72*utilization/100)),
        'dram_activity':max(0,min(1,.12+.55*utilization/100+.01*wobble)),
        'pcie_rx':(0.05+utilization/100)*1e9,
        'pcie_tx':(0.03+utilization/70)*1e9}


def node_profile(elapsed, role, role_index, node_index, value, bandwidth_gbps):
    wobble=wave(elapsed,node_index)
    factor=1-.018*min(role_index,10)
    network_scale={'compute':.96,'storage':.74,'metadata':.10}[role]*factor
    pressure=max(0,min(1,value.get('host_pressure',.04)*(1+.01*node_index+.02*wobble)))
    base={'compute':.22+.40*value['gpu']/100, 'storage':.16+.55*value['busy'],
          'metadata':.10+.25*value['busy']}[role]
    cpu_busy=max(0,min(.95,(base+.20*pressure)*factor+.015*wobble))
    capacity=bandwidth_gbps*1e9/8
    # A NIC and RDMA port use the same local fixture rates, never a verified
    # compute-to-storage traffic pair. Do not exceed configured link capacity.
    rx=max(0,min(capacity,value['rx']*1e9/8*network_scale*(1+.04*wobble)))
    tx=max(0,min(capacity,value['tx']*1e9/8*network_scale*(1-.03*wobble)))
    total={'compute':1024,'storage':512,'metadata':128}[role]*GIB
    used_ratio=max(0,min(.95,.20+.32*cpu_busy+.06*value['busy']+.006*role_index+.01*wobble))
    swap={'compute':32,'storage':16,'metadata':8}[role]*GIB
    error_factor=(1+.03*node_index)*(1+.25*value['busy'])
    return {'cpu_busy':cpu_busy, 'cpu_pressure':pressure,
        'cpu_weights':{'compute':(.70,.22,.06,.02), 'storage':(.45,.25,.28,.02),
                       'metadata':(.50,.43,.05,.02)}[role],
        'memory_total':total, 'memory_available':total*(1-used_ratio),
        'swap_total':swap, 'swap_free':swap*(1-.04-.10*pressure),
        'rx':rx, 'tx':tx, 'error_factor':error_factor,
        # Native transmit-wait ticks retain their native interpretation;
        # this is a sampled scenario fixture, not measured queueing time.
        'transmit_wait':200*network_scale*(1+.03*node_index)*(1+6*value['busy']**2)}


def disk_profile(elapsed, node_index, device_index, value):
    wobble=wave(elapsed,node_index,device_index)
    scale=1-.015*min(node_index,12)
    read=max(0,value['read']*GIB*scale*(.85+.07*device_index+.04*wobble))
    write=max(0,value['write']*GIB*scale*max(.1,1.15-.07*device_index-.03*wobble))
    busy=max(0,min(1,value['busy']*(.84+.035*device_index+.005*node_index+.025*wobble))) if read+write else 0.
    reads,writes=read/(256*1024),write/(256*1024)
    # Little's-law-inspired *synthetic* load, not a real SSD latency model.
    read_latency=.0004*(1+.025*node_index+.04*device_index)*(1+12*busy**2)
    write_latency=.0008*(1+.02*node_index+.03*device_index)*(1+16*busy**2)
    queue=math.ceil(reads*read_latency+writes*write_latency)
    return {'read':read, 'write':write, 'reads':reads, 'writes':writes, 'busy':busy,
        'read_time':reads*read_latency, 'write_time':writes*write_latency, 'queue':queue}
