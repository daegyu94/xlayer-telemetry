# Logs & Events 연결

**목표:** 완료 step·기록한 span/event·진단 projection을 Loki에 보내 같은 시간으로 조사합니다.

## 얻는 것

- Run Overview의 완료 step 목록.
- Cross-Layer Timeline의 실제 span·recorded event.
- Run Logs의 workload `.log` 조회.

## 준비 조건

- [Monitoring](monitoring.md)와 run artifact parent가 준비됨.
- Loki·Alloy binary 설치; source를 읽는 collector에 파일 접근 권한.
- Workload log는 `<run-directory>/logs/**/*.log`에 기록됨.

## 1. Configure

기존 TOML `[telemetry]`에 추가합니다.

```toml
ENABLE_LOGS = true
```

```{admonition} 수집 범위
:class: note

Terminal stdout 전체와 VERL metric JSONL은 자동으로 Run Logs에 들어오지 않습니다. EventRecorder JSONL·완료 step·diagnosis projection과 `logs/**/*.log`를 읽습니다.
```

## 2. Start

```bash
xltel config validate
xltel restart
xltel status
```

**정상 결과:** Loki endpoint가 healthy이고 Alloy를 포함한 collector가 동작합니다. Remote host는 관리망 Loki 주소와 log root를 [운영 Reference](monitoring-reference.md#add-run-logs-with-loki)에 따라 지정합니다.

## 3. Verify

```bash
xltel inspect RUN_ID
```

| 결과 | 확인 |
| --- | --- |
| 완료 step | `telemetry-events/verl-steps.jsonl`에 시간 provenance 포함 |
| Event / span | EventRecorder JSONL에 실제 경계·trace/parent context |
| Workload log | Run directory의 `logs/` 아래 `.log` |
| Grafana | 동일 Run·observer·시간의 step 목록 / Timeline / Logs |

## Exact span 기록

SDK를 사용하는 workload 환경에 설치한 뒤 [EventRecorder 예제](integration-reference.md#record-a-custom-tool-span)를 사용합니다.

실제 Run ID·node·collector가 읽는 run parent에 맞춥니다. Custom config에서는 아래 path도 함께 바꿉니다.

```bash
export TELEMETRY_RUN_ID='grpo-001'
export TELEMETRY_NODE='gpu-local'
export TELEMETRY_EVENTS_DIR="$HOME/telemetry/runs/grpo-001/telemetry-events"
```

```python
from xlayer_telemetry.events import EventRecorder

events = EventRecorder.from_env(producer="agent", role="rollout")
if events is not None:
    with events.span("tool.call", phase="tool_interaction", step=1,
                     attributes={"tool": "pytest"}):
        pass  # 실제 tool 호출로 바꿉니다.
```

**정상 결과:** 실제 실행 전후 timestamp와 duration을 가진 JSONL span이 생성됩니다. Policy·checkpoint·KV event는 해당 코드가 직접 기록할 때만 나타납니다.

```{admonition} Precision / scope
:class: important

Exact span, approximate step, sampled metric은 다른 관측입니다. Clock uncertainty와 parent 관계를 확인합니다. Shared storage의 파일을 여러 collector가 중복 전송하지 않습니다.
```

## Troubleshooting

| 상태 | 행동 |
| --- | --- |
| Step 목록만 비어 있음 | ENABLE_LOGS·Alloy·step file·시간 provenance·retention 확인 |
| Log 없음 | `.log` path와 Log directory 확인; telemetry Run ID와 다를 수 있음 |
| Span 없음 | 실제 계측·event directory·trace/time filter 확인 |
| Candidate 없음 | [Diagnostics](diagnosis.md) 설정·final 결과·projection 확인 |

## 다음

[Slow Step Investigation](dashboards.md) · [Sandbox](sandbox.md) · [Loki remote / retention 상세](monitoring-reference.md#add-run-logs-with-loki)
