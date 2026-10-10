"""Cooperative CPU Agent RL trajectory: real SDK JSONL, async fork/join, no GPU.

Run: python examples/research/trajectory_dependency_demo.py --output /tmp/new-run
Then: xltel inspect /tmp/new-run --execution-graph --root-span TRACE/SPAN
This instruments the await boundaries we own, not arbitrary framework RPCs.
"""

import argparse
import asyncio
import json
from pathlib import Path

from xlayer_telemetry.events import CorrelationContext, EventRecorder, SpanLink
from xlayer_telemetry.operations.execution import inspect_execution


async def generate(recorder, root, predecessors=(), *, completion=False):
    with recorder.span('generation', phase='generation', step=1, trace_id=root.trace_id,
        parent_span_id=root.span_id, links=[SpanLink(identity, 'depends_on') for identity in predecessors],
        attributes={'trajectory_id': 'cpu-trajectory', **({'trajectory_completion': True} if completion else {})}) as identity:
        sum(index*index for index in range(3000))
    return identity


async def tool(recorder, root, predecessor, seconds):
    with recorder.span('tool.call', phase='tool', step=1, trace_id=root.trace_id, parent_span_id=root.span_id,
        links=[SpanLink(predecessor, 'depends_on')], attributes={'trajectory_id': 'cpu-trajectory'}) as identity:
        await asyncio.sleep(seconds)
    return identity


async def run(output):
    output.mkdir(parents=True, exist_ok=False)
    (output/'telemetry-manifest.json').write_text(json.dumps({'run_id': 'cpu-trajectory-demo',
        'data_origin': 'synthetic_cpu_workload', 'configuration': {'execution_mode': 'async'}})+'\n')
    recorders = [EventRecorder(output/'telemetry-events', CorrelationContext(
        'cpu-trajectory-demo', 'sdk', 'agent', str(worker), 'cpu-node')) for worker in range(3)]
    with recorders[0].span('trajectory.execute', phase='trajectory', step=1,
        attributes={'execution_contract': 'dependency_dag', 'children_complete': True,
                    'trajectory_id': 'cpu-trajectory', 'tokens': 1000, 'tool_calls': 2}) as root:
        first = await generate(recorders[0], root)
        with recorders[0].span('queue.wait', phase='queue', step=1, trace_id=root.trace_id,
            parent_span_id=root.span_id, links=[SpanLink(first, 'depends_on')],
            attributes={'time_kind': 'wait', 'trajectory_id': 'cpu-trajectory'}) as queued:
            await asyncio.sleep(.001)
        finished = await asyncio.gather(tool(recorders[1], root, queued, .01), tool(recorders[2], root, queued, .002))
        await generate(recorders[0], root, finished, completion=True)
    for recorder in recorders: recorder.close()
    result = inspect_execution(output, root=(root.trace_id, root.span_id))
    result['root_argument'] = root.trace_id + '/' + root.span_id
    (output/'execution-analysis.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.output)), indent=2))
