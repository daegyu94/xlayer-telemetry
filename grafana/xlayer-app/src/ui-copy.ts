import type {Panel} from './catalog';

// Display-only copy. Unknown producer/LLM messages remain unchanged, and native
// dashboard value mappings remain the source of truth for candidate summaries.
export function displaySummary(panel:Panel|undefined,raw:unknown):string {
 if(typeof raw!=='string'||!raw)return '판단 설명이 기록되지 않았습니다.';
 for(const override of panel?.fieldConfig?.overrides||[]){
  if(override.matcher?.id!=='byName'||override.matcher.options!=='summary')continue;
  for(const property of override.properties||[]){
   if(property.id!=='mappings'||!Array.isArray(property.value))continue;
   for(const mapping of property.value){
    if(mapping.type!=='value'||!mapping.options||!Object.prototype.hasOwnProperty.call(mapping.options,raw))continue;
    const text=mapping.options[raw]?.text;
    if(typeof text==='string'&&text)return text;
   }
  }
 }
 return raw;
}

const NOTES:Record<string,string>={
 'Canonical XLayer dashboards are not provisioned. Run scripts/provision_dashboards.py first.':'Canonical XLayer Dashboard가 provisioning되지 않았습니다. 먼저 scripts/provision_dashboards.py를 실행하세요.',
 'Async trainer-update boundary; concurrent rollout/tool execution is not included or owned by this update.':'Async Trainer update의 경계입니다. 동시에 진행된 rollout/tool 실행이 이 update에 포함되거나 귀속된다는 뜻은 아닙니다.',
 'Boundary scope is not reported; step/rollout ownership is not inferred.':'Boundary scope가 보고되지 않았습니다. Step과 rollout의 실행 소유 관계를 추정하지 않습니다.',
 'Store RPC p95 · seconds · operation/status/engine · rolling shared-service context.':'Store RPC p95입니다. 단위는 seconds이며 operation/status/engine별 Rolling Shared-service 관측입니다.',
 'Client DFS batch read/write and D2H staging p95 · microseconds converted to seconds · distinct operations.':'Client DFS batch의 read/write와 D2H staging p95입니다. 서로 다른 operation을 구분하고 microseconds를 seconds로 변환합니다.',
 'Successful client KV-key bytes/s; not physical block-device bandwidth.':'Client가 성공적으로 전달한 KV-key bytes/s입니다. 물리 block-device bandwidth와 다릅니다.',
 'Failed/skipped keys, error RPC and master admission requests are separate populations; never summed.':'Failed/skipped keys, error RPC, Master admission requests는 서로 다른 모집단입니다. 합산하지 않습니다.',
 'Saved diagnosis · maximum reported per-entity p99; raw unit must be reported. No direct ClickHouse query is added.':'저장된 diagnosis의 entity별 reported p99 최댓값입니다. 전체 요청을 합친 p99나 end-to-end latency가 아니며, 원본 단위가 필요합니다.',
 'Node/device completed I/O mean · seconds/operation; not connector/3FS p99 or Run-attributed I/O.':'Node/device에서 완료된 I/O의 mean latency입니다. 단위는 seconds/operation이며 Connector/3FS p99 또는 Run별 I/O가 아닙니다.',
 'Sampled device utilization; not workload MFU.':'Sampled device utilization입니다. Workload MFU를 의미하지 않습니다.',
 'Sampled shared-engine waiting queue.':'Shared engine의 waiting queue를 Sampled로 관측한 값입니다.',
 'Shared session task states; not attributed to this update.':'Shared session의 task state입니다. 이 update에 귀속하지 않습니다.',
 'Worker/cgroup I/O pressure; not a physical SSD latency.':'Worker/cgroup의 I/O pressure입니다. 물리 SSD latency를 의미하지 않습니다.',
 'No sample in the selected interval.':'선택한 구간에 sample이 없습니다.',
 'A finite sample and evaluation timestamp are required.':'유효한 sample 값과 query evaluation timestamp가 필요합니다.',
 'A unique measured phase interval is required.':'고유한 Measured phase 구간이 필요합니다.',
 'Selected Step evidence. Phase attribution and phase baseline are unavailable.':'선택한 Step의 Evidence입니다. Phase attribution과 Phase baseline은 확인할 수 없습니다.',
 'No matching Step evidence.':'일치하는 Step evidence가 없습니다.',
 'Resource identity or sample time does not match the measured phase.':'Resource identity 또는 Sample time이 Measured phase와 일치하지 않습니다.',
 'Worker/cgroup identity is not linked to this execution span. Same-node coincidence does not establish worker ownership.':'Worker/cgroup identity가 이 Execution span에 연결되지 않았습니다. 같은 Node에서 동시에 관측됐다는 것만으로 Worker 소유 관계가 확인되지는 않습니다.',
 'Calibration uncertainty is absent or exceeds this phase interval.':'Calibration uncertainty가 없거나 Phase interval보다 큽니다.',
 'Rolling query lookback extends beyond an evaluation point; not exclusive to this phase. Inspect the canonical query for its actual window. Correlation is not attribution.':'Rolling query lookback은 evaluation 시점 이전을 포함하며 이 Phase만의 관측이 아닙니다. 실제 window는 Canonical query에서 확인하세요. Correlation은 Attribution이 아닙니다.',
 'Session / State count at this time. Node identity was aggregated; this is not phase usage or a phase baseline.':'이 시점의 Session / State count입니다. Node identity가 집계됐으며 Phase별 사용량이나 Phase baseline이 아닙니다.',
 ' Saved candidate evidence below describes the full Step, not phase attribution.':' 아래 저장된 Candidate evidence는 전체 Step에 대한 내용이며 Phase attribution이 아닙니다.',
 ' Related instrumented sandbox.exec call is linked through observed parent IDs.':' 계측된 sandbox.exec call은 관측된 Parent ID를 통해 연결합니다.',
 'Workload comparability is not verified.':'Workload comparability가 검증되지 않았습니다.',
 'Only phase-window observations can be compared; rolling/session context is separate.':'Phase-window observation만 비교할 수 있습니다. Rolling / Session context는 별도로 유지합니다.',
 'Measured phase boundaries or clock quality are unavailable.':'Measured phase boundary 또는 Clock quality를 확인할 수 없습니다.',
 'Clock reference, session or boundary accuracy differs.':'Clock reference, Session 또는 Boundary accuracy가 서로 다릅니다.',
 'Execution scope differs or its identity is incomplete.':'Execution scope가 서로 다르거나 Identity가 불완전합니다.',
 'An identical explicit workload fingerprint is required.':'동일하고 명시적인 Workload fingerprint가 필요합니다.',
 'Metric units, observation type, scope or resource entity differ.':'Metric unit, Observation type, Scope 또는 Resource entity가 서로 다릅니다.',
 'At least two gauge evaluations in each measured interval are required.':'각 Measured interval에 Gauge evaluation이 최소 2개 필요합니다.',
 'Finite measured values are required.':'유효한 Measured value가 필요합니다.',
 'Observation evaluation points must fall inside their own measured intervals.':'각 Observation evaluation 시점이 해당 Measured interval 안에 있어야 합니다.',
 'Comparable observation; relative delta unavailable because baseline is zero.':'비교 가능한 Observation입니다. Baseline이 0이므로 Relative delta는 계산하지 않습니다.',
 'Comparable gauge observations in verified measured phase windows; correlation is not attribution.':'검증된 Measured phase window의 비교 가능한 Gauge observation입니다. Correlation은 Attribution이 아닙니다.',
 'Choose Run':'Run을 선택하세요',
 'Choose Worker':'Worker를 선택하세요',
 'Choose GPU':'GPU를 선택하세요',
 'Choose Engine / endpoint':'Engine / endpoint를 선택하세요',
 'Choose resource node':'Resource node를 선택하세요',
 'Choose producer / role':'Producer / role을 선택하세요',
 'Distinct reported stages · select a completed record':'서로 다른 Reported stage입니다. 완료된 record를 선택하세요.',
 'Distinct label entities · no implicit aggregation':'서로 다른 label entity입니다. 자동으로 집계하지 않습니다.',
 'Conflicting values or units for the same entity and evaluation time.':'같은 Entity와 Evaluation 시점에 서로 충돌하는 값 또는 Unit이 있습니다.',
 'No finite observation matches the selected scope and identity.':'선택한 Scope와 Identity에 일치하는 유효한 Observation이 없습니다.',
 'Multiple entities are selected; no implicit aggregation or representative value is defined.':'여러 Entity가 선택됐습니다. 자동 집계나 대표값을 정의하지 않습니다.',
 'One explicit entity; freshness is not assessed by this source.':'명시적인 Entity 하나를 표시합니다. 이 Source는 Freshness를 검증하지 않습니다.',
 'A unique age observation with the same producer/worker identity is required.':'같은 Producer / Worker identity의 고유한 Age observation이 필요합니다.',
 'Age is invalid, conflicting or evaluated after the selected observation.':'Age가 유효하지 않거나 서로 충돌하거나, 선택한 Observation 이후에 Evaluation됐습니다.',
 'Freshness threshold must be finite and nonnegative.':'Freshness threshold는 유효한 0 이상의 값이어야 합니다.',
 'The producer observation is stale; its value is withheld.':'Producer observation이 Stale이므로 값을 표시하지 않습니다.',
 'One explicit entity with a matching fresh producer age.':'명시적인 Entity 하나와 일치하는 Fresh producer age가 확인됐습니다.',
 'Event identity conflicts with its record/stream provenance.':'Event identity가 Record / Stream provenance와 충돌합니다.',
 'An explicit Step record reference is malformed; no implicit fallback is permitted.':'명시적인 Step record reference가 유효하지 않습니다. 임의의 다른 Step으로 연결하지 않습니다.',
 'Explicit Step record references conflict.':'명시적인 Step record reference가 서로 충돌합니다.',
 'No unique execution identity or observed parent relation links this event to a usable Step.':'이 Event와 유효한 Step을 연결하는 고유한 Execution identity 또는 관측된 Parent relation이 없습니다.',
 'Multiple completed Step records match the execution context; choose one explicitly.':'Execution context와 일치하는 완료된 Step record가 여러 개입니다. 하나를 명시적으로 선택하세요.',
 'Producer provenance differs; an explicit step_record_id or observed parent relation is required across SDK/bridge sources.':'Producer provenance가 다릅니다. SDK / Bridge source를 연결하려면 명시적인 step_record_id 또는 관측된 Parent relation이 필요합니다.',
 'An explicit step_record_id links this event to the completed Step. Unaligned clocks are not compared and the link does not establish causality.':'명시적인 step_record_id가 이 Event를 완료된 Step에 연결합니다. 정렬되지 않은 Clock은 비교하지 않으며, 연결만으로 Causality가 확인되는 것은 아닙니다.',
 'Linked by explicit execution identity or observed parent relation; correlation does not establish causality.':'명시적인 Execution identity 또는 관측된 Parent relation으로 연결됐습니다. Correlation이 Causality를 증명하지는 않습니다.',
};

export function displayUiNote(raw:string):string{
 if(Object.prototype.hasOwnProperty.call(NOTES,raw))return NOTES[raw];
 const clock=raw.match(/^Clock quality is ([\w-]+)\. Raw resource data remain available; precise phase correlation and deltas are withheld\. (.*)$/s);
 if(clock)return `Clock quality: ${clock[1]}. Raw resource data는 확인할 수 있지만 정밀한 Phase correlation과 delta는 보류합니다. ${displayUiNote(clock[2])}`;
 for(const suffix of [' Saved candidate evidence below describes the full Step, not phase attribution.',' Related instrumented sandbox.exec call is linked through observed parent IDs.'])
  if(raw.endsWith(suffix))return displayUiNote(raw.slice(0,-suffix.length))+NOTES[suffix];
 const rolling=raw.match(/^Rolling ([\d.]+)s observation evaluated inside the phase on (.+); not exclusive to this phase\. Correlation is not attribution\.$/s);
 if(rolling)return `${rolling[2]}의 Phase 안에서 evaluation된 Rolling ${rolling[1]}s 관측입니다. 이 Phase만의 값이 아니며 Correlation은 Attribution이 아닙니다.`;
 const phase=raw.match(/^Query evaluation inside phase on (.+); (.+) application boundary \((.+)\)\. Raw scrape timestamp \/ clock calibration may be unknown\. Correlation is not attribution\.$/s);
 if(phase)return `${phase[1]}의 Phase 안에서 evaluation된 관측입니다. Application boundary: ${phase[2]} (${phase[3]}). Raw scrape timestamp / Clock calibration은 Unknown일 수 있으며 Correlation은 Attribution이 아닙니다.`;
 const entities=raw.match(/^(\d+) entities match\. Choose an explicit entity; none are averaged together\.$/);
 if(entities)return `${entities[1]}개 Entity가 일치합니다. Entity를 명시적으로 선택하세요. 서로 평균을 내지 않습니다.`;
 return raw;
}
