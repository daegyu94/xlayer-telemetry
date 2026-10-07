# Source 선택 · 필요한 관측부터 연결

**목표:** 현재 질문에 필요한 source를 하나씩 추가하고 실제 값·scope를 확인합니다.

## 무엇을 보고 싶은가?

| 질문 | 연결할 Source | 확인할 곳 |
| --- | --- | --- |
| 어느 완료 phase가 느린가? | [VERL file logger bridge](verl-quickstart.md) | Stage Correlation |
| GPU/host가 함께 변했나? | [GPU & Host collector](monitoring.md) | Compute & Communication |
| Rollout queue·TTFT·KV가 변했나? | [vLLM endpoint](native-sources.md) | Stage Correlation의 vLLM |
| Ray task·object store가 변했나? | [Ray endpoint](native-sources.md) | Ray orchestration |
| KV store/retrieve·DFS 단계인가? | [Mooncake / Storage](kv-storage.md) | Policy & KV / Mooncake row |
| Tool·sandbox의 실제 시간이 필요한가? | [Events / Span](logs-events.md)·[Sandbox](sandbox.md) | Cross-Layer Timeline |
| Step별 후보를 자동 생성할까? | [Diagnostics config](diagnosis.md#1-진단-연결) | Bottleneck Summary |
| 다른 host의 자원도 볼까? | [Multi-node](multi-node.md) | Cluster·resource node 선택 |

## 연결 순서

1. GPU/host와 VERL의 첫 완료 step을 확인합니다.
2. 질문에 필요한 native endpoint 또는 span을 추가합니다.
3. `xltel sources`·`status`와 실제 metric 이름으로 연결을 검증합니다.
4. 같은 시간·entity·scope의 current/baseline을 비교합니다.

```{admonition} Source와 attribution
:class: important

Manifest나 endpoint 설정만으로 수집 성공이 증명되지 않습니다. Native vLLM·Ray·storage는 shared signal이며 `Run` filter로 자동 분리되지 않습니다.
```

## 연결 전후 체크

| 확인 | 방법 |
| --- | --- |
| Endpoint 응답 | `/metrics` HTTP 응답과 필요한 metric 이름 |
| Scrape 결과 | `xltel sources`; target Up만으로 모든 metric 존재를 보증하지 않음 |
| Identity | Cluster·node·worker·engine/device가 실제 배치와 일치 |
| Time | Age·clock·rate window 확인 |
| Missing | N/A·stale·query error와 measured zero 구분 |

## 다음

[Native source 연결](native-sources.md) · [Logs & Events](logs-events.md) · [Integration 상세 Reference](integration-reference.md)

:::{container} xlayer-legacy-links

<a id="cross-layer-integration-for-verl" class="xlayer-legacy-anchor"></a>

[Cross-Layer Integration for VERL](integration-reference.md#cross-layer-integration-for-verl)

<a id="how-the-signals-flow" class="xlayer-legacy-anchor"></a>

[How the Signals Flow](integration-reference.md#how-the-signals-flow)

<a id="choose-the-next-source" class="xlayer-legacy-anchor"></a>

[Choose the Next Source](integration-reference.md#choose-the-next-source)

<a id="inspect-one-subsystem" class="xlayer-legacy-anchor"></a>

[Inspect One Subsystem](integration-reference.md#inspect-one-subsystem)

<a id="register-native-endpoints" class="xlayer-legacy-anchor"></a>

[Register Native Endpoints](native-sources.md)

<a id="observe-mooncake-kv-storage" class="xlayer-legacy-anchor"></a>

[Observe Mooncake KV Storage](kv-storage.md)

<a id="register-the-endpoints" class="xlayer-legacy-anchor"></a>

[Register the Endpoints](integration-reference.md#register-the-endpoints)

<a id="interpret-the-evidence" class="xlayer-legacy-anchor"></a>

[Interpret the Evidence](integration-reference.md#interpret-the-evidence)

<a id="map-multiple-nodes-to-a-run" class="xlayer-legacy-anchor"></a>

[Map Multiple Nodes to a Run](integration-reference.md#map-multiple-nodes-to-a-run)

<a id="add-diagnostics" class="xlayer-legacy-anchor"></a>

[Add Diagnostics](diagnosis.md#1-진단-연결)

<a id="understand-asynchronous-runs" class="xlayer-legacy-anchor"></a>

[Understand Asynchronous Runs](integration-reference.md#understand-asynchronous-runs)

<a id="record-a-custom-tool-span" class="xlayer-legacy-anchor"></a>

[Record a Custom Tool Span](integration-reference.md#record-a-custom-tool-span)

<a id="observe-an-agent-sandbox" class="xlayer-legacy-anchor"></a>

[Observe an Agent Sandbox](sandbox.md)

<a id="record-lifecycle-spans" class="xlayer-legacy-anchor"></a>

[Record lifecycle spans](integration-reference.md#record-lifecycle-spans)

<a id="real-validation-coverage" class="xlayer-legacy-anchor"></a>

[Real validation coverage](integration-reference.md#real-validation-coverage)

<a id="sample-the-sandbox-worker-cgroup" class="xlayer-legacy-anchor"></a>

[Sample the sandbox worker cgroup](integration-reference.md#sample-the-sandbox-worker-cgroup)

<a id="preserve-device-evidence-in-events" class="xlayer-legacy-anchor"></a>

[Preserve Device Evidence in Events](integration-reference.md#preserve-device-evidence-in-events)

<a id="enable-the-optional-diagnosis-rule" class="xlayer-legacy-anchor"></a>

[Enable the optional diagnosis rule](integration-reference.md#enable-the-optional-diagnosis-rule)

<a id="optional-integrations" class="xlayer-legacy-anchor"></a>

[Optional Integrations](integration-reference.md#optional-integrations)

<a id="record-sandbox-execution-outcomes" class="xlayer-legacy-anchor"></a>

[Record Sandbox Execution Outcomes](integration-reference.md#record-sandbox-execution-outcomes)

:::
