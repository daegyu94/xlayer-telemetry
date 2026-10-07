# Configuration

**목적:** 같은 config로 시작·조회·종료하고, 실행 환경과 보관 artifact를 분리합니다.

## 설정 파일 선택

| 우선순위 | 입력 |
| --- | --- |
| 1 | `xltel --config FILE ...` |
| 2 | `XLAYER_CONFIG` |
| 3 | 기본 경로의 config; 기존 `config.conf`가 있으면 계속 사용 |

```bash
xltel config path
xltel config show
xltel config validate
```

**정상 결과:** path는 선택한 파일, show는 resolved setting, validate는 exit code `0`입니다. Show는 workload argv·credential을 공개하지 않습니다.

## TOML 형식

```toml
[telemetry]
TELEMETRY_HOME = "~/telemetry"
RUN_ID = "auto"
ENABLE_LOGS = true
GF_USERS_DEFAULT_THEME = "light"

[workload]
# 이미 동작하는 기존 argv를 배열로 저장합니다.
# command = ["/path/to/verl-env/bin/python", "-m", "verl.trainer.main_ppo"]

[environment]
# child 환경으로 전달할 문자열
# CUDA_VISIBLE_DEVICES = "0,1"
```

| 절 | 의미 |
| --- | --- |
| `[telemetry]` | 알려진 uppercase 설정 key |
| `[workload]` | Command argv 배열 |
| `[environment]` | 추가 child environment; 같은 terminal 값이 우선 |

## 값의 우선순위와 경로

- Command option → 같은 이름의 environment → config → default.
- TOML path는 `~`만 확장합니다. `$HOME`·command substitution은 해석하지 않습니다.
- Boolean은 `true`/`false`; unknown key와 잘못된 type은 시작 전에 거부합니다.
- `TELEMETRY_HOME`은 tools/state/run parent의 기본 경로를 정합니다.
- `RUN_ID = "auto"`는 매 실행의 고유 ID를 생성합니다. 고정 ID는 새 Run마다 변경합니다.

```{admonition} Trusted Bash
:class: important

기존 Bash config는 실행 가능한 신뢰된 파일입니다. 보안 sandbox가 아닙니다. Command는 `VERL_COMMAND=(...)` 배열로 보존하며 TOML로 migration해도 원본 파일은 유지됩니다.
```

## 기능별 설정

| 목표 | Key / 적용 | Guide |
| --- | --- | --- |
| CPU-only collector | `ENABLE_GPU_METRICS = false`; collector restart | [GPU & Host](monitoring.md) |
| Log·step·span | `ENABLE_LOGS = true`; node/server restart | [Logs & Events](logs-events.md) |
| Native endpoint | `TELEMETRY_SOURCES_FILE`; 최초 server restart, 이후 refresh | [vLLM / Ray](native-sources.md) |
| Diagnosis | `DIAGNOSTICS_CONFIG`; 새 Run | [Diagnose](diagnosis.md) |
| Prometheus retention | `PROMETHEUS_RETENTION`; server restart | [운영 Reference](monitoring-reference.md#retain-data-for-completed-runs) |
| Native/scrape budget | Bounded sample/target/body/interval 설정 | [Metrics limits](metrics-reference.md#bounded-collection-and-pressure-evidence) |

## 검증과 변경

```bash
xltel config validate
xltel restart --role server
xltel status
```

**정상 결과:** config validation과 backend health를 각각 확인합니다. Collector 입력·node·run parent를 바꾸면 collector도 해당 config로 재시작합니다.

## 다음

[CLI의 설정·migration 상세](cli.md#configuration) · [Monitoring lifecycle](monitoring-reference.md#check-and-stop) · [Architecture](architecture.md)
