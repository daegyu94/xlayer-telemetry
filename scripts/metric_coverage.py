#!/usr/bin/env python3
"""Generate source-backed capability references, never live availability claims."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xlayer_telemetry.adapters.verl import DIRECT_METRICS, ASYNC_DECISIONS, MFU_STAGES, POLICY_VERSION_KEYS, STAGE_PHASES, VerlMetricsAdapter
from xlayer_telemetry.analysis.diagnostics import DEFAULT_QUERIES
from xlayer_telemetry.analysis.metric_queries import METRIC_PROFILES, profile_queries


# These are source-name bindings, NOT exporter renames or automatic unit
# conversions. Unmapped contract entries remain explicitly unwired.
RAW_NAMES = {
    'gpu_utilization_percent':['telemetry_gpu_utilization_percent'],
    'gpu_memory_used_bytes':['telemetry_gpu_memory_used_bytes'],
    'gpu_memory_total_bytes':['telemetry_gpu_memory_total_bytes'],
    'gpu_power_watts':['telemetry_gpu_power_watts'],
    'gpu_temperature_celsius':['telemetry_gpu_temperature_celsius'],
    'gpu_clock_hertz':['telemetry_gpu_sm_clock_mhz'],
    'host_cpu_utilization_percent':['node_cpu_seconds_total'],
    'host_memory_used_bytes':['node_memory_MemTotal_bytes','node_memory_MemAvailable_bytes'],
    'host_memory_pressure_ratio':['node_pressure_memory_waiting_seconds_total'],
    'network_receive_bytes_per_second':['node_network_receive_bytes_total'],
    'network_transmit_bytes_per_second':['node_network_transmit_bytes_total'],
    'network_errors_total':['node_network_receive_errs_total','node_network_transmit_errs_total'],
    'rdma_receive_bytes_per_second':['node_infiniband_port_data_received_bytes_total'],
    'rdma_transmit_bytes_per_second':['node_infiniband_port_data_transmitted_bytes_total'],
    'rdma_errors_total':['node_infiniband_port_errors_received_total'],
    'storage_read_bytes_per_second':['node_disk_read_bytes_total'],
    'storage_write_bytes_per_second':['node_disk_written_bytes_total'],
    'storage_read_bytes_total':['node_disk_read_bytes_total'],
    'storage_write_bytes_total':['node_disk_written_bytes_total'],
    'storage_iops':['node_disk_reads_completed_total','node_disk_writes_completed_total'],
    'storage_queue_depth':['node_disk_io_time_weighted_seconds_total'],
    'storage_device_utilization_percent':['node_disk_io_time_seconds_total'],
    'storage_filesystem_capacity_used_ratio':['node_filesystem_avail_bytes','node_filesystem_size_bytes'],
    'rollout_requests_queued':['vllm:num_requests_waiting'],
    'rollout_output_tokens_per_second':['vllm:generation_tokens_total'],
    'rollout_time_to_first_token_seconds':['vllm:time_to_first_token_seconds_bucket'],
    'rollout_time_per_output_token_seconds':['vllm:request_time_per_output_token_seconds_bucket'],
    'rollout_kv_cache_utilization_ratio':['vllm:kv_cache_usage_perc'],
    'rollout_preemptions_total':['vllm:num_preemptions_total'],
    'orchestration_tasks_pending':['ray_tasks'],
    'orchestration_object_store_used_bytes':['ray_object_store_memory'],
    'mooncake_dfs_read_latency_seconds':['mooncake_dfs_read_latency_us_bucket'],
    'mooncake_dfs_write_latency_seconds':['mooncake_dfs_write_latency_us_bucket'],
    'mooncake_dfs_write_staging_latency_seconds':['mooncake_dfs_write_staging_latency_us_bucket'],
    'mooncake_store_operation_time_seconds':['vllm:mooncake_store_operation_time_seconds_bucket'],
    'mooncake_store_operation_bytes_total':['vllm:mooncake_store_operation_bytes_total'],
    'mooncake_store_operation_failed_keys_total':['vllm:mooncake_store_operation_failed_keys_total'],
    'mooncake_master_put_start_failures_total':['master_put_start_failures_total'],
}
POOL = {'sandbox_active','sandbox_queued','sandbox_create_duration_seconds','sandbox_reset_duration_seconds','sandbox_failures_total'}
PRODUCERS = ('collectors/gpu_sampler.py','collectors/sandbox_sampler.py','metrics/textfile.py')
DIRECT_RULES = {'training_step_time_seconds':['step_duration_seconds'],
    'rl_stage_duration_seconds':['rollout_duration_seconds','communication_duration_seconds','actor_update_duration_seconds','critic_update_duration_seconds','checkpoint_duration_seconds']}
STATUS = {'verl_reported':'VERL key가 보고될 때', 'collector_optional':'Collector 설정/field가 제공될 때',
          'collector_alias':'Collector raw 이름; Canonical 이름으로 재노출하지 않음',
          'native_endpoint_required':'Native endpoint/version 필요',
          'runtime_instrumentation_required':'Runtime 계측 필요', 'explicit_integration':'명시 SDK/integration 필요'}


def literals(path):
    return {node.value for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def function_literals(path, names):
    tree=ast.parse(path.read_text())
    return {item.value for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name in names
            for item in ast.walk(node) if isinstance(item,ast.Constant) and isinstance(item.value,str)}


def tokens(expression):
    # Ignore quoted label values so model names cannot masquerade as metrics.
    expression=re.sub(r'"(?:\\.|[^"\\])*"','',expression)
    return set(re.findall(r'[A-Za-z_:][A-Za-z0-9_:]*',expression))


def panel_queries(root):
    result=[]
    for path in sorted((root/'examples/dashboards').glob('*.json')):
        document=json.loads(path.read_text()); stack=list(document.get('panels',[]))
        while stack:
            panel=stack.pop(); stack.extend(panel.get('panels',[]))
            names=set().union(*(tokens(target.get('expr','')) for target in panel.get('targets',[]) if isinstance(target.get('expr',''),str)))
            if names: result.append((str(path.relative_to(root)),panel['id'],panel.get('title',''),names))
    return result


def catalogue(root=ROOT):
    root=Path(root)
    contract=json.loads((root/'config/metrics.json').read_text())['metrics']
    logger={key:1 for key in DIRECT_METRICS}
    logger.update({key:1 for key in ASYNC_DECISIONS})
    logger.update({key:.5 for key in MFU_STAGES}); logger.update({key:1 for key in POLICY_VERSION_KEYS})
    logger.update({'timing_s/'+stage:1 for stage in STAGE_PHASES})
    logger.update({'timing_per_token_ms/'+stage:1 for stage in STAGE_PHASES})
    bridge={metric.name for metric in VerlMetricsAdapter.translate(logger)}
    source_sets={path:literals(root/'xlayer_telemetry'/path) for path in PRODUCERS}
    gpu=source_sets['collectors/gpu_sampler.py']
    query_sets={'default':{name:tokens(expr) for name,expr in DEFAULT_QUERIES.items()}}
    for profile in METRIC_PROFILES:
        query_sets[profile]={name:tokens(expr) for name,expr in profile_queries({'metric_profiles':[profile],'mooncake_master_node':'declared'},'audit').items()}
    rules=function_literals(root/'xlayer_telemetry/analysis/diagnosis_analysis.py',{'evaluate_rules'})
    rules |= function_literals(root/'xlayer_telemetry/analysis/diagnostics.py',{'_findings'})
    panels=panel_queries(root); rows=[]
    for metric in contract:
        name=metric['name']; raw=RAW_NAMES.get(name,[name]); refs=[]
        for path,known in source_sets.items():
            if any(value in known or (path.endswith('gpu_sampler.py') and value.startswith('telemetry_gpu_') and value.removeprefix('telemetry_gpu_') in gpu) for value in raw):
                refs.append('xlayer_telemetry/'+path)
        collection='explicit_integration'
        if name in bridge:
            collection='verl_reported'; refs.append('xlayer_telemetry/adapters/verl.py')
        elif refs: collection='collector_optional' if raw==[name] else 'collector_alias'
        elif name in POOL: collection='runtime_instrumentation_required'
        elif name in RAW_NAMES or name.startswith('mooncake_'): collection='native_endpoint_required'
        queries={profile:[signal for signal,names in items.items() if set(raw)&names] for profile,items in query_sets.items()}
        queries={key:value for key,value in queries.items() if value}
        rule_signals=sorted(set(DIRECT_RULES.get(name,[]))|{signal for values in queries.values() for signal in values if signal in rules})
        used=[{'path':path,'panel':identity,'title':title} for path,identity,title,names in panels if set(raw)&names]
        rows.append({**metric,'defined':True,'collection':collection,'raw_names':raw,'producer_refs':refs,
                     'query_profiles':queries,'rule_signals':rule_signals,'panels':used,'automatic_from_span':False})
    return rows


def render(root=ROOT):
    rows=catalogue(root); categories=sorted({row['category'] for row in rows})
    text=[f'<!-- Generated by scripts/metric_coverage.py; {len(rows)} definitions / {len(categories)} categories. -->',
          '',f'Canonical 정의 **{len(rows)}개 / {len(categories)}개 category**입니다. 아래는 source/query capability이며 현재 배포의 수집·health·support 보장이 아닙니다.',
          '', 'Raw 이름은 query에서 참조하는 관측입니다. Counter→rate·histogram→quantile·MHz/Hz 등의 변환과 scope는 Metrics Contract를 확인하세요. SDK는 임의 contract를 기록할 수 있지만 자동 producer로 계산하지 않습니다.', '']
    for category in categories:
        text += [f'# {category}', '', '| Defined metric / unit / scope | Raw source name | Collection 조건 | Queried | Diagnosed | Visualized |', '| --- | --- | --- | --- | --- | --- |']
        for row in rows:
            if row['category'] != category: continue
            profile=', '.join(row['query_profiles']) or '—'
            diagnosed=', '.join(row['rule_signals']) or ('비교 context만; rule 없음' if row['query_profiles'] else '—')
            visual=', '.join(sorted({Path(panel['path']).stem for panel in row['panels']})) or '—'
            text.append(f"| `{row['name']}`<br>{row['unit']} · {row['scope']} | {'<br>'.join('`'+x+'`' for x in row['raw_names'])} | {STATUS[row['collection']]} | {profile} | {diagnosed} | {visual} |")
        text.append('')
    return '\n'.join(text)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true'); parser.add_argument('--json',type=Path)
    args=parser.parse_args(); target=ROOT/'docs/_includes/metric-coverage.md'
    content=render()
    if args.check:
        if not target.exists() or target.read_text()!=content: parser.exit(1,'Metric coverage is stale; run python scripts/metric_coverage.py\n')
    else: target.parent.mkdir(parents=True,exist_ok=True); target.write_text(content)
    if args.json: args.json.write_text(json.dumps(catalogue(),ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__': main()
