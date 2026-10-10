"""Bounded sampled lifecycle coverage, never inferred from scrape availability."""
from __future__ import annotations

from pathlib import Path

from ..fileio import IncompleteJSONL, json_objects
from ..time_alignment import event_window, alignment_metadata
from ..adapters.rollout import STATES, WORKLOAD_FIELDS, text


def validate_observations(config):
    settings = config.get('rollout_observations')
    if settings is None:
        return
    if (not isinstance(settings, dict) or set(settings) - {'events_dir', 'router_id', 'max_age_seconds'}
            or not text(settings.get('events_dir'), 4096) or not text(settings.get('router_id'), 64)
            or type(settings.get('max_age_seconds', 60)) not in (int, float)
            or not 0 < settings.get('max_age_seconds', 60) <= 3600 or not config.get('rollout_replicas')):
        raise ValueError('rollout_observations needs replicas, events_dir, router_id and bounded max_age_seconds')


def _unknown(issues=()):
    return {'source': 'explicit_sdk_observations', 'status': 'unknown', 'router_registered': None,
            'inflight_requests': None, 'serving_state': 'unknown', 'generation': None,
            'inactive_observed': False, 'workload': {}, 'applied_policy_version': None, 'quality_issues': list(issues)}


def load_serving_context(config, window, clock, *, reader=None):
    replicas, settings = config.get('rollout_replicas', []), config.get('rollout_observations')
    result = {row['id']: _unknown(['observations_not_configured']) for row in replicas}
    if not settings or not window:
        return result
    validate_observations(config)
    events = []
    try:
        directory = Path(settings['events_dir'])
        files = sorted(directory.glob('*.jsonl'))
        if len(files) > 32 or any(path.stat().st_size > 1024*1024 for path in files):
            raise ValueError('observation_file_limit')
        count = 0
        nodes = clock.get('system_clock_screening', clock).get('nodes', {})
        for path in files:
            # A lost transition has unknown identity/time, so no earlier state
            # in this configured source set can prove interval coverage.
            for row in (reader or json_objects)(path, strict=True):
                count += 1
                if count > 8192:
                    raise ValueError('observation_record_limit')
                attrs = row.get('attributes', {})
                if (row.get('record_type') != 'event' or row.get('name') not in {'rollout.router.snapshot','rollout.replica.state','rollout.replica.workload','weights.applied'} or row.get('run_id') != config['run_id'] or
                        not isinstance(attrs, dict) or type(attrs.get('rollout_observation_version')) is not int or attrs.get('rollout_observation_version') != 1 or
                        attrs.get('cluster') != config['cluster'] or attrs.get('router_id') != settings['router_id'] or
                        nodes.get(row.get('node'), {}).get('status') != 'aligned'):
                    continue
                start, end = event_window(row, reference_id=config.get('clock', {}).get('calibration_reference'),
                    reference_session=alignment_metadata(window).get('reference_session'))
                if start is not None and start == end and start <= window['end']:
                    events.append((start, row, attrs))
    except IncompleteJSONL:
        return {row['id']: _unknown(['observation_input_incomplete']) for row in replicas}
    except (OSError, ValueError, TypeError):
        return {row['id']: _unknown(['observation_read_or_limit_failure']) for row in replicas}
    events.sort(key=lambda item: item[0])
    max_age = settings.get('max_age_seconds', 60)

    def stable(rows, key, issue):
        before = [item for item in rows if item[0] <= window['start']]
        if not before:
            return None, ['no_pre_interval_observation']
        relevant = [before[-1], *[item for item in rows if item[0] > window['start']]]
        stamps = [window['start'], *[item[0] for item in relevant], window['end']]
        if window['start']-relevant[0][0] > max_age or any(b-a > max_age for a,b in zip(stamps[1:], stamps[2:])):
            return None, ['observation_stale_or_gap']
        values = [key(item) for item in relevant]
        if any(value != values[0] for value in values) or values[0] is None:
            return None, [issue]
        return relevant[-1], []

    for replica in replicas:
        value = _unknown(); issues = []
        router = [item for item in events if item[1]['name'] == 'rollout.router.snapshot']
        server_id = replica.get('server_id')
        if server_id:
            def membership(item):
                attrs = item[2]
                servers = attrs.get('servers')
                valid = (attrs.get('observation_source') == 'verl_router_get_status' and attrs.get('query_status') == 'ok'
                         and isinstance(servers, dict) and len(servers) <= 128
                         and all(text(key) and type(v) is int and 0 <= v <= 2**53 for key,v in servers.items()))
                return server_id in servers if valid else None
            row, errors = stable(router, membership, 'router_changed_or_unavailable')
            issues.extend(errors)
            if row:
                value['router_registered'] = membership(row)
                value['inflight_requests'] = row[2]['servers'].get(server_id)
        else:
            issues.append('router_server_mapping_not_declared')
        own = [item for item in events if item[2].get('replica_id') == replica['id'] and item[2].get('instance') == replica['instance']]
        lifecycle = [item for item in own if item[1]['name'] == 'rollout.replica.state' and
                     item[2].get('observation_source') == 'explicit_runtime_confirmation' and item[2].get('serving_state') in STATES]
        row, errors = stable(lifecycle, lambda item: (item[2]['serving_state'], item[2].get('replica_generation')), 'lifecycle_changed_during_interval')
        issues.extend(errors)
        if row:
            value.update(serving_state=row[2]['serving_state'], generation=row[2].get('replica_generation'))
        value['inactive_observed'] = value['serving_state'] in {'sleeping','weight_update','waking'} or any(
            item[2]['serving_state'] in {'sleeping','weight_update','waking'} and window['start'] <= item[0] <= window['end'] for item in lifecycle)
        before = [item for item in lifecycle if item[0] <= window['start']]
        after = [item for item in lifecycle if item[0] > window['start']]
        if before:
            prior = before[-1]
            next_time = after[0][0] if after else window['end']
            if (prior[2]['serving_state'] in {'sleeping','weight_update','waking'} and
                    next_time - prior[0] <= max_age):
                value['inactive_observed'] = True
        for name, field in (('rollout.replica.workload', 'workload'), ('weights.applied', 'applied_policy_version')):
            rows = [item for item in own if item[1]['name'] == name]
            row, errors = stable(rows, lambda item: (item[2].get('workload'), item[2].get('replica_generation')) if field == 'workload'
                                 else (item[1].get('policy_version'), item[2].get('replica_generation')), field+'_changed_during_interval')
            # Absent optional observations remain unknown. Once reported,
            # failed interval coverage must survive reduction to {} / None.
            if rows:
                issues.extend(error if error.startswith(field+'_') else field+'_'+error for error in errors)
            if row and (value['generation'] is None or value['generation'] == row[2].get('replica_generation')):
                if field == 'workload':
                    workload = row[2].get('workload')
                    if isinstance(workload, dict) and workload and not set(workload)-WORKLOAD_FIELDS and all(type(v) is int and 0 <= v <= 2**53 for v in workload.values()):
                        value[field] = workload
                    else:
                        issues.append(field+'_invalid_observation')
                elif (row[2].get('policy_scope') == 'worker_applied' and row[1].get('policy_version_source') == 'producer_reported'
                      and type(row[1].get('policy_version')) is int and row[2].get('observation_source') == 'explicit_worker_applied'):
                    value[field] = row[1]['policy_version']
                else:
                    issues.append(field+'_invalid_observation')
                if value[field] is not None and value[field] != {} and value['generation'] is None:
                    value['generation'] = row[2].get('replica_generation')
            elif row:
                issues.append(field+'_generation_mismatch')
        value['status'] = 'covered' if value['router_registered'] is not None or value['serving_state'] != 'unknown' else 'unknown'
        value['quality_issues'] = sorted(set(issues))
        result[replica['id']] = value
    return result


def comparison_issues(current, baseline):
    issues = []
    for role, context in (('current', current), ('baseline', baseline)):
        for issue in context.get('quality_issues', []):
            if issue.startswith(('workload_', 'applied_policy_version_')) or issue in {
                    'lifecycle_changed_during_interval', 'router_changed_or_unavailable', 'observation_read_or_limit_failure',
                    'observation_input_incomplete'}:
                issues.append('replica_'+role+'_'+issue)
    for field in ('generation', 'workload', 'applied_policy_version', 'serving_state', 'router_registered'):
        a, b = current.get(field), baseline.get(field)
        if (a is not None or b is not None) and a != b:
            issues.append('replica_'+field+'_changed_or_unverified')
    return issues


def apply_serving_limits(candidates, replicas, current_context):
    for candidate in candidates:
        if candidate['id'] == 'gpu_starvation':
            gpu_nodes = {item.get('labels', {}).get('node') or item.get('labels', {}).get('nodename')
                         for item in candidate.get('evidence', []) if item['signal'] == 'gpu_utilization_percent'} - {None}
            paused = [row for row in replicas if gpu_nodes.intersection(row['nodes']) and
                      current_context.get(row['id'], {}).get('inactive_observed')]
            if paused:
                if candidate['state'] == 'strong_signal': candidate['state'] = 'supporting_signal'
                candidate['context_status'] = 'inactive_replica_on_gpu_node'
                candidate['missing_evidence'].append('gpu_idle_overlaps_inactive_replica_device_ownership_unverified')
        signals = [item for item in candidate.get('evidence', []) if item['signal'].startswith(('vllm_', 'mooncake_connector_'))]
        anchors = [row for row in replicas if signals and all(item.get('labels', {}).get('instance') == row['instance'] and
            item.get('labels', {}).get('node') == row['endpoint_node'] for item in signals)]
        if len(anchors) != 1:
            continue
        state = current_context.get(anchors[0]['id'], _unknown())
        candidate['replica_id'] = anchors[0]['id']
        candidate['serving_context'] = state
        if state['serving_state'] in {'sleeping', 'weight_update', 'waking'}:
            candidate.update(state='weak_signal', context_status='intentional_inactive',
                summary='Pressure overlaps an explicitly observed inactive replica; do not interpret it as a serving fault')
            candidate['missing_evidence'].append('replica_intentionally_inactive')
        elif state['router_registered'] is False:
            if candidate['state'] == 'strong_signal': candidate['state'] = 'supporting_signal'
            candidate['context_status'] = 'not_router_registered'
            candidate['missing_evidence'].append('replica_not_router_registered_not_endpoint_failure')
        elif state.get('inactive_observed'):
            if candidate['state'] == 'strong_signal': candidate['state'] = 'supporting_signal'
            candidate['context_status'] = 'lifecycle_transition_overlap'
            candidate['missing_evidence'].append('partial_inactive_observation_not_whole_step_state')
        elif state['serving_state'] == 'unknown':
            candidate['missing_evidence'].append('replica_serving_state_unverified')
        candidate['signal_strength'] = candidate['state']
