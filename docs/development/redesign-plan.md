# XLayer Telemetry 재설계 — 반복 버그 예방을 위한 최소 실행 계획

이 계획의 목적은 기존 기능에서 반복되는 버그를 공통 데이터 계약과 단일 판단 경로로 줄이는 것입니다. 기존 전체 계획의 Phase 0–8 일괄 구현 조건을 이 문서의 최소 범위로 교체하며, 새 metric·KPI·rule 추가는 완료 조건에서 제외합니다.

## G. 실행 계약

### G.1 목표와 구현 방향

Identity·scope 유실, missing과 실제 zero 혼동, 시간·단위 drift, Rule/LLM의 중복 보정, UI의 문장 기반 상태 판정을 구조적으로 줄입니다. 현재 제공하는 진단 기능 전체를 대상으로 공통 계약을 적용하며, rollout/tool 사례만 지원하는 제품으로 축소하지 않습니다.

기존 내부 구현은 교체·삭제할 수 있지만, 목표 디렉터리 모양이나 파일 수를 맞추는 작업은 요구하지 않습니다. 실제 호출 경로에 공통 계약이 연결되고, 대체된 중복 판단 코드가 제거됐으며, 같은 오류를 다시 넣으면 검사가 실패하는 것이 완료 근거입니다.

모든 버그가 발생하지 않는다는 보장은 할 수 없습니다. 이번 범위에서는 아래 반복 결함군의 예방 장치를 구현하고, 이를 벗어나는 구조 변경은 별도 후속 과제로 둡니다.

### G.2 권한과 경계

- 이번 계획 수정은 구현 실행이나 새 goal 등록을 뜻하지 않습니다. 실행을 요청받으면 이 최소 계획만 수행하고, 기존 전체 계획의 필수 조건을 되살리지 않습니다.
- 기존 Phase 0의 완료 작업과 검증 기록은 유지합니다. WP-06은 진행 중인 변경과 검증을 확인한 뒤 마무리하며, 계획 수정만으로 완료 처리하지 않습니다.
- 기본은 작업자 한 명의 순차 실행입니다. 별도 모델 분업과 병렬 작업은 현재 대화에서 명시적으로 요청한 경우에만 적용합니다.
- 각 작업은 로컬 main 기준 branch에서 수행하고 검증·Signed-off-by commit·fast-forward 병합 후 이번 branch만 정리합니다. 원격 push·PR·배포·Actions 설정 변경은 별도 요청 사항입니다.
- 사용자 workload·monitoring stack·config·저장된 artifact를 보존하고, 검증 자원은 격리해 이번 작업이 만든 것만 정리합니다.

### G.3 기준점과 재개

최소 범위로 전환할 때 확인한 로컬 main은 `5b51e268970f87d76f691c4d492a7a182b48daca`입니다. 기존 계획 반입 기준과 Phase 0 결과는 [진행표](redesign-status.md)에 보존하며, 실제 구현 시작 시 최신 main과 테스트 baseline을 다시 확인합니다.

기존 전체 계획은 Git 이력에 보존합니다. 범위 전환 전 문서는 `git show 5b51e268970f87d76f691c4d492a7a182b48daca:docs/development/redesign-plan.md`로 확인할 수 있으며, 과거 metric 수·결함 수·실행 시간은 현재 측정값으로 재사용하지 않습니다.

### G.4 전체 완료 조건

1. 아래 R-00–R-05가 검증·로컬 main 병합 근거와 함께 done입니다. 후속 과제로 옮긴 기존 WP는 done으로 표시하지 않습니다.
2. Rule·standalone LLM·저장 보고서 기반 LLM이 공통 observation/evidence 계약을 실제로 사용합니다. LLM이 볼 수 있는 관측값을 Rule 후보로 제한하지 않습니다.
3. 현재 지원하는 진단 signal의 이름·unit·statistic·scope 정의가 공통 catalog로 연결되고, UI가 사용하는 대응 관계는 drift 검사로 확인합니다.
4. Entity/time/status 검증과 후보 강도 제한이 공통 경로에 있으며, 호출자별 중복 보정은 제거됐습니다. 새 타입 선언이나 파일 이동만으로 완료 처리하지 않습니다.
5. 기존 CLI·config·argv/exit code·artifact reader·dashboard UID/URL과 optional source 동작을 보존합니다. 의도한 동작 변경은 ADR과 변경 전후 검사로 명시합니다.
6. 최종 통합 tree의 전체 CPU 검사, frontend 검사, 영향받는 실제 Grafana/browser 경로, 문서 build, 설치본 smoke와 generator/contract drift 검사가 통과합니다. 필수 로컬 검사 실패·미실행은 완료로 처리하지 않습니다.
7. Skip·실환경 Pending·cleanup·최종 SHA를 구분해 기록합니다. 실제 GPU/VERL·물리 multi-node·운영 3FS·실제 모델 추론과 원격 적용은 별도 검증표에 남깁니다.

## 1. 최소 설계와 범위

| 반복 결함군 | 이번 구조 변경 | 예방 장치 |
| --- | --- | --- |
| 다른 engine/device의 신호 혼합 | EntityRef·Scope를 observation과 함께 전달하고 공통 matcher에서 비교 | cross-engine/device/rank 반례와 실제 query selector 검사 |
| missing/stale/error가 zero로 변환 | Observation에 value·status·Issue를 묶고 입력 경계에서 검증 | observed zero와 no data/error를 같은 public 경로에서 구분 |
| clock/window와 단위 불일치 | TimeWindow에 provenance·clock uncertainty를 보존하고 signal metadata를 한곳에서 관리 | invalid/unknown/calibrated window와 unit/statistic drift 검사 |
| Rule/LLM 판정 경계 불일치 | 공통 collect/assess/EvidenceBundle과 단일 candidate cap | observation parity와 여러 품질 제한의 동시 적용 검사 |
| UI 문구가 상태·drill-down을 변경 | 공통 row decoder, 구조화된 reason, resource context 변환 | 문구 변경·unknown field·entity 누락·retry 후 복구 검사 |

외부 JSON·config·backend 응답을 읽는 경계에는 dict를 사용할 수 있습니다. 경계 이후의 진단 흐름은 검증된 타입을 사용하고, 출력 경계에서 기존 artifact 형식으로 직렬화합니다. 공통 타입은 analysis/frontend 내부로 우선 적용하며 SDK·collector 전체를 동시에 이관하지 않습니다.

Transport의 DNS/header/body deadline·byte cap·취소·child cleanup은 유지합니다. Query 실패를 공통 Issue로 바꾸는 경계를 통합하되, 이 목적만으로 HTTP transport를 재작성하거나 기존 subprocess 격리를 제거하지 않습니다.

## 2. 실행 순서

R-00 → R-01 → R-02 → R-03 → R-04 → R-05 순서로 수행합니다. 필요한 caller·fixture·schema·export·package asset을 각 작업에서 함께 갱신하며, 별도 기능을 선행 조건으로 추가하지 않습니다.

### R-00: 안정화 마무리와 계약 baseline

- **작업:** 진행 중인 WP-06의 실제 변경과 gate 연결을 확인해 마무리합니다. 전체 CPU 결과를 기록하고, 기존 diagnosis·standalone LLM·저장 artifact·UI row를 연결하는 소수의 대표 fixture와 characterization을 만듭니다.
- **범위 제한:** 변경할 진단 경로의 fake/builder만 공용화합니다. 전체 테스트 파일 재배치나 모든 fake 교체는 수행하지 않습니다.
- **완료 기준:** 정상·no baseline·optional source 장애·observed zero·다른 entity·unsafe clock 사례의 기존 결과와 실패 경계가 고정됩니다. 잘못된 기존 결과는 불변 golden으로 고정하지 않고 실패 regression과 의도한 변경으로 기록합니다.

### R-01: 진단 도메인 계약

- **작업:** EntityRef·Scope·TimeWindow·Observation·Issue와 EvidenceBundle의 필요한 최소 필드를 정의합니다. 현재 window/clock/quality helper를 공통 계약에 연결하고, I/O decoder와 기존 형식 serializer를 둡니다.
- **보존:** 원래 시각과 reference/uncertainty, source/query identity, unit/statistic, current/baseline, missing reason을 함께 전달합니다. 유효한 zero는 관측값이고, unknown을 zero로 기본화하지 않습니다. Clock 품질과 관측 상태는 별도 차원으로 보존합니다.
- **완료 기준:** invalid/non-finite/reversed window, unknown clock, no data/error/stale, 같은 entity와 다른 entity를 검사합니다. 공통 타입·새 판단 모듈에 좁은 mypy 검사를 연결하고, 다음 R-03에서 실제 Rule/LLM 호출 경로에 적용합니다. 저장 schema를 불필요하게 변경하지 않습니다.

### R-02: 현재 signal 정의의 단일 기준

- **작업:** 기존 MetricQuery/METRIC_PROFILES를 출발점으로 현재 진단 signal의 query·scope·unit·statistic·baseline 조건을 한 catalog에 모읍니다. DEFAULT_QUERIES, SIGNAL_SCOPE, BASELINE_REQUIRED와 reducer metadata가 이를 사용하게 합니다.
- **언어 경계:** App에서 사용하는 signal ID·unit·scope·panel key 대응만 공통 catalog와 기계적으로 대조합니다. 같은 metadata를 TS가 필요로 하면 그 작은 부분만 생성하고, query와 panel 원본은 기존 canonical dashboard를 유지합니다. 사용자 정의 LLM query도 입력 adapter에서 같은 계약으로 검증합니다.
- **완료 기준:** 현재 signal을 누락하지 않고, alias와 실제 source identity를 유지합니다. 이름·unit·scope·statistic 또는 panel 대응 하나를 어긋나게 바꾸면 drift 검사가 실패하며 package 설치본에서도 catalog를 읽을 수 있습니다.

### R-03: 공통 evidence와 단계별 진단

- **작업:** DiagnosticEngine.analyze의 I/O와 판단을 collect/align → baseline/select/assess → evaluate/cap → assemble 경계로 분리합니다. Rule·standalone LLM·packet_from_report가 공통 evidence builder/decoder를 사용하게 합니다.
- **핵심:** Entity 선택, workload/boundary 비교, sampling/clock 품질, missing 처리와 강도 제한을 호출자별로 다시 계산하지 않습니다. I/O는 budget 안에서 실행하고, 판단 단계는 입력이 같으면 결과가 같은 함수로 만듭니다. 정상 Rule 결과와 LLM hypothesis는 각자의 의미를 유지합니다.
- **보존:** LLM의 사용자 정의 query, sensitive payload allowlist, 입력 크기 제한, evidence 참조 검증을 유지합니다. Report에 없는 정보는 저장 보고서 기반 LLM에서도 unknown으로 남기며, standalone 경로와 관측 입력이 같을 때만 parity를 요구합니다.
- **완료 기준:** 기존 지원 rule 전체의 characterization과 negative 검사가 통과합니다. Shared-only·unsafe clock·stale·partial·다른 entity 제한을 순서와 중복 적용에 무관하게 검사하고, 대체된 중복 collection/quality/cap 코드를 제거합니다. Facade가 조립된 단계를 호출하며 복잡성을 다른 거대 함수로 옮기지 않습니다.

### R-04: Frontend 계약 경계

- **작업:** 진단 row를 공통 decoder와 TS 타입으로 읽고, 상태/clock reason과 resource identity 변환을 해당 UI 경로에서 공통화합니다. PhaseMatrix의 문장 startsWith 분기 등 의미 판정은 구조화된 상태·reason을 사용하게 합니다.
- **범위 제한:** InvestigationView 전체 재작성과 Router/Scenes 전체 이관은 별도 과제입니다. 이번 변경에서 건드리는 선택 상태는 기존 URL/Scenes를 기준으로 사용하고, 같은 state의 추가 복제는 만들지 않습니다.
- **완료 기준:** 문구만 바꿔도 의미 판정이 유지되고, measured zero/no data/stale/error/unknown과 unknown additive field를 안전하게 표시합니다. Entity/window 보존, Reload·Back·Forward·공유 링크, cached page·optional source 장애·retry 후 회복을 영향받는 실제 browser journey에서 확인합니다. npm test/typecheck/build와 관련 ci_demo.py journey에 검사를 연결합니다.

### R-05: 중복 제거와 완료 증명

- **작업:** R-01–R-04로 대체한 구 판단 코드·metadata 사본을 삭제하고, 실제 호출 경로를 검색해 우회 경로가 남지 않았는지 확인합니다. 영향받은 사용자 문서·schema·package asset만 갱신합니다.
- **완료 기준:** G.4를 확인하고 동일 최종 source tree의 검증 범위·실패/skip·실환경 Pending·cleanup을 진행표에 기록합니다. 전체 저장소의 파일 이동·지침 축소·과거 validation 문서 정리는 완료 조건에 추가하지 않습니다.

## 3. 검증과 실행 방법

기존 [Quality Goals](quality-goals.md), [Quality Backlog](quality-backlog.md), AGENTS.md의 사용자 계약을 유지합니다. 기존 test 기대값을 약화하거나 skip을 늘려 green을 만들지 않고, baseline 실패와 새 실패를 test 이름·실패 조건으로 비교합니다.

| 시점 | 검사 |
| --- | --- |
| 각 R 작업 | 관련 public entrypoint regression, 공통 계약 반례, 기존 check.sh default gate, 새 공통 타입/판단 모듈 mypy |
| R-03 통합 | 전체 CPU suite, Rule/LLM observation parity, stage/report characterization, old artifact reader |
| R-04 | npm test/typecheck/build, 실제 Grafana/browser와 helper 검사, Light/Dark·390px 등 영향 화면 |
| R-05 | 최종 전체 CPU·Python 3.10/3.12·설치본 smoke, 영향받는 실제 Prometheus/ClickHouse query/protocol, 전체 frontend/browser·문서 build·contract drift |

```bash
bash scripts/check.sh default tests/test_diagnostics.py tests/test_llm_diagnosis.py
python -m pytest -q
python -m pytest -q grafana/xlayer-app/scripts
python -m pytest -q tests/test_documentation_links.py tests/test_documentation_diagrams.py
bash scripts/docs.sh build
```

최종 full gate에는 기존 check.sh가 요구하는 격리 backend와 browser 도구를 준비합니다. 도구가 없어 skip한 검사를 통과로 보고하지 않으며, 환경 차이에 따른 대체 검사는 같은 fixture/assertion과 미검증 환경을 명시합니다. 문서만 바꾸는 이번 범위 전환에는 문서 검사와 build를 적용합니다.

각 R 작업 시작 시 main SHA와 working tree를 확인합니다. 구현·회귀 검증·staged diff 확인·commit·로컬 병합을 완료하고 다음 R 작업으로 진행하며, status의 done은 검증과 병합 이후에만 기록합니다. 이 계획을 목표로 등록한 뒤에는 후속 과제로 옮긴 작업까지 자동 확장하지 않습니다.

## 4. 이번 완료 조건에서 제외한 후속 과제

| 후속 과제 | 다시 시작할 조건 |
| --- | --- |
| 모든 metric/producer·T0/T1·Agent RL KPI·신규 rule 구현 | 사용자가 새 진단 기능을 요청하고 필요한 실제 입력/source가 준비됨 |
| 전체 Registry와 Python/TS/dashboard/docs/demo codegen | 최소 catalog 이후에도 실제 metadata drift가 남고 작은 생성 범위로 해결되지 않음 |
| Config resolver 전면 통합·Python supervisor·launcher 이관 | Phase 0 이후에도 config/ownership 중복이 원인인 결함이 재현되거나 별도 이관 요청이 있음 |
| Backend package 전면 교체·transport 재작성 | 공통 collection 경계로 해결할 수 없는 transport 결함과 대체 구현의 필요성이 입증됨 |
| Grafana App 전면 view-model/Router 재설계 | R-04 이후에도 state/lifecycle 문제를 좁은 공통 경계에서 해결할 수 없음 |
| 전체 테스트 재배치·coverage 확대·AGENTS/문서 정리 | 별도 유지보수 목표로 요청됨 |

후속 과제는 필요 없다는 판정이 아니라 이번 최소 목표의 범위 밖입니다. 진행 중인 코드 변경에서 발견한 새 회귀와 위 필수 계약 위반은 이번 작업 안에서 해결하며, 이를 후속 과제라는 이유로 남기지 않습니다.

## 5. 기존 WP에서 전환

| 기존 WP | 최소 계획의 처리 |
| --- | --- |
| WP-00–08 | 완료 이력 유지; 진행 중인 WP-06은 R-00에서 확인·마무리 |
| WP-10–12 | R-00의 관련 fixture/characterization, R-05의 필요한 의존 경계 검사로 축소 |
| WP-20–23 | R-01과 R-03의 진단 계약·좁은 타입 검사로 통합 |
| WP-30–32 | R-02와 R-04의 현재 signal 정의와 소비자 drift 검사로 축소 |
| WP-40–45 | R-03의 공통 evidence·오류 경계·단계별 판단으로 통합; transport 전면 교체 제외 |
| WP-46 | R-01/R-03/R-04에서 필요한 row serializer/decoder·old reader 검증만 포함 |
| WP-60–62 | R-04의 상태/reason/identity 경계로 축소 |
| WP-80–81 | R-05의 이번 변경 정리·최종 검증으로 축소 |
| WP-13·33–34·50–52·70–72 | 후속 과제로 이동; 이번 목표에서 수행하지 않음 |

진행표의 과거 완료 SHA·검증 결과는 유지하고, 기존 미완료 WP는 새 범위로 재분류합니다. 기존 전체 계획을 읽던 재개 프롬프트도 [최소 실행 프롬프트](codex-prompt.txt)로 갱신합니다.
