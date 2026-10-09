"""Opt-in, read-only rollout observations; no Ray import or scheduler control."""
from __future__ import annotations

import asyncio
import math

STATES = {'sleeping', 'weight_update', 'waking', 'serving', 'unknown'}
WORKLOAD_FIELDS = {'prompt_tokens', 'output_tokens', 'turns', 'tool_calls', 'concurrency'}


def text(value, limit=256):
    return isinstance(value, str) and 0 < len(value) <= limit and not any(ord(c) < 32 for c in value)


def router_status(value):
    """Decode public get_status counts; never serialize actor handles/requests."""
    servers = value.get('servers') if isinstance(value, dict) else None
    if (not isinstance(servers, dict) or len(servers) > 128 or
            any(not text(key) or type(count) is not int or not 0 <= count <= 2**53 for key, count in servers.items()) or
            type(value.get('total_inflight')) is not int or value['total_inflight'] != sum(servers.values()) or
            type(value.get('active_servers')) is not int or value['active_servers'] != len(servers)):
        raise ValueError('unsupported router status')
    handles = value.get('registered_handles', list(servers))
    if not isinstance(handles, list) or len(handles) != len(servers) or set(handles) != set(servers):
        raise ValueError('inconsistent router membership')
    return dict(servers)


class RolloutObserver:
    """Call state hooks only after runtime confirmation, with native identities."""
    def __init__(self, recorder, *, cluster, router_id):
        if not text(cluster, 64) or not text(router_id, 64):
            raise ValueError('cluster and router_id are required')
        self.recorder, self.cluster, self.router_id = recorder, cluster, router_id

    def _attributes(self, **values):
        return {'rollout_observation_version': 1, 'cluster': self.cluster, 'router_id': self.router_id, **values}

    def router_status(self, status):
        self.recorder.event('rollout.router.snapshot', phase='rollout_state', attributes=self._attributes(
            observation_source='verl_router_get_status', query_status='ok', servers=router_status(status)))

    async def poll_router(self, getter, *, timeout_seconds=1):
        """Getter must return an awaitable, e.g. router.get_status.remote()."""
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 10:
            raise ValueError('router poll timeout must be 0..10 seconds')
        try:
            status = await asyncio.wait_for(getter(), timeout=timeout_seconds)
            self.router_status(status)
            return True
        except (OSError, RuntimeError, TimeoutError, asyncio.TimeoutError, ValueError, TypeError) as error:
            self.recorder.event('rollout.router.snapshot', phase='rollout_state', attributes=self._attributes(
                observation_source='verl_router_get_status', query_status=type(error).__name__))
            return False

    def _replica(self, replica_id, instance, generation):
        if not text(replica_id, 64) or not text(instance) or (generation is not None and not text(generation, 64)):
            raise ValueError('explicit replica/instance identity required')
        return {'replica_id': replica_id, 'instance': instance, 'replica_generation': generation}

    def replica_state(self, replica_id, instance, state, *, generation=None):
        if state not in STATES:
            raise ValueError('unsupported replica state')
        self.recorder.event('rollout.replica.state', phase='rollout_state', attributes=self._attributes(
            **self._replica(replica_id, instance, generation), serving_state=state,
            observation_source='explicit_runtime_confirmation'))

    def replica_workload(self, replica_id, instance, workload, *, generation=None):
        if (not isinstance(workload, dict) or not workload or set(workload) - WORKLOAD_FIELDS or
                any(type(value) is not int or not 0 <= value <= 2**53 for value in workload.values())):
            raise ValueError('workload needs explicit bounded token/turn/tool/concurrency counts')
        self.recorder.event('rollout.replica.workload', phase='rollout_state', attributes=self._attributes(
            **self._replica(replica_id, instance, generation), workload=dict(workload),
            observation_source='explicit_runtime_confirmation'))

    def policy_applied(self, replica_id, instance, version, *, generation=None):
        # A router snapshot or trainer announcement cannot call this implicitly.
        self.recorder.policy_applied(version, attributes=self._attributes(
            **self._replica(replica_id, instance, generation), observation_source='explicit_worker_applied'))
