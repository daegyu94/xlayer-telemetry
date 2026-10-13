import {readContext} from '../src/context';
import {test} from 'node:test';import assert from 'node:assert/strict';
import {executionKey,executionChoices,selectExecution,observedPhases,measuredWorkers,workerGpuCell,pressureOrder,appliedPolicies,stepProjection,workerContext,resourceContext} from '../src/distributed';
import {PRESSURE_SPECS} from '../src/scene-specs';
const step={cluster:'c',run_id:'r',node:'n',step:42,window_start_ms:1000,window_end_ms:20000};
const span={...step,record_type:'span',node:'n',producer:'sdk',role:'rollout',worker_id:'w0',trace_id:'t',span_id:'s',phase:'rollout',name:'generate',boundary_accuracy:'exact',start_time_ms:2000,end_time_ms:10000,duration_seconds:8,duration_source:'monotonic',attributes:{workload_fingerprint:'f',boundary_scope:'worker_call'}};
test('execution selection separates same worker IDs across nodes and sources',()=>{
 const rows=[span,{...span,node:'n2',span_id:'s2'},{...span,cluster:'other'}];assert.equal(executionChoices(rows,step).length,2);assert.equal(selectExecution(rows,executionKey(span)).length,1);
});
test('phase coverage adds only measured phases; critic stays distinct',()=>{
 const phases=observedPhases([span,{...span,phase:'critic_update'},{...span,phase:'checkpoint_save',record_type:'event'}],step);assert.ok(phases.includes('critic_update'));assert.ok(!phases.includes('checkpoint_save'));assert.deepEqual(observedPhases([span],step,executionKey(span)),['rollout']);
});
test('worker duration peers require matching operation, scope and fingerprint',()=>{
 const rows=[3,3.2,5].map((seconds,i)=>({...span,worker_id:`w${i}`,span_id:`s${i}`,duration_seconds:seconds,end_time_ms:2000+seconds*1000}));const peers=measuredWorkers(rows,step);assert.equal(peers[2].median,3.2);assert.equal(peers[2].peers,3);assert.ok(peers[2].delta!>50);assert.equal(measuredWorkers(rows.map((r,i)=>i===2?{...r,attributes:{}}:r),step)[2].peers,0);
});
test('known prompt/tool/concurrency/policy differences cannot share an opaque fingerprint cohort',()=>{
 for(const field of ['prompt_tokens','tool_calls','concurrency','applied_policy_version','replica_generation']){
  const rows=[3,3.2,5].map((seconds,i)=>({...span,worker_id:`w${i}`,span_id:`s${i}`,duration_seconds:seconds,
   end_time_ms:2000+seconds*1000,attributes:{...span.attributes,[field]:i===2?2:1}}));
  assert.equal(measuredWorkers(rows,step)[2].peers,0);
 }
});
test('pressure ordering includes an outlier GPU anywhere in eight entities without calling high util a fault',()=>{
 const rows=Array.from({length:8},(_,i)=>({value:i===7?9:90,time:10000,unit:'percent',labels:{cluster:'c',node:'n',gpu:String(i)}}));assert.equal(pressureOrder(rows,{kind:'utilization'})[0].sample.labels.gpu,'7');
 const ranked=pressureOrder(rows,{kind:'utilization',signal:'gpu',unit:'percent',evidence:[{signal:'gpu',unit:'percent',cluster:'c',window_start_ms:1000,window_end_ms:20000,entity:'node=n,gpu=7',baseline:80,evidence_type:'supporting'}]});assert.ok(Math.abs(ranked[0].delta!+88.75)<1e-9);
});
test('queue/task ranking retains entity/state, no sum across sessions',()=>{
 const rows=[{value:100,time:1,unit:'short',labels:{SessionName:'a',State:'RUNNING'}},{value:2,time:1,unit:'short',labels:{SessionName:'a',State:'PENDING_ARGS_AVAIL'}},{value:8,time:1,unit:'short',labels:{SessionName:'b',State:'PENDING_ARGS_AVAIL'}}];assert.equal(pressureOrder(rows,{kind:'task-state'})[0].sample.labels.SessionName,'b');assert.equal(pressureOrder(rows,{kind:'task-state'}).length,3);
});
test('trainer policy or unqualified event cannot create applied-policy coverage',()=>{
 const event={cluster:'c',run_id:'r',node:'n',worker_id:'w',record_type:'event',name:'weights.applied',policy_version:0,policy_version_source:'producer_reported',event_time_unix_nano:1000000,attributes:{policy_scope:'worker_applied'}};assert.equal(appliedPolicies([event]).length,1);for(const change of [{name:'policy.update.completed'},{attributes:{}},{worker_id:null},{policy_version_source:'inferred'}])assert.equal(appliedPolicies([{...event,...change}]).length,0);
});

test('dynamic phase coverage excludes spans outside the selected Step',()=>{
 assert.ok(!observedPhases([{...span,phase:'critic_update',end_time_ms:21000}],step).includes('critic_update'));
 assert.ok(!observedPhases([{...span,phase:'reference',boundary_accuracy:'calibrated',time_uncertainty_seconds:-1}],step).includes('reference'));
});
test('policy coverage rejects noninteger versions instead of rounding them',()=>{
 const event={cluster:'c',run_id:'r',node:'n',worker_id:'w',record_type:'event',name:'weights.applied',policy_version_source:'producer_reported',timestamp_unix_nano:1000000,attributes:{policy_scope:'worker_applied'}};
 for(const version of [1.5,'128',-1,Number.MAX_SAFE_INTEGER+1,true])assert.equal(appliedPolicies([{...event,policy_version:version}]).length,0);
});

test('pressure support cannot cross cluster, Step window or conflict provenance',()=>{
 const sample={value:10,time:10000,unit:'percent',labels:{cluster:'c',node:'n',gpu:'0'}};
 const evidence={cluster:'c',window_start_ms:1000,window_end_ms:20000,signal:'gpu',unit:'percent',entity:'node=n,gpu=0',evidence_type:'supporting',baseline:80};
 assert.equal(pressureOrder([sample],{kind:'utilization',signal:'gpu',unit:'percent',evidence:[evidence]})[0].supporting,true);
 for(const change of [{cluster:'other'},{window_end_ms:9999},{window_start_ms:10001},{identity_conflict:true}])assert.equal(pressureOrder([sample],{kind:'utilization',signal:'gpu',unit:'percent',evidence:[{...evidence,...change}]})[0].supporting,false);
});
test('five pressure cards join canonical producer evidence with entity, scope and units',()=>{
 const cases=[
  ['vLLM waiting','vllm_requests_waiting','service','requests','short',{node:'n',instance:'engine-a'},'node=n,engine=engine-a',14,7,100],
  ['GPU utilization','gpu_utilization_percent','device','percent','percent',{nodename:'n',gpu:'0'},'node=n,gpu=0',80,40,100],
  ['Storage busy','storage_device_busy_ratio','device','percentunit','percentunit',{nodename:'n',device:'sda'},'nodename=n,device=sda',.8,.4,100],
  ['Sandbox I/O PSI','sandbox_io_pressure_ratio','cgroup','ratio','percentunit',{nodename:'n',role:'sandbox',worker_id:'w'},'nodename=n,role=sandbox,worker_id=w',.8,.4,100],
  ['RDMA tx wait','rdma_transmit_wait_ticks_per_second','network-interface','ticks/s','suffix:ticks/s',{nodename:'n',instance:'ep',port:'1'},'nodename=n,instance=ep,port=1',8,4,100],
 ] as const;
 for(const [name,signal,scope,evidenceUnit,sampleUnit,labels,entity,current,baseline,delta] of cases){
  const spec=PRESSURE_SPECS.find(item=>item.name===name);
  assert.equal(spec?.signal,signal,name);
  const sample={value:current,time:10_000,unit:sampleUnit,labels:{cluster:'c',...labels}};
  const evidence={row_kind:'evidence',cluster:'c',window_start_ms:1000,window_end_ms:20_000,
   signal,observation_scope:scope,unit:evidenceUnit,window_statistic:name==='GPU utilization'?'mean':'max',
   entity,current,baseline,evidence_type:'supporting'};
  const [row]=pressureOrder([sample],{kind:'higher',signal:spec?.signal,unit:sampleUnit,evidence:[evidence],endTime:10_000});
  assert.equal(row.supporting,true,name);
  assert.equal(row.delta,delta,name);
 }
});

test('pressure link preserves unknown units, window statistics, zero and freshness',()=>{
 const sample={value:80,time:10_000,unit:'percent',labels:{cluster:'c',nodename:'n',gpu:'0'}};
 const evidence={row_kind:'evidence',cluster:'c',window_start_ms:1000,window_end_ms:20_000,
  signal:'gpu_utilization_percent',observation_scope:'device',unit:'ratio',window_statistic:'mean',
  entity:'node=n,gpu=0',current:.8,baseline:.4,evidence_type:'supporting'};
 const run=(change:Record<string,unknown>={})=>pressureOrder([sample],{kind:'utilization',signal:'gpu_utilization_percent',
  unit:'percent',evidence:[{...evidence,...change}],endTime:10_000})[0];
 assert.equal(run().delta,100);
 assert.equal(pressureOrder([{...sample,value:50}],{kind:'utilization',signal:'gpu_utilization_percent',
  unit:'percent',evidence:[evidence],endTime:10_000})[0].delta,100); // Window mean, not latest sample.
 assert.equal(run({unit:null}).delta,100); // Canonical signal contract supplies the producer unit.
 assert.equal(run({unit:'mystery'}).delta,undefined);
 assert.equal(run({unit:'mystery'}).supporting,true); // Entity link remains; numeric comparison is unknown.
 assert.equal(pressureOrder([{...sample,unit:'mystery'}],{kind:'utilization',signal:'gpu_utilization_percent',
  evidence:[evidence]})[0].unitKnown,false);
 assert.equal(run({baseline:0}).delta,undefined);
 for(const change of [{observation_scope:'node'},{window_statistic:'max'},{entity:'node=n,gpu=1'},
  {window_end_ms:9000},{quality_warnings:'source_sample_before_interval'},{evidence_type:'counter'}]){
  assert.equal(run(change).supporting,false,JSON.stringify(change));
 }
 assert.equal(pressureOrder([sample],{kind:'higher',signal:'gpu_utilization_percent',evidence:[evidence],endTime:20_000,maxAgeMs:1000}).length,0);
});
test('unmapped remote clock does not erase an explicit worker monotonic duration',()=>{
 const remote={...span,node:'remote'};const row=measuredWorkers([remote],step)[0];assert.equal(row.duration,8);assert.equal(row.window.status,'missing');
});

test('Step evidence never accepts another record, clock window or observer',()=>{
 const selected={...step,record_id:'record',observer_node:'observer'};
 assert.equal(stepProjection([selected],selected).length,1);
 for(const change of [{cluster:'other'},{run_id:'other'},{record_id:'old'},{window_end_ms:19000},{observer_node:'other'},{identity_conflict:true}])assert.equal(stepProjection([{...selected,...change}],selected).length,0);
 assert.deepEqual(stepProjection([selected]),[]);
});
test('worker and pressure pivots preserve observer, Run, Step and time',()=>{
 const context={variables:{cluster:['c'],run_id:['r'],source_node:['observer'],record_id:['record'],node:['original']},from:'1000',to:'2000',timezone:'browser'};
 const worker=workerContext(context,{node:'remote',gpu:0},'key');
 assert.deepEqual(worker.variables.node,['remote']);assert.deepEqual(worker.variables.gpu,['0']);assert.deepEqual(worker.variables.source_node,['observer']);
 const resource=resourceContext(context,{node:'storage',device:'sda',engine:'e'});assert.deepEqual(resource.variables.node,['storage']);assert.deepEqual(resource.variables.record_id,['record']);assert.equal(resource.from,'1000');
});

test('conflicting duplicate span IDs are not silently replaced before phase ambiguity checks',()=>{
 const conflict={...span,parent_span_id:'other-parent'};
 const row=measuredWorkers([span,conflict],step)[0];assert.equal(row.window.status,'ambiguous');assert.equal(row.duration,undefined);
});

test('duplicate span IDs cannot overwrite workload, duration or resource provenance',()=>{
 for(const change of [{attributes:{...span.attributes,workload_fingerprint:'other'}},{duration_seconds:9},{gpu:'other'}]){
  const rows=measuredWorkers([span,{...span,...change}],step);
  if(rows.length===1){assert.equal(rows[0].window.status,'ambiguous');assert.equal(rows[0].duration,undefined);}
  else assert.equal(rows.length,2);
 }
});

test('Worker GPU phase samples use Matrix clock screening; call duration survives uncertainty',()=>{
 const row=measuredWorkers([{...span,gpu:'0'}],step)[0];
 const samples=[5000,6000].map(time=>({time,value:0,unit:'percent',labels:{cluster:'c',node:'n',gpu:'0'}}));
 const proof={nodes:['n'],uncertainty:0.01,sampleAge:1};
 assert.equal(workerGpuCell(row,samples,'aligned',proof).value,0);
 for(const status of ['unsafe','unknown','unchecked']){
  assert.equal(workerGpuCell(row,samples,status,proof).value,undefined);
  assert.equal(row.duration,8);
 }
 assert.equal(workerGpuCell(row,samples,'aligned',{...proof,nodes:['other']}).value,undefined);
 assert.equal(workerGpuCell(row,samples,'aligned').value,undefined);
 assert.equal(workerGpuCell(row,samples,'aligned',{...proof,uncertainty:2}).value,undefined);
 assert.equal(workerGpuCell(row,samples,'aligned',{...proof,sampleAge:30}).value,undefined);
});

test('sandbox resource links retain the observer and select the canonical nodename',()=>{
 const context=readContext('?var-run_id=job-a&var-source_node=trainer&var-node=gpu-a&var-sandbox_node=.*&var-record_id=step-a&from=1000&to=2000');
 const selected=resourceContext(context,{nodename:'sandbox-b',node:'worker-label',cluster:'lab'},'sandbox');
 assert.deepEqual(selected.variables.sandbox_node,['sandbox-b']);
 assert.deepEqual(selected.variables.source_node,['trainer']);
 assert.deepEqual(selected.variables.record_id,['step-a']);
 assert.deepEqual(selected.variables.run_id,['job-a']);
 assert.equal(selected.from,'1000');assert.equal(selected.to,'2000');
 assert.deepEqual(resourceContext(context,{node:'other'}).variables.sandbox_node,['.*']);
 assert.deepEqual(resourceContext(context,{},'sandbox').variables.sandbox_node,['.*']);
});
