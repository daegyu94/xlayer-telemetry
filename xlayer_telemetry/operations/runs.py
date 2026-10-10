"""Bounded saved-run catalog and comparison; no database or backend queries."""
from __future__ import annotations

from collections import defaultdict
from collections import Counter
from datetime import datetime
import hashlib
from itertools import islice
from fractions import Fraction
import json
import os
from pathlib import Path
import time

from ..analysis.jsonl_cache import JSONLCache
from ..analysis.diagnosis_analysis import workload_matches
from ..analysis.evidence_quality import correlation_quality_issues
from ..fileio import atomic_write_text
from ..measurements import finite_number

CATALOG_UID = 'xlayer-run-catalog'
MAX_RUNS = 100
MAX_RECORDS = 5000
MAX_BYTES = 16 * 1024 * 1024
MAX_CATALOG = 4 * 1024 * 1024
_CACHE = JSONLCache(max_records=MAX_RECORDS, max_bytes=4*1024*1024, max_files=2)


def _object(path):
    try:
        with path.open('rb') as stream: body = stream.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024: return {}
        value = json.loads(body)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _text(value):
    return value if isinstance(value, str) and 0 < len(value) <= 256 and not any(ord(c)<32 for c in value) else None


def _stamp(value):
    if not isinstance(value, str): return None
    try: return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp() * 1000
    except ValueError: return None


def _records(path):
    try:
        if not path.is_file() or path.is_symlink(): return [], False
        before = path.stat()
        if before.st_size > MAX_BYTES: return [], True
        def version(value):
            return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
        with path.open('rb') as stream:
            if version(os.fstat(stream.fileno())) != version(before): return [], True
            rows = list(islice(_CACHE.read(path), MAX_RECORDS + 1))
            if version(path.stat()) != version(before): return [], True
            partial = len(rows) > MAX_RECORDS
            if before.st_size:
                stream.seek(-1, 2)
                partial = partial or stream.read(1) != b'\n'
            stream.seek(0)
            expected = sum(bool(line.strip()) for line in islice(stream, MAX_RECORDS+1))
            partial = partial or expected != len(rows) or bool(before.st_size and not rows)
            if version(os.fstat(stream.fileno())) != version(before) or version(path.stat()) != version(before):
                return [], True
    except (OSError, ValueError, RecursionError):
        return [], True
    return rows[:MAX_RECORDS], partial


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',',':'), allow_nan=False)


def summarize(run: Path):
    manifest, health = _object(run/'telemetry-manifest.json'), _object(run/'telemetry-health.json')
    run_id = _text(manifest.get('run_id'))
    if not run_id: return None
    config = manifest.get('configuration') or {}
    if not isinstance(config, dict): config = {}
    cluster, observer = _text(config.get('cluster')), _text(config.get('observer_node'))
    model = _text(config.get('model_identifier') or config.get('model_name'))
    all_rows, partial = _records(run/'telemetry-events/verl-steps.jsonl')
    # A copied foreign run is not a record of this run. Observer mismatch is
    # retained as another entity, not silently folded into a trainer average.
    rows = [row for row in all_rows if row.get('run_id') == run_id and _text(row.get('record_id'))
            and row.get('schema_version') == 1 and row.get('cluster',cluster) == cluster]
    partial = partial or len(rows)!=len(all_rows)
    unique = {}
    conflict = False
    for row in rows:
        key = row['record_id']
        if key in unique and unique[key] != row: conflict = True
        unique[key] = row
    rows = list(unique.values())
    origin = {row.get('data_origin', config.get('data_origin', 'observed')) for row in rows}
    data_origin = next(iter(origin)) if len(origin)==1 else 'mixed' if origin else _text(config.get('data_origin')) or 'unknown'
    grouped = defaultdict(list)
    for row in rows:
        identity = {key: row.get(key) for key in ('node','worker_id','producer','role','rank','local_rank','execution_mode','boundary_scope')}
        grouped[_json(identity)].append(row)
    metrics = []
    for key, group in sorted(grouped.items()):
        entity = json.loads(key)
        workloads = [row.get('workload') for row in group]
        valid_workload = all(isinstance(w,dict) and w for w in workloads)
        shape_counts = Counter(_json(w) for w in workloads if isinstance(w,dict))
        workload_shapes = [{'fields':json.loads(key),'fraction':str(Fraction(count,len(group)))}
                           for key,count in sorted(shape_counts.items())]
        accuracy = {row.get('analysis_window',{}).get('accuracy',row.get('boundary_accuracy','unknown')) for row in group}
        quality = 'complete' if not partial and not conflict and len(accuracy)==1 and 'unknown' not in accuracy else 'partial'
        if not entity.get('node') or not entity.get('worker_id') or not entity.get('boundary_scope') or entity.get('execution_mode') not in {'sync','async'}:
            quality = 'partial'
        names = ['step_duration_seconds'] + sorted({f'stage:{name}' for row in group for name in (row.get('stage_durations_seconds') or {})})
        for name in names:
            values = [finite_number(row.get('step_duration_seconds') if name=='step_duration_seconds' else (row.get('stage_durations_seconds') or {}).get(name[6:])) for row in group]
            valid = [value for value in values if value is not None and value >= 0]
            if not valid: continue
            metrics.append({'metric':name,'unit':'s','value':sum(valid)/len(valid),'scope':'application',
                'statistic':'mean_recorded_duration','entity':entity,'count':len(valid),
                'accuracy':next(iter(accuracy)) if len(accuracy)==1 else 'mixed',
                'quality': quality if len(valid)==len(group) else 'partial',
                'workload_shapes':workload_shapes,'workload_observed':valid_workload,'clock':'local_reported_duration'})
    # Preserve resource evidence from the latest recorded diagnosis window.
    # Do not average p99 across reports, or treat shared evidence as run I/O.
    reports, report_partial = _records(run/'diagnostics/diagnostics.jsonl')
    reports = [report for report in reports if report.get('run_id')==run_id and report.get('record_type')=='bottleneck_diagnosis']
    latest = reports[-1] if reports else {}
    for signal in (latest.get('comparison') or {}).get('signals', []):
        if signal.get('scope') == 'application' or finite_number(signal.get('current')) is None: continue
        labels = signal.get('labels')
        if not isinstance(labels,dict): labels = {}
        quality = 'complete'
        if report_partial or not labels or not signal.get('unit') or not signal.get('window_statistic'):
            quality = 'partial'
        if latest.get('clock_quality',{}).get('status') != 'aligned': quality = 'clock_unknown'
        sampling = (signal.get('sampling_quality') or {}).get('current')
        if not sampling or correlation_quality_issues(sampling): quality = 'sampling_unknown'
        if signal.get('comparison_status') or signal.get('signal')=='threefs_p99_latency' and (signal.get('comparison_quality') or {}).get('status') != 'shared_report_window':
            quality = 'comparison_quality_incomplete'
        metrics.append({'metric':signal['signal'],'value':signal['current'],'unit':signal.get('unit'),
            'scope':signal.get('scope'),'statistic':signal.get('window_statistic'),'entity':labels,'quality':quality,
            'clock':latest.get('clock_quality',{}).get('status','unknown'),'count':1,
            'exposure':{key:(sampling or {}).get(key) for key in ('interval_seconds','query_step_seconds','range_window_seconds','evaluation_count')},
            'observation':'latest_stored_window_not_run_average'})
    step_metrics = [row for row in metrics if row['metric']=='step_duration_seconds']
    starts = [finite_number(row.get('window_start_ms')) for row in rows]
    ends = [finite_number(row.get('window_end_ms')) for row in rows]
    starts,ends = [v for v in starts if v is not None],[v for v in ends if v is not None]
    from ..telemetry_health import reported_workload_status
    workload = health.get('workload') or {}
    status = reported_workload_status(workload)
    telemetry_status = _text(health.get('status')) or 'unknown'
    selected = max(rows,key=lambda row:finite_number(row.get('window_end_ms')) or 0) if rows else {}
    return {'key':hashlib.sha256(_json([cluster,run_id,observer]).encode()).hexdigest()[:24], 'run_id':run_id,
        'cluster':cluster,'observer_node':observer,'model':model,'execution_mode':_text(config.get('execution_mode')),
        'fingerprint':_text(config.get('workload_fingerprint')), 'steps':len(rows),
        'average_step':step_metrics[0]['value'] if len(step_metrics)==1 else None,
        'from':min(starts) if starts else _stamp(manifest.get('created_at')),'to':max(ends) if ends else None,
        'status':status,'status_recorded_at':workload.get('recorded_at',health.get('observed_at')),
        'workload_exit_code':workload.get('exit_code') if type(workload.get('exit_code')) is int else None,
        'telemetry_status':telemetry_status,
        'quality':'partial' if partial or conflict or telemetry_status=='partial' or len(metrics)>100 or data_origin not in {'observed','synthetic'} else 'complete','metrics':metrics[:100],
        'data_origin':data_origin,'source':'stored_artifact','selected_step':{key:selected.get(key) for key in
            ('record_id','step','window_start_ms','window_end_ms','node','worker_id','boundary_scope')},
        'limitations':['Recorded status is not a live process check. Shared resources are not run-owned.',
            'Resource values describe the last saved diagnosis window, not a run average.']}


def catalog(root: Path, *, max_runs=MAX_RUNS):
    root = Path(root).expanduser().absolute()
    paths = [root] if (root/'telemetry-manifest.json').is_file() else sorted(islice((p for p in root.iterdir() if p.is_dir() and not p.is_symlink()),max_runs+1)) if root.is_dir() else []
    values, unavailable = [], []
    for path in paths[:max_runs]:
        try:
            value = summarize(path)
        except (OSError, ValueError, TypeError, AttributeError):
            unavailable.append({'run_id': _text(_object(path/'telemetry-manifest.json').get('run_id')),
                                'reason': 'artifact_unreadable_or_invalid'})
            continue
        if value is not None:
            values.append(value)
    keys = [row['key'] for row in values]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate saved-run identity; keep artifact roots separate or remove copied manifests from this index.')
    result = {'schema_version':1,'generated_at':time.time(),'runs':values,'truncated':len(paths)>max_runs,
              'source':'saved_artifact_projection','clock_verified':False,'unavailable_runs':unavailable}
    if len(_json(result).encode()) > MAX_CATALOG:
        raise ValueError('Run catalog exceeds 4 MiB; select a smaller root or run subset.')
    return result


def compare(a,b):
    reasons=[]
    for key in ('model','cluster','execution_mode','data_origin'):
        if not a.get(key) or not b.get(key) or a[key]=='unknown' or b[key]=='unknown': reasons.append(key+'_missing')
        elif a[key] != b[key]: reasons.append(key+'_mismatch')
    if not a.get('fingerprint') or not b.get('fingerprint'): reasons.append('workload_fingerprint_missing')
    elif a['fingerprint'] != b['fingerprint']: reasons.append('workload_fingerprint_mismatch')
    if a.get('quality')!='complete' or b.get('quality')!='complete': reasons.append('artifact_coverage_partial')
    if not a.get('metrics') or not b.get('metrics'): reasons.append('saved_metric_missing')
    incompatible = any(reason.endswith('_mismatch') for reason in reasons)
    overall = 'Incomparable' if incompatible else 'Partial' if reasons else 'Verified'
    out=[]
    def identity(row): return _json([row.get('metric'),row.get('scope'),row.get('entity')])
    peers={identity(row):row for row in b.get('metrics',[])}
    for before in a.get('metrics',[]):
        after = peers.get(identity(before))
        blocked = list(reasons)
        if not after: blocked.append('matching_entity_missing')
        else:
            for key in ('unit','statistic','accuracy'):
                if before.get(key)!=after.get(key): blocked.append(key+'_mismatch')
            if before.get('quality')!='complete' or after.get('quality')!='complete': blocked.append('metric_quality_incomplete')
            if before.get('scope')!='application' and any(name in (before.get('statistic') or '').lower() for name in ('max','min','p95','p99')):
                exposure_a,exposure_b=before.get('exposure') or {},after.get('exposure') or {}
                if not exposure_a.get('interval_seconds') or not exposure_a.get('evaluation_count') or exposure_a!=exposure_b:
                    blocked.append('window_exposure_or_population_mismatch')
            if before.get('scope')=='application':
                if not before.get('workload_observed') or not after.get('workload_observed'): blocked.append('workload_observation_missing')
                else:
                    shapes_a, shapes_b = before.get('workload_shapes',[]), after.get('workload_shapes',[])
                    matched = len(shapes_a)==len(shapes_b) and all(
                        left.get('fraction')==right.get('fraction') and set(left.get('fields',{}))==set(right.get('fields',{}))
                        and workload_matches({'workload':left['fields']},{'workload':right['fields']},
                            {'match_fields':list(left['fields']),'relative_tolerance':0}) for left,right in zip(shapes_a,shapes_b))
                    if not matched: blocked.append('recorded_workload_mismatch')
        av,bv = finite_number(before.get('value')), finite_number((after or {}).get('value'))
        delta = bv-av if not blocked and av is not None and bv is not None else None
        out.append({'metric':before['metric'],'scope':before.get('scope'),'entity':before.get('entity'),
            'unit':before.get('unit'),'statistic':before.get('statistic'),'a':av,'b':bv,'delta':delta,
            'delta_percent':100*delta/abs(av) if delta is not None and av!=0 else None,
            'reasons':blocked or (['baseline_zero'] if av==0 else []),
            'observation':before.get('observation','mean_recorded_duration')})
    # Preserve missing metrics from either run, including multi-entity cases.
    for after in b.get('metrics',[]):
        if not any(identity(before)==identity(after) for before in a.get('metrics',[])):
            out.append({'metric':after['metric'],'scope':after.get('scope'),'entity':after.get('entity'),
                'unit':after.get('unit'),'a':None,'b':after.get('value'),'delta':None,'delta_percent':None,
                'reasons':['matching_entity_missing']})
    if overall=='Verified' and any(row['reasons'] and row['reasons']!=['baseline_zero'] for row in out): overall='Partial'
    return {'comparability':overall,'reasons':reasons,'metrics':out,'run_a':a['key'],'run_b':b['key']}


def publish(root, output):
    data = catalog(Path(root))
    output = Path(output).expanduser().absolute()
    destination = output / 'xlayer-run-catalog.json'
    existing = _object(destination) if destination.exists() else {}
    if destination.is_symlink() or destination.exists() and existing.get('uid')!=CATALOG_UID:
        raise ValueError('Existing catalog destination is not owned by XLayer; it was preserved.')
    dashboard = {'uid':CATALOG_UID,'title':'XLayer saved Run catalog','schemaVersion':39,'version':1,
        'tags':['xlayer-internal'],'editable':False,'panels':[], 'templating':{'list':[]},'xlayerRunCatalog':data}
    atomic_write_text(destination,json.dumps(dashboard,allow_nan=False)+'\n')
    return {'status':'published','run_count':len(data['runs']),'path':str(destination),'truncated':data['truncated']}
