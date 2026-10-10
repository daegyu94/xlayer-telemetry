# Sandbox 관측

:::{container} xlayer-page-meta
**Task** 실제 runtime·worker cgroup을 계측
:::

**목표:** 직접 관리하는 Local/Dedicated sandbox의 lifecycle과 worker cgroup 관측을 연결합니다. Remote Tool/Reward는 명시적인 client 호출 경계만 기록합니다.

## V1 Support Boundary

| 유형 | V1 범위 | 연결 조건 / 한계 |
| --- | --- | --- |
| Colocated | Lifecycle·CPU/memory/I/O 관측 | GPU host의 실제 worker/container cgroup·local device 확인 |
| 직접 관리하는 Dedicated | 조건부 lifecycle·resource 관측 | 해당 node에 Collector/SDK 설치·권한·identity·clock 검사 필요 |
| Remote SandboxFusion | Client-side Tool/Reward span만 | 자동 API adapter·내부 resource/lifecycle 모니터링 미지원 |
| Managed / External provider | Client-side Tool/Reward span만 | 내부 queue·CPU/memory/I/O·실행 상태·cross-service attribution 미지원 |

```{admonition} Remote Sandbox — Not Supported (V1)
:class: important

Remote 서비스의 내부 모니터링과 자동 통합은 Future Work입니다. VERL에서 외부 서비스를 호출하는 것은 제한하지 않습니다. 기존 SDK로 명시한 client span·outcome은 볼 수 있지만 remote execution span, server queue time, SSD latency 또는 Run별 remote 사용량으로 해석하지 않습니다.
```

## 얻는 것

- 직접 계측한 acquire/exec/release 등 lifecycle span.
- Cgroup v2 CPU·memory·I/O·PSI와 지원되는 event counter.
- 같은 node/device의 local storage와 비교할 supporting evidence.

## 준비 조건

| 조건 | 확인 |
| --- | --- |
| 외부 runtime adapter | XLayer는 runtime·scheduler를 제공하지 않습니다. |
| 안정적인 worker cgroup v2 subtree | 실제 관측할 path와 권한 |
| Node identity | Colocated 또는 dedicated sandbox node 이름 |
| Logs/Timeline 필요 시 | [Loki와 EventRecorder](logs-events.md) 연결 |

```{admonition} Scope
:class: important

Docker CLI worker의 cgroup이 container resource를 포함한다고 가정하지 않습니다. Cgroup PSI와 node/device busy는 다른 scope이며 개별 trajectory의 SSD 사용량이 아닙니다.
```

GPU cluster에서 실행하는 colocated sandbox는 해당 GPU host의 로컬 SSD를 사용할 수 있습니다. Cgroup `io.stat`·I/O PSI와 같은 **sandbox node + device**의 Node Exporter BW·IOPS·mean latency·queue를 비교합니다. 이 경로는 backend Storage Cluster의 DS/MDS I/O와 별도입니다.

`sandbox.node`, `sandbox.device`, 실제 `device_major_minor`를 확인합니다. 같은 `nvme0n1` 이름만으로 다른 host를 연결하지 않습니다. OverlayFS·LVM·page cache·shared device에서는 cgroup의 device evidence가 physical SSD 요청의 완전한 attribution이 아니므로, tool→cgroup→device supporting/missing 경계를 유지합니다.

작업 중 처음 나타난 `io.stat` device도 관측 목록에 남지만, 이전 counter가 없으면 그 device의 delta는 계산하지 않습니다. Resource event는 producer 파일명 대신 event·Run·sandbox node·시간으로 찾습니다. 탐색은 최대 128 JSONL file·file당 4 MiB·총 16 MiB·8,192 record이며 읽기 실패나 한도 도달은 `read_incomplete`로 표시합니다.

## 1. Configure

1. [SandboxRecorder integration](integration-reference.md#record-lifecycle-spans)으로 실제 runtime lifecycle을 감쌉니다.
2. Dedicated 배치는 trace/parent context와 실제 sandbox node를 전달합니다.
3. [Sampler 설정](integration-reference.md#sample-the-sandbox-worker-cgroup)에서 실제 worker cgroup과 output path를 지정합니다.

![Colocated sandbox의 lifecycle과 worker cgroup 관측은 서로 다른 경계를 사용한다](figures/diagrams/sandbox-colocated.svg)

## 2. Start

선택한 adapter·sampler를 workload/collector 배포에서 시작합니다. Existing runtime을 종료하거나 새 sandbox scheduler로 대체하지 않습니다.

```bash
xltel status
xltel inspect RUN_ID
```

**정상 결과:** 기록한 sandbox span과 활성화한 cgroup source만 나타납니다. Pool occupancy는 runtime producer가 `sandbox_active`·`sandbox_queued`를 기록할 때만 채워집니다.

## 3. Verify

| 확인할 것 | 정상 결과 |
| --- | --- |
| Span | Trace/parent와 실제 start/end·duration |
| Cgroup | 지정한 subtree의 CPU·memory·I/O/PSI |
| Age | `Sandbox sample age`가 갱신됨 |
| Device evidence | 실제 backing device major:minor / node가 확인됨 |

## 볼 화면

**Stage Correlation → Sandbox signals**에서 sandbox node를 선택합니다. **Cross-Layer Timeline**에서 exact span과 sampled cgroup/device metric을 분리해 읽습니다.

## Record a Remote Client Call

**얻는 것:** Client에서 측정한 호출 소요시간, caller가 기록한 결과·timeout·retry 정보입니다. 기존 client의 timeout/retry 정책과 실제 호출을 유지합니다. XLayer는 API client나 retry loop를 만들지 않습니다.

```python
from pathlib import Path
from xlayer_telemetry.events import CorrelationContext, EventRecorder

events = EventRecorder(Path("artifacts/run-a/telemetry-events"),
    CorrelationContext(run_id="run-a", node="rollout-a", producer="tool_client",
                       role="rollout", worker_id="worker-0"))

async def observed_call(existing_client_call, *, step, attempt):
    # The caller supplies the real Tool or Reward API operation and timeout policy.
    with events.span("tool.call", phase="tool_interaction", step=step,
                     attributes={"tool": "code_execution", "deployment": "remote",
                                 "observation_scope": "client_call", "attempt": attempt}):
        return await existing_client_call()
```

Reward 호출은 같은 패턴에서 `reward.call`·`phase="reward"`로 기록할 수 있습니다. 이는 generic SDK instrumentation이며 모든 Reward span을 자동 bottleneck rule 입력으로 사용하는 기능은 아닙니다.

**정상 결과:** JSONL에 `node=rollout-a`의 monotonic call duration과 `status=ok/error`가 기록됩니다. `TimeoutError` 등 예외는 `error_type`으로 기록하고 원래 예외를 다시 전달합니다. Latency에는 client/network/remote queue/실행/응답 처리가 섞일 수 있으며 이를 분해하지 않습니다.

| 확인할 사실 | 기록 방법 | 자동으로 제공하지 않는 것 |
| --- | --- | --- |
| 호출 시간 / 예외 | 실제 호출을 `events.span(...)`으로 감쌈 | Remote server execution duration |
| API 응답·reward 결과 | Caller가 실제 응답을 확인한 뒤 `events.event("tool.result", ...)` 기록 | HTTP 오류·test/reward 성공 자동 분류 |
| Timeout | 기존 client에서 관측한 예외 또는 명시 outcome event | Timeout 정책·재시도 실행 |
| Retry | 실제 retry 결정/attempt를 별도 `tool.retry` event/attribute로 기록 | 자동 retry count metric·서버 중복 실행 판별 |

`Span.status=ok`는 client 코드가 예외 없이 끝났다는 뜻입니다. Remote 작업의 성공·reward correctness를 보장하지 않습니다. Span 개수·duration histogram·timeout/retry Prometheus counter도 자동 생성하지 않습니다. Secret·URL credential·prompt/code payload는 event attribute에 넣지 않습니다.

**확인:** Loki/Run Logs 또는 Timeline에서 `tool.call`·`reward.call`과 caller가 기록한 결과 event를 확인합니다. Remote service의 resource source가 없다면 Sandbox resource panel은 Missing/No data로 남아야 합니다. Client host의 GPU/CPU/disk metric을 remote 내부 자원으로 대체하지 않습니다.

## Troubleshooting

| 상태 | 행동 |
| --- | --- |
| Span만 있고 resource 없음 | Sampler·cgroup path·읽기 권한 확인 |
| Resource가 stale | Sampler process·sample age·textfile 확인 |
| Device를 알 수 없음 | Unknown으로 남기고 [device evidence](integration-reference.md#preserve-device-evidence-in-events) 확인 |
| Pool occupancy N/A | 해당 runtime producer가 값을 제공하는지 확인 |
| Remote call span은 있으나 Sandbox resource가 없음 | V1의 정상 지원 경계. Remote 내부 metric을 0/healthy 또는 client node의 값으로 채우지 않음 |

## 다음

[Candidate와 missing evidence](diagnosis.md) · [실환경 검증 범위](integration-reference.md#real-validation-coverage) · [Dedicated 배치](multi-node.md)
