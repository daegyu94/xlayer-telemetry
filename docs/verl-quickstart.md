# Connect VERL · 기존 명령 연결

**목표:** 동작하는 VERL command·Python 환경을 유지하고 첫 완료 step을 관측합니다.

## 얻는 것

| 연결 | 확인할 결과 |
| --- | --- |
| Wrapper + file logger bridge | 완료 step·stage duration·logger에 존재하는 scalar |
| Node collector | GPU·host metric |
| Optional Loki | 완료 step 목록·기록한 span/event·진단 projection |

```{admonition} 환경 분리
:class: note

Telemetry 환경은 VERL·CUDA를 설치하지 않습니다. 기존 VERL Python으로 workload를 실행합니다. Native vLLM/Ray와 tool/sandbox span은 별도로 연결합니다.
```

## 준비 조건

- 기존 VERL command가 XLayer 없이 동작함.
- [Quickstart](quickstart.md)·monitoring binary 설치 완료.
- GPU·dataset·model·CUDA 환경은 기존 VERL 배포에서 준비됨.

## 1. Configure

```bash
xltel config path
xltel config validate
xltel doctor
xltel install-tools
```

**정상 결과:** config validation 성공, 필요한 node/server 도구 확인. Bash launcher가 trainer mode를 숨긴다면 `--mode async` 또는 config의 `EXECUTION_MODE = "async"`를 사용합니다.

## 2. Start

```bash
xltel up
xltel run -- /path/to/verl-env/bin/python -m verl.trainer.main_ppo ...
```

`...`를 이미 동작하는 VERL argument로 바꿉니다.

**정상 결과:** up은 `Monitoring ready`를 출력합니다. Run은 고유 Run ID·artifact 위치를 안내하고 원래 workload 종료 코드를 반환합니다. Wrapper가 지원하는 trainer entrypoint에 `file` logger를 추가합니다.

```{admonition} Logger와 mode
:class: important

Stage·reward·throughput은 첫 step 완료 후 갱신됩니다. Bash launcher는 `VERL_FILE_LOGGER_PATH`와 `file` logger 전달을 담당합니다. vLLM rollout의 `mode=async`는 trainer boundary와 다른 설정입니다.
```

## 3. Verify

출력에 표시된 Run ID로 저장 artifact를 읽습니다.

```bash
xltel status
xltel inspect RUN_ID
```

| 확인할 것 | 정상 결과 |
| --- | --- |
| Workload | 원래 종료 코드 유지 |
| Snapshot | 첫 완료 `step`과 logger가 보고한 stage/scalar |
| Event history | `telemetry-events/verl-steps.jsonl`에 완료 step |
| Completeness | `telemetry-health.json`의 마지막 export 결과 확인 |
| Grafana | Run Overview에 선택한 Run의 snapshot·age |

```{admonition} Precision
:class: note

File logger의 step interval은 approximate입니다. 원래 event 시각이 없는 replay는 unknown이며 현재 step으로 표시하지 않습니다. Exact phase 경계는 직접 기록한 span이 필요합니다.
```

## 볼 화면

1. **Run Overview:** 완료 snapshot과 age 확인.
2. **Agent RL Stage Correlation:** 완료된 phase duration 비교.
3. [Loki](logs-events.md)를 연결했다면 완료 step의 Duration → What changed? / Evidence.

## Troubleshooting

| 상태 | 먼저 확인 |
| --- | --- |
| Workload는 성공, telemetry incomplete | `telemetry-health.json`·`telemetry-bridge.log` |
| Run이 없음 | Run ID·snapshot 경로·collector run parent |
| Stage가 없음 | 첫 step 완료·`file` logger·지원 scalar |
| Step 목록만 없음 | Loki·Alloy·step event·시간 범위 |
| vLLM·Ray가 N/A | [Native source](native-sources.md) 등록·실제 metric 이름 |

## 다음

[Logs & Events](logs-events.md) · [vLLM / Ray](native-sources.md) · [느린 Step 조사](dashboards.md) · [Wrapper / 종료 / freshness 상세](verl-reference.md)

:::{container} xlayer-legacy-links

<a id="connect-an-existing-verl-run" class="xlayer-legacy-anchor"></a>

[Connect an Existing VERL Run](verl-reference.md#connect-an-existing-verl-run)

<a id="what-this-adds" class="xlayer-legacy-anchor"></a>

[What This Adds](verl-reference.md#what-this-adds)

<a id="1-prepare-one-config-file" class="xlayer-legacy-anchor"></a>

[1. Prepare One Config File](verl-reference.md#1-prepare-one-config-file)

<a id="2-start-server-node-and-verl" class="xlayer-legacy-anchor"></a>

[2. Start Server, Node, and VERL](verl-reference.md#2-start-server-node-and-verl)

<a id="3-check-the-first-completed-step" class="xlayer-legacy-anchor"></a>

[3. Check the First Completed Step](verl-reference.md#3-check-the-first-completed-step)

<a id="check-telemetry-completeness" class="xlayer-legacy-anchor"></a>

[Check Telemetry Completeness](verl-reference.md#check-telemetry-completeness)

<a id="inspect-or-refresh-a-subsystem" class="xlayer-legacy-anchor"></a>

[Inspect or Refresh a Subsystem](verl-reference.md#inspect-or-refresh-a-subsystem)

<a id="add-sources-when-needed" class="xlayer-legacy-anchor"></a>

[Add Sources When Needed](verl-reference.md#add-sources-when-needed)

<a id="if-data-is-missing" class="xlayer-legacy-anchor"></a>

[If Data Is Missing](verl-reference.md#if-data-is-missing)

:::
