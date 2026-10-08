# Subsystem Deep Dive

**목표:** candidate를 지지하거나 반증할 추가 관측을 선택하고 한 조건씩 바꿔 확인합니다.

## 준비 조건

- [선택한 느린 interval](dashboards.md)의 Run·record·observer/resource·scope.
- [Comparable baseline](diagnosis.md#2-comparable-baseline-확인).
- 추가 source·profiler의 실제 실행 환경과 사용 가능한 자원.

## 무엇을 더 볼까?

| 관찰 | 다음 조사 | 확인할 한계 |
| --- | --- | --- |
| Rollout 지연 + queue 증가 | 같은 vLLM engine의 KV·TTFT·preemption | Shared engine signal |
| Rollout 지연 + 낮은 queue | Tool span·외부 호출 log | 없는 queue source를 낮은 queue로 읽지 않음 |
| Update 지연 + 낮은 GPU | Host staging·collective·memory | Node metric은 worker attribution이 아님 |
| Checkpoint 지연 | Client → network → filesystem → storage device | Local busy·I/O mean·service p99는 다른 scope |
| Weight sync 지연 | NIC/RDMA·replica 준비·실제 timer | Port bytes는 Run별 NCCL bytes가 아님 |
| Peer straggler | 같은 조건의 worker/device 비교 | Cluster 평균만으로 worker 판정하지 않음 |

## Log / Span / 저장 artifact

```bash
xltel inspect RUN_ID
```

**정상 결과:** 저장된 manifest·snapshot·step/span·선택적 diagnosis가 보입니다. Current service health는 `xltel status`로 별도 확인합니다.

## 짧은 Trace 수집

1. 의심 stage·rank·interval을 좁힙니다.
2. [VERL profiler 설정](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/verl/torch-profiler.yaml)을 실제 workload에 적용합니다.
3. 실제 trace를 PyTorch Profiler·Nsight 등 기존 도구에서 엽니다.
4. Profiler를 끈 실행에서 개선이 유지되는지 확인합니다.

```{admonition} Profiling boundary
:class: important

Profiler artifact는 Grafana에 자동 표시되지 않습니다. `run_profile.sh`는 synthetic GPU workload의 수집/overhead recipe이며 실제 VERL throughput 검증이 아닙니다.
```

## 변경 후 Verify

| 유지할 조건 | 확인할 결과 |
| --- | --- |
| Model·batch·sequence·concurrency·cache·topology | 비교 가능한 workload |
| Profiler 없이 재실행 | 실제 duration/throughput 개선 |
| 동일 correctness 기준 | Loss·reward·workload correctness |
| 동일 source·entity·window | Regression과 missing evidence |

## 상세 recipe

[Selected-rank trace](dashboard-reference.md#capture-a-short-trace) · [Synthetic profile](dashboard-reference.md#practice-with-a-synthetic-profile) · [NCCL baseline](dashboard-reference.md#measure-a-communication-baseline) · [Storage path](dashboard-reference.md#follow-the-storage-path)

## 다음

[Baseline / Evidence](diagnosis.md) · [실환경 기록](real-verl-demo.md) · [운영 한계](architecture-reference.md#failure-boundaries-and-operating-limits)
