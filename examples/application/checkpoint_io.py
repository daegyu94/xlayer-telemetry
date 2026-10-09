"""CPU SDK recipe: explicit logical payload bytes, not physical storage I/O."""
from __future__ import annotations

import argparse
from pathlib import Path
import socket
import time

from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.metrics import Metric, MetricEmitter


def record(output: Path, *, run_id='checkpoint-example', payload=b'xlayer checkpoint\n' * 1024):
    output.mkdir(parents=True, exist_ok=False)
    context=CorrelationContext(run_id=run_id,producer='checkpoint-sdk',role='trainer',worker_id='driver',node=socket.gethostname())
    events=EventRecorder(output/'telemetry-events',context)
    emitter=MetricEmitter(output/'telemetry-metrics',run_id=run_id,producer=context.producer,role=context.role,worker_id=context.worker_id,node=context.node)
    path=output/'checkpoint.bin'
    samples=[Metric('checkpoint_size_bytes',len(payload))]
    for operation in ('save','load'):
        started=time.monotonic()
        with events.span('checkpoint.'+operation,phase='checkpoint',step=1,
            attributes={'logical_bytes':len(payload),'byte_semantics':'explicit operation payload, not physical device/service bytes',
                        'checkpoint_uri':str(path)}) as span:
            if operation=='save':
                count=path.write_bytes(payload)
                if count!=len(payload): raise RuntimeError('incomplete checkpoint write')
            elif path.read_bytes()!=payload:
                raise RuntimeError('checkpoint validation failed')
        duration=time.monotonic()-started
        samples.append(Metric(f'checkpoint_{operation}_time_seconds',duration))
        if duration>0:
            direction='write' if operation=='save' else 'read'
            samples.append(Metric(f'checkpoint_{direction}_throughput_bytes_per_second',len(payload)/duration))
    emitter.emit(step=1,samples=samples)
    print(f'Recorded CPU logical checkpoint payload: {len(payload)} bytes in {output}')
    print('Not a VERL checkpoint, durable-write test, physical bandwidth or 3FS attribution measurement.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--run-id',default='checkpoint-example')
    args=parser.parse_args();record(args.output,run_id=args.run_id)


if __name__=='__main__': main()
