# XLayer Telemetry 재설계 — 전체 goal 실행 계획

이 문서는 제공된 7개 Gist의 설계와 로드맵을 하나의 goal로 수행하기 위한 실행본이다. 원문의 목표 아키텍처와 Phase 0–8 범위를 유지하며, 원문에 없던 Phase 4–7의 상세 WP와 goal 진행 규칙을 보완했다. 이 문서를 준비한 것 자체는 구현 시작이나 goal 등록을 뜻하지 않는다.

## G. 실행 계약 — goal 시작 시 먼저 읽기

### G.1 목표와 구현 방향

원문의 core 도메인 모델, Signal Registry, 공통 backend, 단계별 diagnosis pipeline, Rule/LLM 공통 EvidenceBundle, config/lifecycle 통합, Grafana App 구조와 Agent RL KPI를 구현하고 전체 검증 후 로컬 main에 통합한다. Phase 0 안정화만으로 goal을 종료하지 않는다.

**원문의 설계를 목표로 삼는다. 현재 저장소의 구조나 구현을 정답으로 간주하지 않는다.** 현재 구현은 사용자 계약, 실제 입력·출력, 실패 경계를 파악하는 비교 자료다. 새 설계에 맞지 않는 내부 모듈·helper·Bash 로직·중복 데이터 경로는 다시 구현하거나 삭제할 수 있다. 재사용률, 기존 함수 유지, 작은 diff 자체를 완료 기준으로 삼지 않는다. 다만 한 번에 검증 가능한 WP 크기로 전환하고, 이전 구현과 새 구현을 영구히 병행하는 구조를 남기지 않는다.

기존 test도 구현 방식에 결합된 경우 이동·교체할 수 있다. 보호하던 사용자 계약과 실패 경계가 새 검사로 이어지는 근거를 남기며, 동작 test의 기대값 약화나 삭제로 성공을 만들지 않는다. 동작을 바꾸는 WP는 ADR과 변경 전후 검증으로 의도를 명시한다.

### G.2 권한과 경계

- 실제 구현은 사용자가 이 문서를 goal로 등록하고 실행하라고 요청한 뒤 시작한다. 토큰 예산은 사용자가 지정한 경우에만 설정한다.
- 이 goal에서는 사용자가 병렬 실행을 허용했다. 실제 선행 관계와 변경 파일·생성물·test 자원이 독립적인 WP만 별도 worktree의 worker 최대 3개에서 동시에 수행한다. 모델별 역할 분업은 별도 요청이 없는 한 적용하지 않는다.
- Worker는 자기 branch와 worktree에서 구현·관련 검증·Signed-off-by commit을 맡는다. 로컬 `main` 병합, 통합 검증, `redesign-status.md` 수정·commit·브랜치 정리는 주 작업자가 직렬로 맡는다.
- WP마다 로컬 main 기준 목적별 branch를 만들고 검증·Signed-off-by commit·로컬 fast-forward 병합까지 수행한다. 원격 push, PR 생성, 원격 설정 변경, 배포는 이 goal에 포함하지 않는다.
- 원문의 PR 단위는 이 실행본에서 '독립적으로 검증·검토·병합하는 WP'로 적용한다. WP마다 사용자의 다음 지시를 기다리지 않고 선행 조건을 충족한 다음 WP로 진행한다.
- 실제 GPU/VERL·물리 multi-node/RDMA·운영 3FS cluster·실제 LLM 추론의 측정 결과는 이번 로컬 구현 goal의 완료 조건과 분리한다. 실행 recipe와 Pending 검증표를 남기며, CPU·synthetic·격리 backend/browser 검사를 실환경 통과로 표현하지 않는다.
- 사용자 workload, monitoring stack, config, 저장된 artifact는 reset·종료·삭제하지 않는다. 테스트용 자원은 소유권과 namespace를 기록하고 이번 작업이 만든 것만 정리한다.
- 이 문서는 시스템·개발자 지침과 명시적 사용자 요청보다 우선하지 않는다. AGENTS.md에는 재설계에 필요한 구체적 변경과 개발 도구 도입만 반영하고 '이 계획 전체가 모든 지침보다 우선한다'는 규칙을 만들지 않는다.

### G.3 source 기준과 재개

원문 진단의 기준은 `6ddce33a763d3de30e306980860d33aa88ab32f1`이다. 실행본 작성 시 read-only로 확인한 main은 `13eb1a04d75c753d95fa399191a3f0c6d0f2585d`이며, D1의 충돌 assertion은 이미 변경돼 있다. 원문의 test 개수·실패 개수·성능 수치는 과거 관측 기록으로 취급한다. 이 실행본 작성 중 제품 test는 실행하지 않았다.

각 WP 시작 시 실제 main SHA·변경 상태를 다시 확인한다. 이미 해결된 요구는 현재 코드와 관련 검증으로 입증하고 중복 패치를 만들지 않는다. 이미 충족된 WP도 확인한 SHA·명령·결과를 기록해야 done으로 바꿀 수 있다. 원문의 줄 번호는 탐색 힌트이며 symbol·call path를 기준으로 현재 위치를 찾는다.

WP-00에서 이 실행본을 `docs/development/redesign-plan.md`로 반입한다. 이후 이 경로를 기준 문서로 사용하고 `docs/development/redesign-status.md`에 상태를 기록한다. 같은 내용을 다섯 파일과 병합 파일 양쪽에서 관리하지 않는다. 원문 1–5 파일명은 출처 설명에만 사용한다.

### G.4 전체 완료 조건

다음 조건을 모두 만족해야 goal 완료다.

1. 6장의 모든 WP가 구현 또는 기존 충족 증거와 함께 done이고, 해당 결과 commit이 로컬 main의 조상이다. 원문에서 누락됐던 Phase 4–7도 포함한다.
2. 원문의 목표 구조에 따른 실제 호출 경로가 연결돼 있다. 타입·registry·pipeline·supervisor·UI가 선언만 존재하거나 기존 병렬 구현을 계속 호출하는 상태는 완료가 아니다.
3. identity/scope·clock/window·missing/zero·unit/statistic·bounded I/O·process ownership 계약의 positive/negative 검증이 있고, 설명되지 않은 새 회귀와 범위 내 미해결 결함이 없다.
4. 최종 main의 필수 CPU·Python 호환성·격리 Prometheus/ClickHouse·frontend/browser·문서 검증이 통과한다. 5장의 필수 도구가 없으면 준비하거나 해당 검증을 Pending으로 남기고 완료로 처리하지 않는다.
5. 실제 GPU/VERL·물리 multi-node·운영 backend·실제 모델 검증은 별도 표에서 실행 범위와 Pending 이유·명령을 확인할 수 있다. 이 명시적인 실환경 후속 항목만 로컬 goal 완료와 분리할 수 있다.
6. 사용자 안내·설치 package·schema·CLI·dashboard·App·demo가 새 구현과 일치하고, 필요한 compatibility 경로와 데이터 전환 방법이 검증돼 있다.
7. 마지막으로 staged/unstaged 상태, 생성 자원 cleanup, source/검증 SHA, 로컬 main 병합을 확인한다. 작업과 무관한 기존 변경은 그대로 보존한다.

환경 문제를 제품 성공으로 바꾸지 않는다. 구현 완료, 검증 완료, 원격 운영 적용 완료는 서로 다른 상태로 보고한다. 원격 Actions/branch protection 설정과 실환경 측정의 미실행은 명시된 외부 후속 항목이며, 로컬 필수 gate 실패는 후속 항목으로 넘겨 goal을 완료할 수 없다.

### G.5 읽는 순서와 문서 효력

G장 → 8장 결정 기준 → 6.0 공통 규칙·6.1 실행 순서 → 현재 WP 전체 → 7장 세션 절차 → 현재 WP가 참조하는 설계 절 순서로 읽는다. 0–1장의 진단은 과거 source의 근거이며 현재 상태를 보장하지 않는다. 2–4장은 목표 설계, 5–8장은 실행 규약이다. 구체적인 보완 WP가 예시 코드나 과거 설명보다 우선한다.

이 실행본은 원문의 내부 구현 교체를 허용한다. 기존 사용자 계약을 보존하는 Refactor와 계약을 의도적으로 바꾸는 Behavior를 구분하는 것은 기존 내부 설계를 보존하라는 뜻이 아니다.

## 0. 요약

> 0–1장은 원문의 기준 commit에서 관찰한 진단이다. 현재 main의 상태·수치가 아니며 G.3과 WP-00에서 다시 확인한다. 설계 방향의 근거를 보존하기 위해 원문 내용을 남겼다.

### 0.1 한 줄 결론

버그가 자주 나는 이유는 개발 습관이 아니라 **데이터 구조** 때문이다. 수정 커밋의 96%에 테스트가 같이 들어가 있다. 그런데도 같은 종류의 버그가 계속 다시 나온다. 핵심 개념(identity/scope, 관측 상태, signal, panel, config, process ownership)을 정의한 곳이 없다. 이 개념들이 untyped dict와 문자열 literal 형태로 Python, TypeScript, Bash, dashboard JSON, 문서에 각각 따로 복제되어 있다. 그래서 하나를 고치면 형제 경로가 깨진다.

### 0.2 하네스 평가

테스트 하네스는 **양은 충분하지만 구조는 부분적으로만 갖춰져 있다.**

- 테스트는 2,136개이고 실행 순서 의존성은 없다. CI workflow도 3개 있다. 여기까지는 좋다.
- **현재 `main` HEAD가 red다.** `tests/test_operational_regressions.py:95`가 HEAD 커밋 `6ddce33`과 정반대 계약을 assert해서 실패한다. HEAD 커밋은 관련 test 일부(55개)만 돌리고 들어갔다.
- **기본 `pytest` 실행 결과가 실행 환경에 따라 달라진다.** 이 서버의 셸에서는 122개가 실패하고, proxy 변수를 지우면 4개만 실패한다. 원인은 `HTTP_PROXY`와 `NO_PROXY`에 `127.0.0.1`이 없는 것이다.
- 테스트가 행동 단위가 아니라 **리뷰 회차 단위**로 묶여 있다(`test_review_*`, `*_regressions` 등 16개 파일). 그래서 같은 계약이 여러 파일에 흩어지고, 서로 반대 방향으로 assert하기도 한다.
- 가짜 backend 70개가 **PromQL substring 매칭**으로 응답을 고른다. 쿼리 문자열만 바꿔도 테스트가 받는 데이터가 조용히 바뀐다.
- golden(전체 리포트 고정) 테스트, coverage, Python lint와 type check가 모두 없다.

Agent 하네스는 **규칙은 많지만 기계로 강제되는 것이 거의 없다.**

- `AGENTS.md`는 211줄, 23.5KB이고 금지 조항이 약 46개다.
- 핵심 의미 규칙(scope 보존, missing≠0, browser wait)을 검사하는 장치는 없다.
- commit 전에 도는 gate(git hook, Claude hook)가 없다. CI가 required check인지도 확인되지 않는다. 커밋 `5efccfc` 본문에는 "GitHub Actions stays disabled"라고 적혀 있다.
- 리뷰 결과가 `quality-backlog.md`, 커밋 본문, `docs/validation/*.json`, 리뷰 회차별 test 파일 네 곳에 중복으로 기록된다.

### 0.3 핵심 처방 7가지

| # | 처방 | 막는 버그 군 |
|---|---|---|
| 1 | `EntityRef`, `StepRef`, `TimeWindow`를 Python dataclass로 정의하고, 대응하는 TS 타입을 생성한다 | identity/scope 유실 (\~29건) |
| 2 | `Observation{value, unit, status, reasons}`와 닫힌 `Status` enum, 구조화된 `Issue` | missing/stale/error가 0이나 `{}`로 바뀌는 문제 (\~15건) |
| 3 | **Signal Registry** 하나(`config/signals.toml`)에서 Python, TS, dashboard panel key, 문서 표, demo를 생성한다 | 계약 drift (\~13건), signal 하나를 추가할 때 고쳐야 하는 8곳 |
| 4 | 722줄짜리 `analyze()`를 단계별 순수 함수 pipeline으로 나누고, Rule과 LLM이 같은 `EvidenceBundle`을 쓰게 한다 | LLM 경로 뒤늦은 보정 (10건), `DiagnosticEngine` hotspot (25건) |
| 5 | Backend client 하나와 `QueryResult` 오류 모델 하나 | timeout/retry/부분 결과 버그 (\~11건) |
| 6 | Bash에 있는 lifecycle을 Python supervisor 하나로 옮기고, config resolver도 하나로 둔다 | lifecycle 버그 (\~12건), shell/Python 기본값 drift |
| 7 | 하네스: `scripts/check.sh` 단일 gate, hook 강제, env 격리, 공용 fake와 builder, golden 테스트, 행동 단위 테스트 재편 | red HEAD, 환경 의존 실패, 테스트가 회귀를 놓치는 문제 |

이와 별도로, 이번 요청에 따라 **Agent RL 병목 진단용 텔레메트리와 KPI 체계**를 4장에서 설계한다. 현재 계약은 metric 165개를 "정의"만 하고 있다. 이 중 진단 rule이 실제로 쓰는 것은 15개이고, producer가 없는 contract-only가 67개다. 목록은 크지만 진단 질문에서 출발한 설계가 아니다.

---

## 1. 현재 상태 진단 (근거)

### 1.1 규모 스냅샷

| 항목 | 값 |
|---|---|
| Commit | 285개, 약 5주(09-08 \~ 10-12), 작성자 1명. 하루 최대 36커밋 |
| 수정성 commit | 101개(fix/preserve/keep/separate/isolate/guard 계열) |
| 수정 commit당 변경 파일 | 평균 10.7개, 중앙값 6개. 20파일 이상 묶음 수정이 18개 |
| Python package | 89 module, 17,627줄, 함수 649개. 100줄 넘는 함수 18개, 50줄 넘는 함수 76개 |
| 가장 큰 단위 | `DiagnosticEngine.analyze` 722줄(`analysis/diagnostics.py:567-1288`) |
| 가장 자주 바뀐 파일 | `analysis/diagnostics.py` 40 commit, `src/module.tsx` 35 commit(분리 전), `ci_demo.py` 28 commit |
| Tests | 160 파일, 약 25k줄, 2,136개 수집, 직렬 실행 약 7분 |
| Grafana App | `src/` 50여 파일. `InvestigationView.tsx` 1,002줄. Browser validator 20개(3.7k줄) |
| Docs | Markdown 58개(12.9k줄)와 `docs/validation` 104개 파일(33.9k줄, 5.7MB) |
| Shell lifecycle | `run_telemetry.sh` 619줄, `run_verl_with_telemetry.sh` 495줄, `verl_local.sh` 347줄 |

### 1.2 이번 조사에서 확인한 실제 결함

아래는 리팩토링과 상관없이 바로 고쳐야 하는 항목이다.

| # | 결함 | 근거 | 영향 |
|---|---|---|---|
| D1 | `main` HEAD에서 test 1개 실패 | `tests/test_operational_regressions.py:95`가 `io_devices['259:0']=={}`를 기대한다. HEAD `6ddce33`은 첫 관측 장치의 counter를 보존하도록 바꿨다 | 계약이 두 파일에 반대 방향으로 존재한다. 어느 쪽이 맞는지 결정한 기록(ADR)이 없다 |
| D2 | App pressure card와 진단 evidence 연결 불가 | `InvestigationView.tsx:842`는 `"rdma_tx_wait_per_second"`를 찾는데, Python은 `rdma_transmit_wait_ticks_per_second`라는 이름을 만든다(`metric_queries.py:101`). 또 `distributed.ts:142`가 `row.unit === panel unit`을 요구하지만 기본 query evidence에는 unit이 없다 | GPU, vLLM, Sandbox, Storage, RDMA 5개 card가 evidence에 연결되지 않는 것으로 보인다. 코드를 읽고 추정한 결과이며 fixture로 재현하지는 않았다 |
| D3 | Proxy 환경에서 local HTTP가 proxy로 나감 | 셸에 `HTTP_PROXY`가 설정되어 있고 `NO_PROXY`에 `127.0.0.1`이 없다. `urllib`은 CIDR(`12.0.0.0/8`)을 무시한다. test 118개가 이것 때문에 실패한다 | 제품 코드의 `urlopen`도 같은 방식으로 local Prometheus 요청을 proxy로 보낼 가능성이 있다. 사용자에게 보이는 버그일 수 있다 |
| D4 | Wrapper 종료 유예 시간이 host process 수에 비례해 늘어남 | `scripts/run_verl_with_telemetry.sh:346` 근처에서 50회 poll하고, 매번 Python으로 `/proc` 전체를 스캔한다. 이 서버(약 1,800 process)에서 의도한 5초가 약 14초가 된다 | `test_review_followup.py::test_normal_workload_exit_cleans_owned_descendants`가 15초 timeout으로 항상 실패한다. 실제 GPU 노드에서도 종료가 지연될 수 있다 |
| D5 | 개발 환경 의존성 누락 | `.venv`에 `setuptools`가 없다. CI에서는 따로 설치한다 | `test_install_tools.py` 1개 실패 |
| D6 | 표시 문장에 걸린 로직 | `ui-copy.ts:21-99`가 영어 문장을 정확히 일치시켜 한국어로 바꾼다. `PhaseMatrix.tsx:50`은 `startsWith('Clock quality is')`로 분기한다 | 영어 문장을 다듬기만 해도 번역이 빠지고 분기가 바뀐다 |

### 1.3 버그 근본 원인 분류 (git history 101건 분석)

| 군 | 건수 | 대표 SHA | 반복되는 구조적 이유 |
|---|---|---|---|
| A. Identity/scope 유실 | 약 29 | `8172bc2`, `4fa2536`, `e2bb004`, `7320a23`, `599d865`, `e1f3c79`, `8eee527`, `7a1beec` | Identity가 label key 묶음(`cluster`, `node`/`nodename`, `instance`/`engine`, `gpu`, `run_id` 등)이고 untyped dict로 이동한다. Python에 손으로 쓴 key-tuple projection이 108개 있다. TS는 `RecordRow = Record<string, unknown>` |
| B. 품질 상태 붕괴 | 약 15 | `6ddce33`←`320a51b`, `5047e6f`, `6055bc6`, `2acccff`, `c0a9b24` | Status 타입이 없다. `"unknown"` literal이 146개, ad-hoc reason code가 42개, `Enum`/`Literal`은 0개다. `.get(x, 0)`이 31곳 남아 있다. Python은 `no_data`, TS는 `no-data`를 쓴다 |
| C. 시간/clock/window | 약 8 | `05072ef`, `599d865`, `3e48c0e`, `cc4ade5` | 시간이 단위(s/ms/ns)와 clock domain(wall/monotonic) 표시 없이 float로 전달된다. window 검증 코드가 14곳에 흩어져 있다 |
| D. Lifecycle/ownership | 약 12 | `32d25d5`, `a18b5b2`, `8b469b8`, `6aa8ee2`, `9e7bf96` | 약 1.9k줄의 Bash와 heredoc Python에 들어 있다. PID 파일 규칙이 6곳에서 서로 다르다 |
| E. Frontend state 동기화 | 약 6 | `3d0fb6b`, `5efccfc`, `3e48c0e` | URL, React state, Scenes cache, `window.location` 직접 읽기, Shell 필드 등 진실의 원천이 5곳이다. 재렌더 counter로 땜질하고 있다 |
| F. 언어 간 계약 drift | 약 13 | `e1f3c79`(`communication`↔`network`), `8257b25`, `464d49a`, `9e7bf96` | 이름, 단위, 기본값이 각 언어에 literal로 복제되어 있다. codegen이 없다 |
| G. Browser/CI wait | 약 12 | `bdc4ede`→`18e8741`→`1397c93`→`9fdd16a` | 공용 readiness helper가 없다. 고정 wait가 45개 있다. 한국어 문장을 selector로 쓴다 |
| H. Budget/timeout | 약 11 | `d10cede`, `81057c7`, `ec74ed1` | HTTP 경로 4개, deadline 방식 3개. 오류 타입으로 "이 query 실패"와 "backend down"을 구분할 수 없다 |
| I. 비정상 입력 격리 | 약 7 | `e32c74b`, `60989d3`, `ca21983` | JSONL reader 5벌, atomic write 4벌. reader마다 잡는 예외 집합이 다르다 |

**A, B, C 군을 관통하는 패턴은 parallel pipeline이다.** Rule 진단과 LLM 진단이 entity scoping, sampling quality, clock projection, unit을 각자 구현한다. 그 결과 rule 경로의 불변식을 LLM 경로로 뒤늦게 옮겨 심는 수정이 10건 이상 나왔다(`87df51f`, `2acccff`, `52fb4c4`, `8b77fa6`, `e32c74b`, `0525a81`, `4fa2536`, `e2bb004`, `36d7c0e`, `8172bc2`). Frontend에도 "label을 drill-down Context로 바꾸는 매핑"이 5곳 이상 있다(`distributed.ts::resourceContext`, 같은 이름의 `run-explorer.ts::resourceContext`, `infrastructure.ts`, `PhaseMatrix.tsx:89`, `storage-overview.ts:38`).

**같은 곳을 반복해서 고친 사례(fix-on-fix chain):**

- `llm_diagnosis.packet_from_report`: allowlist에 key를 하나씩 추가하는 수정 4회.
- `distributed.ts::resourceContext`와 그 형제 함수들: 같은 개념에 수정 6회.
- `evidence_quality.quality`: 품질 차원을 하나씩 추가하는 수정 5회.
- `rollout_state.load_serving_context`: 3일 동안 5회.
- `run_verl_with_telemetry.sh` ownership: 5회.
- `browser_validate.py::multi_worker_journey`: 7회.

### 1.4 구조 문제 Top 10 (연쇄 버그를 일으킬 가능성 순)

1. **722줄 `analyze()`가 report를 mutable dict로 조립한다.** test 파일 49개가 이 출력 형태를 assert하므로 signal 하나만 추가해도 suite의 1/3이 흔들린다.
2. **오류, 누락, 품질이 `"prometheus:{name}:{issue}"` 같은 문자열로 전달되고, 다른 모듈이 prefix로 파싱한다.** 예: `diagnostics.py:1373`은 retry 판단에서 no-data와 query 실패를 구분하지 못한다. `storage_overview.py:22,46`.
3. **Candidate의 strength가 rule 평가 뒤에 7개 파일 27곳에서 강등된다.** 최종 강도가 실행 순서에 따라 달라진다.
4. **Signal catalog가 없다.** 같은 signal이 `DEFAULT_QUERIES`, `PROFILE_SIGNALS`, `SIGNAL_SCOPE`, `BASELINE_REQUIRED`, reducer, dashboard JSON, `demos/live.py`, TS card map에 흩어져 있다. query 이름과 signal 이름을 손으로 매핑한 곳이 13곳이다.
5. **Rule과 LLM이 collection pipeline을 따로 가진다.** 두 경로는 config schema도 다르다(`prometheus.url`+dict vs `prometheus_url`+list).
6. **Backend client와 오류 모델이 통일되어 있지 않다.** HTTP 경로가 4개다. `_http_transport`는 요청마다 subprocess를 띄운다. client 종류를 `hasattr`로 판별한다. 같은 실패가 경로에 따라 `RuntimeError` 또는 `ValueError`로 나온다.
7. **Identity와 scope가 타입이 아니다.** env에서 identity를 해석하는 코드가 두 벌이고, node fallback이 서로 다르다(`events.py:78`은 hostname, `emitter.py:131`은 `None`). scope 어휘도 3종이다(schema 12값, `metrics.json` 자유 문자열, TS 문자열).
8. **Layer가 새고 있다.** SDK `sandbox.py`가 collector를 적재하고 analysis 로직도 가진다. `health`, `cluster`, `subsystems`가 1,447줄 엔진 모듈의 private client를 import한다. analysis는 runtime에 dashboard JSON에서 PromQL을 빌려 온다. 함수 내부 lazy import로 숨긴 순환 의존이 4쌍 있다.
9. **Config 모델이 두 개다(flat `dict[str,str]`과 nested JSON).** Shell에도 기본값이 다르게 박혀 있다. 예: `CLUSTER_NAME`이 Python은 `training-cluster`, shell은 `telemetry-cluster`. Port 19090 vs 9090.
10. **Frontend URL state의 진실의 원천이 하나가 아니다.** `InvestigationView.tsx`가 9개 page의 data projection, 도메인 판단, runner 생성을 모두 맡고 있고 테스트가 없다.

### 1.5 하네스 상세 평가

**테스트 하네스**

| 항목 | 현재 | 판정 |
|---|---|---|
| 규모 | 2,136 tests, npm 204 tests, helper 52 tests | 충분 |
| 결정성 | 역순 실행 결과 동일 | 양호 |
| 환경 격리 | `HOME`만 격리한다. Proxy, `TELEMETRY_*`, config key 36개가 test로 새어 들어온다. subprocess에 `os.environ \| {...}`를 넘기는 곳이 77곳 | **불량.** 122개 실패 중 118개가 환경 때문 |
| 계약의 위치 | 리뷰 회차 파일 16개, 약 110개 test에 계약이 흩어져 있다. 서로 반대로 assert하는 경우도 있다 | **불량.** 현재 red HEAD의 원인 |
| Fixture | `@pytest.fixture` 24개. test 모듈끼리 import하는 곳 19곳. 가짜 backend method 70개, HTTP handler 22개 | **불량.** 반환 형태 drift: 21개 중 19개 fake에 `result_quality`가 없다 |
| Fake 응답 선택 | PromQL substring 매칭 140곳 | **불량** |
| 구현 텍스트 assert | 72개 test, substring assert 254개. shell 함수를 텍스트 마커로 잘라 실행 | 취약. rename만으로 깨지고, 행동이 틀려도 통과한다 |
| End-to-end 고정 | Golden report 없음 | 없음 |
| 정적 검사 | Python lint/type 없음. TS는 `tsc --strict` | 부분적 |
| 시간 가정 | 1초 이하 wall-clock assert 21곳, sleep 80곳 | 불안정 위험 |
| 속도/구조 | 직렬 7분, marker/xdist/per-test timeout 없음. CI 제한은 10분 | 여유가 얇다. 사람들이 부분 실행을 하게 되고, 그것이 계약 분산 문제로 이어진다 |
| Optional 도구 | 로컬에서는 promtool, ClickHouse가 없어 PromQL 의미 검사 30개가 skip된다 | 로컬 green이 CI green보다 약한 gate |

**Agent 하네스**

| 규칙 | 강제 장치 |
|---|---|
| 고카디널리티 ID를 Prometheus label로 쓰지 않음 | 있음(test) |
| SDK import가 collector/analysis를 적재하지 않음 | 있음(test). 단, `sandbox.py` 경로는 이미 위반 상태 |
| D2 다이어그램과 문서 링크 | 있음(test, CI) |
| Correlation≠attribution, identity 보존 | **없음.** 사고가 날 때마다 test를 추가하는 방식 |
| Missing/stale≠0 | **없음** |
| 실패하는 regression test를 먼저 작성 | **없음** |
| 영향 범위가 넓은 변경은 전체 suite 실행 | CI에만 있다. 그 CI도 비활성일 가능성. **D1이 위반 사례** |
| Browser 고정 sleep 금지 | **없음.** 실제로 45곳 있음 |
| Secret을 log/artifact에 노출하지 않음 | **없음** |
| `Signed-off-by`, `--ff-only`, branch 정리 | **없음** |

결론: 기계로 검사하는 규칙은 구조적인 것 5개 정도다. 버그를 실제로 만드는 의미 규칙은 문장으로만 존재한다. 또 `quality-goals.md:36`은 구현한 agent가 자기 diff와 test assertion을 스스로 리뷰하도록 허용한다. 커밋 `8257b25`의 Mooncake test는 검증 대상 식에서 기대값을 뽑아 쓰는 동어반복이었고, 바로 다음 커밋에서 다시 작성됐다.

---

## 2. 처음부터 설계한다면: 설계 원칙

### 2.1 제품이 답해야 하는 질문 하나

XLayer의 핵심 출력은 다음 질문 하나에 대한 답이다.

> "Run R의 Step S는 비교 가능한 baseline보다 왜 느렸는가? 어느 phase가 critical path에서 늘어났고, 같은 시간·같은 entity에서 어떤 subsystem 신호가 함께 변했는가? 무엇을 모르는가?"

모든 module은 이 질문의 어느 단계를 맡는지로 정당화되어야 한다. 이 기준으로 보면 지금 구조에서 담당 단계가 모호한 것들이 드러난다. 예를 들어 `subsystems.py`는 subsystem 목록이 아니라 source discovery를 한다. `demos/live.py`는 metric 이름 114개를 따로 관리한다.

### 2.2 설계 원칙 7개

1. **도메인 타입 우선.** Identity, 시간, 관측 상태, 증거, 후보는 언어 타입으로 표현한다. dict는 I/O 경계에서만 쓴다. 타입이 있으면 "key를 복사하는 것을 잊음"이 code review 사안이 아니라 type error가 된다.
2. **정의는 한 곳, 나머지는 생성.** Signal, subsystem, status, panel key, config key는 하나의 선언 파일에서 Python, TS, docs, dashboard 메타데이터로 생성한다. 손으로 쓴 사본이 있으면 test가 원본과 대조한다.
3. **값과 상태는 같이 다닌다.** 값만 따로 꺼내 판정하는 경로를 만들지 않는다. `Observation.value`는 3.3절의 OBSERVED/ZERO/PARTIAL 계약에서만 허용하며, PARTIAL은 제한된 coverage와 함께 해석한다.
4. **진단은 단계형 순수 함수 pipeline이다.** I/O(collect)와 판단(evaluate)을 분리한다. 판단 단계는 입력이 같으면 출력이 같아야 하고, golden test로 고정한다.
5. **의존 방향은 한쪽이다.** 3.1절의 layer 표를 기준으로 한다. sdk/collectors와 backends/artifacts는 각각 core에 의존하며 서로를 강제로 경유하지 않는다. 역방향 import는 test로 금지한다.
6. **실패는 데이터다.** 예외는 경계에서 `Issue`로 바꾼다. 내부 로직은 문자열 prefix가 아니라 enum으로 분기한다.
7. **규칙은 하네스가 강제한다.** 문장으로만 있는 규칙은 test, lint, hook 중 하나로 바꾸거나 삭제한다. AGENTS.md에는 명령어, code map, 강제 장치가 없는 소수의 판단 규칙만 남긴다.

### 2.3 하지 않을 것 (YAGNI)

- 새 telemetry backend, microservice 분리, 플러그인 프레임워크, DI 컨테이너를 만들지 않는다.
- 검증 없이 한 번에 갈아엎지 않는다. 내부 구현은 새로 작성하거나 폐기할 수 있으며 각 WP의 계약·실패 경계와 golden을 검증한 상태로 통합한다. 기존 설계나 helper 재사용은 필수 조건이 아니다.
- OpenTelemetry SDK로 전면 교체하지 않는다. 현재 JSONL event/span 형식은 유지하고, 필요하면 export adapter 하나만 추가한다.
- Grafana App은 계속 optional로 둔다. 별도 PromQL client나 backend를 만들지 않는다는 기존 원칙은 옳으므로 유지한다.
- 165개 metric 계약을 모두 구현하지 않는다. 4장의 T0/T1만 진단 대상으로 하고, 나머지는 catalog에 "planned"로 표시한다.

## 3. 목표 아키텍처

원문의 목표 구조를 구현 기준으로 유지한다. 아래 예시의 미정 타입·필드는 실제 contract와 함께 완성하고, 현재 코드가 이 구조와 다르면 현재 코드를 교체한다. 공개 entrypoint와 저장 데이터의 전환은 WP별로 검증한다.

### 3.1 Layer와 의존 방향

위 layer일수록 아래 layer에 의존한다. 아래 layer는 위를 import하지 않는다.

| Layer (위 → 아래) | 내용 | import 가능 대상 |
|---|---|---|
| `demos` | registry 기반 synthetic 데이터 합성기 | 모든 layer. 단, 누구도 `demos`를 import하지 않는다 |
| `cli`, `ops` | `xltel`, config resolver, supervisor, health, app install | pipeline 이하 |
| `pipeline`, `diagnose` | collect → align → select → assess → evaluate → cap → assemble, `rules/`, `llm/`(EvidenceBundle 소비자) | backends, artifacts, core |
| `backends`, `artifacts` | prometheus, loki, clickhouse(3FS)의 공통 `QueryResult` / run store(manifest, step history, events, diagnosis의 versioned reader·writer) | core |
| `sdk`, `collectors` | EventRecorder, MetricEmitter, sandbox recorder / gpu, host, cgroup, topology | core |
| `core` | 타입(EntityRef, StepRef, TimeWindow, Observation, Status, Issue, Candidate), 생성된 registry, fileio, clock, units | stdlib만 |

규칙:

- `core`는 stdlib만 import한다.
- `sdk`와 `collectors`는 `core`만 import한다. SDK import가 collector나 analysis를 적재하지 않는다는 현재 규칙을 그대로 유지하고, 지금 위반 중인 `sandbox.py`도 고친다.
- `pipeline`은 `backends`를 Protocol로만 안다. test에서는 in-memory fake를 끼운다.
- `ops`/`cli`는 `pipeline`을 호출만 하고, 엔진의 private 멤버를 쓰지 않는다.
- 강제 방법: `tests/test_layering.py`가 AST import graph를 만들어 금지 edge를 검사한다. 함수 내부 lazy import도 포함한다.

### 3.2 Python package 구조 제안

```
xlayer_telemetry/
  core/
    identity.py      # EntityRef, StepRef, RunRef, Scope(enum), identity_from_env()  ← 유일한 env 해석기
    timebase.py      # TimeWindow, ClockDomain, Accuracy(enum), ClockQuality
    observation.py   # Status(enum), Observation, Issue, IssueKind(enum)
    evidence.py      # Evidence, Candidate, Strength(enum), Cap(enum)
    units.py         # Unit(enum) 하나의 단위 어휘 + Grafana unit 변환표
    fileio.py        # atomic write, JSONL read(유일), bounded decode
    _generated/      # registry codegen 결과 (signals.py, subsystems.py) — 수정 금지
  registry/
    load.py          # config/signals.toml → SignalSpec 검증 로더
  sdk/               # events.py, emitter.py, sandbox.py (현 metrics/, events.py, sandbox.py)
  collectors/        # 현 collectors/ + metrics/textfile.py
  backends/
    base.py          # Backend Protocol, QueryResult, Deadline(유일)
    prometheus.py    # query/query_range (현 prometheus.py + _http_transport 일부)
    clickhouse.py    # 현 diagnostics.ThreeFSClient 분리
    loki.py          # readiness + 필요 시 query
  artifacts/
    run_store.py     # manifest, step_history, run listing (현 manifest, step_history, operations/runs)
    diagnosis_io.py  # report/row 직렬화 + schema validate
  pipeline/
    collect.py       # SignalSpec × Window × Entity → ObservationSet (I/O 유일 단계)
    align.py         # clock 보정, window 정합
    select.py        # baseline 선택, entity 매칭
    assess.py        # sampling/clock/coverage 품질 → QualityFacts
    evaluate.py      # rules → Candidate[] (순수)
    cap.py           # strength 상한을 한 곳에서 적용 (순수)
    assemble.py      # Report 조립 (순수)
  diagnose/
    rules/           # subsystem별 rule 파일 (rollout.py, trainer.py, sandbox.py ...)
    llm/             # packet = EvidenceBundle 직렬화, validate, ollama client
  ops/               # config.py(유일 resolver), supervisor.py, process_identity.py, health.py, app.py
  cli.py             # parser + dispatch table (명령당 handler 함수)
  demos/             # registry 기반 합성
```

현재 모듈과의 대응은 6장 로드맵의 각 단계에 적는다. 이름 변경은 하위호환 re-export를 1회 릴리스 동안 유지한 뒤 제거한다. 현재 shell이 `python -m xlayer_telemetry.X` 형태로 18개 모듈 경로를 호출하므로, 이 경로들은 사실상 public API다.

### 3.3 Core 도메인 모델

아래는 형태를 보이기 위한 예시다. 필드 이름은 현재 artifact key를 최대한 그대로 쓴다.

```python
from enum import Enum

class Status(str, Enum):
    OBSERVED = "observed"          # 값 있음, 신선함
    ZERO = "zero"                  # 측정된 0 (OBSERVED의 특수형, 표시용 구분)
    NOT_CONFIGURED = "not_configured"
    QUERY_FAILED = "query_failed"
    TIMEOUT = "timeout"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NO_DATA = "no_data"            # backend 정상, series 없음
    STALE = "stale"                # 마지막 sample이 freshness 초과
    PARTIAL = "partial"            # 일부 entity/구간만
    UNVERIFIED = "unverified"      # clock/identity 검증 실패
    PENDING = "pending"            # 아직 settle 전

class IssueKind(str, Enum): ...      # Status의 실패 계열 + ENTITY_MISMATCH, CLOCK_UNSAFE, MALFORMED_INPUT

@dataclass(frozen=True)
class EntityRef:
    cluster: str
    node: str | None = None        # 정규화된 node 이름 (nodename/instance 해석은 생성 시 1회)
    role: str | None = None
    worker_id: str | None = None
    rank: int | None = None
    gpu: str | None = None         # uuid 우선
    engine: str | None = None      # vLLM instance
    device: str | None = None      # block/IB device
    replica: str | None = None
    scope: Scope = Scope.NODE      # run/worker/gpu/replica/shared-service/cgroup ...
    def key(self) -> tuple: ...    # 정렬·dedupe·join 키 (유일)
    def labels(self, spec: SignalSpec) -> dict[str, str]: ...  # PromQL selector 생성 (유일)

@dataclass(frozen=True)
class TimeWindow:
    start_ns: int
    end_ns: int
    domain: ClockDomain            # WALL / MONOTONIC(node) / CALIBRATED(reference)
    accuracy: Accuracy             # EXACT / CALIBRATED / APPROXIMATE / SAMPLED / UNKNOWN
    uncertainty_ns: int | None
    source: str                    # span / bridge / timer / sample

@dataclass(frozen=True)
class Observation:
    signal: str                    # registry id
    entity: EntityRef
    window: TimeWindow
    status: Status
    value: float | None            # status ∈ {OBSERVED, ZERO, PARTIAL}일 때만
    unit: Unit
    statistic: str                 # mean/max/p95/delta/rate
    samples: int
    issues: tuple[Issue, ...] = ()

@dataclass(frozen=True)
class Candidate:
    id: str
    subsystem: SubsystemId         # registry enum
    phase: Phase
    strength: Strength             # STRONG / SUPPORTING / WEAK / MISSING_EVIDENCE / HYPOTHESIS(LLM)
    evidence: tuple[Evidence, ...]
    counter_evidence: tuple[Evidence, ...]
    missing: tuple[Issue, ...]
    caps: tuple[Cap, ...]          # 왜 강도가 제한됐는가 (clock, scope, sampling...)
```

핵심 불변식은 다음과 같고, 모두 property test로 고정한다(5.1절).

- `Observation(status=NO_DATA)`는 `value`를 가질 수 없다. 생성자에서 거부한다.
- `EntityRef.key()`가 다르면 서로 다른 entity다. `evaluate` 단계는 서로 다른 key의 observation을 하나의 evidence로 합치지 않는다.
- `Candidate.strength`는 `cap.py`에서만 바뀐다. 이 규칙은 lint로 강제한다: `.strength =` 대입이나 `replace(..., strength=` 호출을 `cap.py` 밖에서 금지한다.

### 3.4 진단 pipeline

입력은 `StepRef`와 config다. 단계는 아래 순서로 실행된다.

| # | 단계 | 입력 → 출력 | 비고 |
|---|---|---|---|
| 1 | `collect` | 활성 SignalSpec × (current window, baseline windows) × 관련 EntityRef → `ObservationSet` | source clock으로 query window 변환이 필요하면 요청 전에 적용하고 원본과 reference window를 함께 보존한다. Backend 호출과 deadline은 이 경계에서 관리한다 |
| 2 | `align` | ObservationSet → 보정된 TimeWindow, `Issue(CLOCK_UNSAFE …)` | 수집된 calibration 근거로 정합성을 검사한다. 추가 I/O나 잘못된 query 구간의 사후 위장은 하지 않는다 |
| 3 | `select` | → comparable baseline, entity 매칭 | Workload shape, boundary scope, execution mode가 같은 baseline만. Entity는 같은 key끼리만 |
| 4 | `assess` | → `QualityFacts` | Sampling 충분성, freshness, coverage, clock quality, scope 적합성 |
| 5 | `evaluate` | → `Candidate[]` (초기 strength) | `rules/*.py`: phase slowdown × subsystem signal 변화 |
| 6 | `cap` | Candidate[] + QualityFacts → Candidate[] (`caps` 기록) | Strength가 바뀌는 유일한 곳 |
| 7 | `assemble` | → `Report`(schema versioned), App용 row projection(schema 있음) | `artifacts/diagnosis_io`가 `report.json`, `rows.jsonl`로 저장 |

`diagnose/llm`은 공통 `EvidenceBundle`의 관측·scope·quality를 입력으로 사용한다. 직접 metric을 분석하는 LLM 경로도 유지하며 Rule 후보만 모델 입력으로 제한하지 않는다. 모델 응답의 evidence id와 수치는 실제 bundle과 대조한다. 중복 field 복사 목록은 공통 schema로 대체하지만 민감정보 제한과 입력 budget은 유지한다.

- 각 단계의 입력과 출력은 `core` 타입이다. 단계마다 golden fixture를 둔다(`tests/golden/<scenario>/<stage>.json`).
- 지금의 `DiagnosticEngine`은 이 함수들을 순서대로 부르는 50줄 이하 facade로 줄인다. 사용 중인 test 101곳이 깨지지 않도록 생성자 시그니처는 한동안 유지한다.
- Retry 판단은 `Status`로 한다. `QUERY_FAILED`, `TIMEOUT`, `PENDING`은 budget 안에서만 재시도한다. 완료된 조회의 `NO_DATA`와 아직 settle 중인 `PENDING`을 구분하며 세부 정책은 WP-40에서 검증한다.

### 3.5 Signal Registry (단일 정의원)

선언 파일은 `config/signals.toml` 하나다. 기존 `config/metrics.json`은 producer 측 "metric 계약"으로 남긴다. registry는 진단 측 "signal"을 정의하고, 각 signal은 `metric` 필드로 계약 metric을 참조한다.

```toml
[signal.vllm_requests_waiting]
subsystem   = "rollout"                     # subsystems.toml의 id
metric      = "rollout_requests_queued"     # metrics.json 계약 참조 (검증됨)
source      = "vllm"                        # native source kind
promql      = 'vllm:num_requests_waiting{ {selector} }'
selector    = ["cluster", "node", "engine"] # EntityRef → label 매핑은 registry가 결정
scope       = "replica"
unit        = "requests"
statistic   = "max"
kind        = "saturation"                  # utilization | saturation | errors | rate | duration
direction   = "higher_is_worse"
baseline    = "required"                    # required | optional | absolute
threshold   = { absolute = 1.0 }
freshness_s = 30
tier        = "T0"                          # 4장
phases      = ["rollout"]                   # 이 signal이 evidence가 될 수 있는 phase
panel       = "rollout.vllm_waiting"        # dashboard canonical panel key
```

생성물(`scripts/gen_registry.py`, 결과물은 commit하고 `--check`로 drift를 검사):

| 생성물 | 대체하는 현재 사본 |
|---|---|
| `core/_generated/signals.py` | `DEFAULT_QUERIES`, `DEFAULT_THRESHOLDS`, `PROFILE_SIGNALS`, `SIGNAL_SCOPE`, `BASELINE_REQUIRED`, reducer 표, query↔signal 이름 매핑 13곳 |
| `grafana/xlayer-app/src/generated/registry.ts` | `semantics.ts:40` SUBSYSTEMS, `InvestigationView.tsx:842` card→signal map, `run-health.ts:30`, `matrix-presentation.ts:29`, phase label 3벌 |
| `grafana/xlayer-app/src/generated/diagnosis.ts` | `RecordRow = Record<string, unknown>`. `diagnosis.schema.json`과 새 `diagnosis-row.schema.json`에서 생성 |
| dashboard panel의 `xlayer.key` 주입 | 숫자 panel ID 결합 47쌍. 생성기 enumerate 순서에 의존하는 문제(`dashboard_views.py:241`) |
| `docs/_includes/signals.md`, `rules.md`, `config-keys.md` | `diagnosis-reference.md:335-352`의 threshold 사본 등 |
| `demos/` 합성 metadata | `demos/live.py`에 손으로 쓴 metric 이름 114개 |

Registry는 Phase 3에서 현재 signal의 출력을 보존하며 도입하고, Phase 7에서 원문 T0/T1 producer·query·rule을 완성한다. Metric ID와 실제 exporter 이름·버전 alias는 구분한다.

### 3.6 Backend client와 오류 모델

```python
class Backend(Protocol):
    def instant(self, expr: str, at_ns: int, deadline: Deadline) -> QueryResult: ...
    def range(self, expr: str, window: TimeWindow, step_s: float, deadline: Deadline) -> QueryResult: ...

@dataclass(frozen=True)
class QueryResult:
    status: Status                      # OBSERVED / NO_DATA / QUERY_FAILED / TIMEOUT / PARTIAL ...
    series: tuple[Series, ...]          # Series = (labels, samples)
    issues: tuple[Issue, ...]
    elapsed_s: float
```

- HTTP transport의 proxy 정책은 공통으로 관리한다. Loopback과 명시적 NO_PROXY 대상은 direct, 그 밖의 backend는 사용자 proxy 설정을 존중한다. URL이 backend로 설정됐다는 이유만으로 proxy를 우회하지 않는다. 기존 credential-safe redirect와 bounded I/O를 보존하며 WP-03·41에서 검증한다.
- Deadline 방식도 하나다. 요청마다 subprocess를 띄우는 `_http_transport` 방식은 측정 후 결정한다. hang 격리가 꼭 필요한 경로에만 남기고 그 이유를 ADR로 적는다.
- `hasattr` 기반 client 판별을 없앤다. 모든 fake는 `Backend` Protocol을 구현하고, `mypy`가 이를 검사한다.

### 3.7 Config와 lifecycle

**Config:** `ops/config.py`만 resolver다. 우선순위는 CLI > env > TOML/Bash > default이고, 결과는 검증된 `ResolvedConfig` dataclass다. Shell script는 `xltel config export --shell`이 출력한 resolved snapshot을 `source`만 한다. Script 안에서 `KEYS`에 속한 key에 `${X:-default}`를 쓰는 것을 test로 금지한다. TOML에서 설정할 수 없던 운영 knob 44개를 `KEYS`에 넣을지는 knob마다 결정한다(사용 빈도 기준).

**Lifecycle:** `ops/process_identity.py`가 PID 파일 형식 하나(pid, start_time, boot_id, session), `is_owned()`, `stop(grace)`, `lock()`을 제공한다. `ops/supervisor.py`는 workload를 실행하고 sidecar를 관리한다. 담당 범위는 signal forwarding, process group ownership, bounded shutdown, logger settle이다. 반복적인 전체 `/proc` scan 비용을 줄이되 late child와 PID/PGID 재사용을 처리하고 그룹 signal 직전에 현재 ownership을 확인한다. 단 한 번의 snapshot이나 PID 생존 여부만으로 종료 권한을 판단하지 않는다. monotonic deadline과 실제 cleanup 경계는 WP-07·51·52에서 검증한다. 세 launcher(`run_telemetry.sh`, `run_verl_with_telemetry.sh`, `verl_local.sh`)는 env 준비와 `exec python -m xlayer_telemetry.ops.supervisor ...`만 하는 얇은 wrapper가 된다. 기존 argv, Python 환경, exit code를 보존해야 한다는 계약은 supervisor의 test로 옮긴다.

### 3.8 Artifact와 schema

- `diagnosis.schema.json`(report)과 새 `diagnosis-row.schema.json`(App이 읽는 row, `row_kind`별 variant)을 runtime에서 검증한다. 쓸 때는 항상 검증하고, 읽을 때는 손상 record를 격리하고 나머지를 계속 읽는다.
- Rule row와 LLM row는 같은 projector(`assemble.rows()`)로 만든다. 지금은 LLM 쪽이 `resource_attribution` 등 필드 5개를 빠뜨리고 있다.
- Schema version을 올리는 절차: reader가 N-1 버전을 읽는 test를 추가하고, 그 다음 writer를 바꾼다.

### 3.9 Frontend (Grafana App)

```
src/
  generated/      registry.ts, diagnosis.ts (codegen, 수정 금지)
  data/           catalog loader, canonical-refs.ts (panel key → {dashboard, panel, refIds}), row decoder
  domain/         순수 view-model: investigationModel(rows, samples, ctx) → typed result + reason code
  scenes/         page별 factory. 모든 runner·variable 생성은 여기서만
  ui/             presentation only. props = view-model. 문구는 i18n[reasonCode]
```

- **URL state는 native Router/Scenes와 동기화된 값을 쓴다.** `matrix_*`, `phase_worker`, `detail_tab`, `storage_metric`은 지원되는 URL-synced Scenes 경로로 통합한다. Theme·timezone·time range는 Grafana native 계약을 유지한다. Render 중 직접 `window.location` 읽기와 `*SelectionVersion` counter를 없애고 navigation·cache 동작을 WP-60에서 실제 browser로 검사한다.
- **Panel은 ID가 아니라 key로 찾는다.** `canonical-refs.ts` 하나에 모든 참조를 모으고, test 하나가 provisioned JSON 전체를 돌며 모든 key가 존재하는지 확인한다.
- **도메인 규칙은 Python이 계산해서 row에 넣는다.** 예: clock comparability, candidate rank, workload comparability. TS는 다시 계산하지 않는다. `compareRuns`처럼 TS 재구현이 꼭 필요하면 Python과 같은 golden 입출력 fixture를 공유하는 parity test를 둔다.
- **분기는 reason code로 한다.** 문장 일치나 `startsWith`를 쓰지 않는다. DOM에는 `data-state`/`data-reason`을 달고 browser validator는 이것을 selector로 쓴다. 한국어 문장 selector를 금지한다.
- `InvestigationView.tsx`를 `domain/investigation.ts`(test 대상)와 page별 `ui/*View.tsx`로 나눈다. `PhaseMatrix.tsx:37-48`의 cell pipeline도 `domain/`으로 옮긴다.

## 4. Agent RL 병목 진단을 위한 텔레메트리·KPI 설계

이 장은 원문의 목표 KPI 범위다. 현재/계약-only 수치는 원문 baseline의 조사 결과이며 WP-70에서 실제 지원 source·버전과 대조한다. 아래 metric 식은 의미를 보여 주는 설계 표기이므로 range selector·histogram bucket·label·단위 등을 검증한 실제 query로 구현한다. 예시 임계값은 운영 환경에서 calibration을 마쳤다는 뜻이 아니다.

### 4.1 설계 방법

현재 `config/metrics.json`에는 metric이 165개 있다. 이 목록은 "관측 가능한 것"을 먼저 나열하는 방식(bottom-up)으로 만들어졌다. 실제 사용 현황은 다음과 같다.

| Category | 정의 | 진단 rule 사용 | 시각화 | Producer 없음(contract-only) |
|---|---|---|---|---|
| sandbox | 24 | 0 | 21 | 0 |
| training | 23 | 1 | 8 | 13 |
| storage | 23 | 1 | 18 | 5 |
| orchestration | 21 | 4 | 16 | 7 |
| gpu | 12 | 3 | 6 | 4 |
| network | 11 | 2 | 6 | 5 |
| rollout | 10 | 3 | 6 | 3 |
| data_movement | 10 | 0 | 4 | 6 |
| host | 9 | 1 | 3 | 6 |
| agent | 7 | **0** | 1 | 5 |
| checkpoint | 7 | 0 | 0 | 7 |
| container | 6 | 0 | 0 | 6 |
| reward | 2 | 0 | 1 | 0 |
| **합계** | **165** | **15** | **90** | **67** |

(`scripts/metric_coverage.py --json` 결과. "진단 rule 사용"은 `rule_signals`가 비어 있지 않은 항목 수다.)

이 구조에서는 Agent RL에서 가장 특징적인 병목인 tool/sandbox 대기, rollout long-tail, async staleness에 대한 진단 rule이 거의 없다. 반대로 진단에 쓰이지 않는 metric 정의가 많다. 처음부터 설계한다면 질문에서 출발해 아래로 내려가는 방식(top-down)으로 만든다.

1. **L0 결과 KPI:** 이 run이 GPU 시간을 얼마나 효율적으로 학습 진척으로 바꾸는가.
2. **L1 시간 분해:** Step 시간이 어느 phase에서 쓰였고, 어느 phase가 critical path에 있는가.
3. **L2 subsystem 신호:** 늘어난 phase를 담당하는 subsystem이 같은 window와 같은 entity에서 포화, 오류, 대기를 보였는가.
   - Resource 계열(GPU, host, network, storage, cgroup)은 **USE**(Utilization, Saturation, Errors)로 본다.
   - Service 계열(vLLM, Ray, Mooncake, 3FS, sandbox runtime, reward)은 **RED**(Rate, Errors, Duration)에 Queue를 더해 본다.
4. **Tier로 필수 여부를 정한다.**
   - **T0(필수):** 이것이 없으면 "어느 phase, 어느 subsystem"을 답할 수 없다. 기본 설치만으로 수집되어야 한다.
   - **T1(subsystem 진단):** 해당 subsystem을 쓰는 run에서 필수다. source를 연결하면 자동으로 활성화된다.
   - **T2(deep dive):** 진단 rule에는 쓰지 않는다. drill-down과 profiler로 넘어가기 위한 문맥이다.

각 KPI와 signal은 registry(3.5절)에 다음 속성을 가진다.

- 답하는 질문
- 공식(PromQL 또는 span 집계)
- 단위(unit)
- entity scope
- 품질 조건(freshness, 최소 sample 수)
- baseline 비교 방식(ratio, delta, absolute)
- 이 signal이 evidence가 되는 phase
- 반대 증거(counter-evidence)

### 4.2 Agent RL step 모델

**Phase vocabulary를 줄인다.** 현재 23개는 VERL timer key, framework phase, 운영 phase가 섞여 있다. 진단용 canonical phase는 아래 표로 정리한다. 나머지(`dataset_loading`, `profiling`, `artifact_write` 등)는 `phase_detail`로 강등한다. VERL key 매핑은 `adapters/verl.py:20` `STAGE_PHASES`의 현재 매핑을 그대로 쓴다.

| Canonical phase | VERL source | 담당 subsystem |
|---|---|---|
| `rollout` | `timing_s/gen`, agent loop span | rollout(vLLM/SGLang), agent loop |
| `tool_interaction` | tool span (SDK/sandbox) | sandbox, tool service, network |
| `reward` | `timing_s/reward` | reward(rule/RM/sandbox test 실행) |
| `reference_log_prob` | `timing_s/old_log_prob`, `timing_s/ref` | trainer GPU |
| `critic` | `timing_s/values` | trainer GPU |
| `advantage_estimation` | `timing_s/adv` | host CPU (보통 작음) |
| `actor_update` / `critic_update` | `timing_s/update_actor`, `timing_s/update_critic` | trainer GPU, NCCL network |
| `weight_sync` | `timing_s/update_weights`, weight transfer span | network(NVLink/RDMA), rollout ready |
| `checkpoint_save` | `timing_s/save_checkpoint` | storage(3FS/local), network |
| `evaluation` | `timing_s/testing` | rollout |
| `rl_step` | `perf/time_per_step` 또는 `timing_s/step` | 전체 |

**동기(sync) 모드:** parent rl_step과 nested tool span을 중복 합산하지 않고, 같은 scope의 exclusive 구간만 사용한다. 대략 `T_step ≈ T_rollout + T_reward + T_logprob + T_critic + T_adv + T_update + T_weight_sync + T_ckpt + T_other` 이다. `T_other`는 측정한 phase 합과 step 시간의 차이이고, 그 자체를 KPI로 둔다(unaccounted time). 이 값이 크면 계측이 빠졌다는 뜻이므로 진단 신뢰도를 낮춘다.

**비동기(async, fully-async/one-step-off) 모드:** Rollout과 trainer가 겹쳐 돌기 때문에 phase 합이 step 시간이 아니다. 대신 두 pool의 **대기 시간 비율**로 critical path를 판정한다.

- Trainer가 샘플을 기다린 시간 비율(`trainer_wait_for_samples_ratio`)이 높다 → rollout bound.
- Rollout이 새 weight를 기다린 시간 비율(`rollout_wait_for_weights_ratio`)이 높거나 rollout 측 idle이 크다 → trainer bound.
- `policy_version_lag`가 높다 → 품질 위험(staleness)이 있다. 이것은 처리량 병목과 별개로 보고한다.

현재 계약에 있는 `training_async_*`와 `policy_version_lag`가 이 판정의 원천이다. "Async trainer update가 모든 rollout/tool span을 포함한다고 가정하지 않는다"는 AGENTS.md의 계약을 이 모델이 명시적으로 구현한다.

### 4.3 KPI 계층

**L0: 결과 KPI (run, step 단위; dashboard 첫 화면)**

| KPI | 공식 | 단위 | 의미 |
|---|---|---|---|
| Step time | `perf/time_per_step` (p50/p95 over window) | s | 기본 속도 |
| Training throughput / GPU | `perf/throughput` (VERL tokens/s/GPU) | tokens/s/GPU | 하드웨어 효율의 정규화 지표 |
| Samples per hour | 학습에 실제 사용된 trajectory 수 / wall time | samples/h | 사용자 관점의 진척 |
| **Rollout goodput** | 같은 cohort에서 학습 사용이 확인된 trajectory 수 / 생성된 trajectory 수 | ratio | 사용 여부가 연결되지 않으면 unknown. Filter/timeout/error/truncation은 별도 속성이며 일괄 폐기라고 추정하지 않는다 |
| **Truncation ratio** | `response_length/clip_ratio` 또는 `finished_reason="length"` 비율 | ratio | max length 도달로 생기는 낭비와 long-tail 원인 |
| GPU busy share | trainer pool과 rollout pool 각각의 SM active 평균 | ratio | 어느 pool이 놀고 있는가 |
| Unaccounted time | `T_step − exclusive phase coverage` (sync, 같은 boundary) | s, ratio | 중첩/부모 구간 중복을 제외한 계측 공백. Coverage를 확정할 수 없으면 unknown |
| Policy staleness | `policy_version_lag` p95 (async) | versions | off-policy 정도 |

**L1: Phase 시간 분해 (step 단위)**

- Phase별 duration, step 대비 share, baseline 대비 ratio를 본다. Critical-path 여부는 sync에서는 합산 구조로, async에서는 대기 비율로 판정한다.
- **Rollout 내부를 다시 분해한다.** Agent RL 병목의 대부분이 여기에 숨어 있다.
  - `trajectory_duration` 분포: p50, p95, max.
  - **Long-tail ratio** = max / p50. 같은 cohort의 tail 편차를 나타내며 engine idle 시간 그 자체는 아니다. Idle 판정에는 별도 engine 관측이 필요하다.
  - 한 trajectory 안의 시간 분해: LLM 생성 시간 vs tool 시간 vs 대기(queue) 시간.
  - `agent_environment_wait_ratio` = trajectory별 관련 tool 구간의 합집합 길이 / 유효 trajectory 구간 길이를 동일 cohort에서 집계한 값. 병렬·nested tool의 중복과 missing span을 처리한다.
  - Turn 수와 tool call 수 분포.

**L2: Subsystem 신호** (4.4절 표)

### 4.4 Subsystem별 필수 metric

표기:
- **현재** 열: ✓ = 진단 rule에서 사용, V = dashboard에만 있음, C = 계약만 있음(producer 없음), — = 계약에도 없음.
- Selector의 `{e}`는 `EntityRef`에서 생성되는 label 집합이다.
- 원천 이름은 vLLM v1, DCGM 3.x, node_exporter 1.x 기준이다. 버전별 alias는 registry에 둔다.

#### (1) Rollout engine: vLLM (SGLang은 같은 signal id에 다른 source)

| Signal | 원천 | Kind | Tier | 판정에 쓰는 방식 | 현재 |
|---|---|---|---|---|---|
| `vllm_requests_running` | `vllm:num_requests_running` | utilization | T0 | Engine 활용. rollout phase 중 0인 시간 비율 = engine idle ratio | V |
| `vllm_requests_waiting` | `vllm:num_requests_waiting` | saturation | T0 | Queue 포화. ≥1이 지속되면 engine capacity 부족 | ✓ |
| `vllm_kv_cache_usage` | `vllm:kv_cache_usage_perc` | saturation | T0 | ≥0.9이면 KV 메모리 포화 | ✓ |
| `vllm_preemptions_delta` | `increase(vllm:num_preemptions_total)` | errors | T0 | KV 부족으로 생긴 재계산. 0보다 크면 강한 증거 | ✓ |
| `vllm_generation_tokens_rate` | `rate(vllm:generation_tokens_total)` | rate | T0 | Running이 높은데 이 값이 떨어지면 compute/메모리 대역폭 문제 | V |
| `vllm_prompt_tokens_rate` | `rate(vllm:prompt_tokens_total)` | rate | T1 | 다중 turn에서 prefill 비중이 커지는 것 감지 | — |
| `vllm_prefix_cache_hit_ratio` | `rate(prefix_cache_hits)/rate(prefix_cache_queries)` | efficiency | T1 | 다중 turn agent에서 hit 하락 → prefill 폭증 | — |
| `vllm_ttft_p95` | `histogram_quantile(0.95, vllm:time_to_first_token_seconds)` | duration | T1 | Queue와 prefill 지연 | V |
| `vllm_itl_p95` | `vllm:inter_token_latency_seconds` (구버전 `time_per_output_token`) | duration | T1 | Decode 속도. batch가 과대하거나 GPU 경합 | V |
| `vllm_queue_time_p95` | `vllm:request_queue_time_seconds` | duration | T1 | 대기 시간 직접 측정 | — |
| `vllm_finished_length_ratio` | `request_success_total{finished_reason="length"}` 비율 | quality | T1 | Truncation (L0와 교차 검증) | — |

반대 증거: waiting=0이고 running이 낮은데 rollout phase가 길다면 engine 병목이 아니다. tool/sandbox나 long-tail을 의심한다.

#### (2) Agent loop와 tool (SDK span)

| Signal | 원천 | Kind | Tier | 판정 | 현재 |
|---|---|---|---|---|---|
| `trajectory_duration` | agent loop span (trajectory_id는 attribute) | duration | **T0** | 분포와 long-tail ratio | — (span은 있으나 KPI 없음) |
| `rollout_long_tail_ratio` | max/p50 trajectory duration per step | derived | **T0** | ≥3이면 long-tail 후보 | — |
| `agent_environment_wait_ratio` | 동일 cohort의 trajectory별 tool interval union / 유효 trajectory duration | derived | **T0** | Tool-bound 판정의 핵심; overlap/missing 경계 포함 | C |
| `agent_tool_call_duration` | tool span (tool label만, ID는 attribute) | duration | T0 | Tool별 p95 vs baseline | V |
| `agent_tool_call_errors` / `timeouts` | span status | errors | T0 | 오류와 timeout. 재시도로 trajectory가 늘어남 | C |
| `agent_turns`, `agent_tool_calls` | VERL `num_turns`, `tool_call_counts` | workload | T1 | **Comparability 조건**: turn 분포가 다르면 baseline 비교 불가 | C/V |
| `rollout_output_tokens_mean` | VERL `response_length/mean` | workload | T1 | Comparability 조건 | V |

#### (3) Sandbox (tool 실행 환경)

| Signal | 원천 | Kind | Tier | 판정 | 현재 |
|---|---|---|---|---|---|
| `sandbox_acquire_wait` | sandbox span: 요청 → 할당 | saturation | **T0** | Pool 고갈. 대기가 늘면 sandbox capacity 부족 | — |
| `sandbox_create_duration` / `reset_duration` | runtime adapter span | duration | T0 | Image pull, overlayfs, 초기화 지연 | C/V |
| `sandbox_active` / capacity | runtime adapter | utilization | T1 | Capacity 대비 사용률 | V |
| `sandbox_failures` | runtime adapter | errors | T0 | 실패 → tool 재시도 | V |
| `sandbox_cpu_throttle_ratio` | `throttled_periods / periods` (cgroup v2) | saturation | T0 | CPU quota 포화 (test 실행, compile) | V |
| `sandbox_cpu_pressure` | cgroup `cpu.pressure` some | saturation | T1 | 경합 | V |
| `sandbox_memory_pressure`, `oom_kill` | cgroup `memory.pressure`, `memory.events` | saturation/errors | T0 | OOM → tool 실패 | V |
| `sandbox_io_pressure` | cgroup `io.pressure` | saturation | T1 | Local SSD 경합 (shared 3FS와 구분) | V |
| `sandbox_io_bytes` per device | cgroup `io.stat` | utilization | T2 | Device별 drill-down | V |

반대 증거: tool span은 늘었는데 sandbox 신호가 정상이면, 원인은 외부 tool 서비스(network 또는 원격 API)일 가능성이 높다. 이 경우 candidate는 `tool_service`로 두고 sandbox로 attribution하지 않는다.

#### (4) Reward

| Signal | 원천 | Kind | Tier | 판정 | 현재 |
|---|---|---|---|---|---|
| `reward_phase_duration` | `timing_s/reward` | duration | T0 | Phase 판정 | ✓(stage) |
| `reward_call_duration` | reward span (rule/RM/test 실행) | duration | T1 | 호출 단위 p95 | — |
| `reward_errors` / timeouts | reward span status | errors | T1 | 실패 → 0 reward 오염 | — |
| `reward_queue` | RM 서빙이면 vLLM signal 재사용 | saturation | T1 | RM 서버 포화 | — |
| `reward_score_mean` | VERL `critic/score/mean` | quality | T2 | 성능 문맥 (병목 판정에는 사용하지 않음) | V |

#### (5) Trainer (actor, critic, ref/log-prob)

| Signal | 원천 | Kind | Tier | 판정 | 현재 |
|---|---|---|---|---|---|
| `phase_duration{phase}` | VERL `timing_s/*` | duration | **T0** | L1 분해 | ✓ |
| `training_mfu{stage}` | VERL `perf/mfu/actor`, `critic`, `actor_infer` | efficiency | T0 | MFU가 떨어지고 duration이 늘면 compute 효율 문제 | V |
| `training_tokens_processed` | VERL `perf/total_num_tokens` | workload | T0 | **Comparability**: 토큰 수가 늘었다면 정상적인 증가 | V |
| `training_rank_straggler_ratio` | max/median rank step time | saturation | T1 | 특정 rank·node 지연 → 해당 entity로 drill-down | C |
| `training_communication_seconds` / overlap | framework timer 또는 trace | duration | T1 | NCCL 병목 | C |
| `training_data_wait_seconds` | framework timer | saturation | T1 | Dataloader/host 병목 | C |
| `gpu_memory_peak` | allocator / DCGM | saturation | T1 | Offload·recompute 유발 여부 | C |

#### (6) Weight sync와 policy 동기화

| Signal | 원천 | Kind | Tier | 판정 | 현재 |
|---|---|---|---|---|---|
| `weight_sync_duration` | `timing_s/update_weights` 또는 transfer span | duration | **T0** | Phase 판정 | ✓(stage) |
| `weight_sync_bytes` | transfer instrumentation | workload | T1 | Bytes가 같은데 duration이 늘면 대역폭 문제 | C |
| `weight_sync_bandwidth` | bytes / duration | efficiency | T1 | NCCL baseline 대비 비율 | C |
| `rollout_replica_ready_lag` | weight apply 완료 event | duration | T1 | 일부 replica 지연 → 그 replica로 drill-down | V |
| `policy_version_lag` | trainer와 rollout version 차이 | quality | **T0(async)** | Staleness | ✓ |
| `trainer_wait_for_samples_ratio` | `training_async_*` 또는 span | saturation | **T0(async)** | Rollout bound 판정 | C |
| `rollout_wait_for_weights_ratio` | weight 대기 span | saturation | **T0(async)** | Trainer bound 판정 | — |

#### (7) Orchestration: Ray

| Signal | 원천 | Kind | Tier | 판정 | 현재 |
|---|---|---|---|---|---|
| `ray_pending_tasks` | `ray_tasks{State=~"PENDING.*"}` | saturation | T1 | 자원 부족 또는 scheduling 지연 | ✓ |
| `ray_object_store_usage` / spill | Ray object store metric | saturation | T1 | Spill → disk I/O 지연 | ✓/C |
| `ray_actor_restarts` | Ray actor state | errors | T1 | 장애 → 재생성 시간 | C |
| `ray_scheduling_delay` | trace 또는 histogram | duration | T2 | 필요 시 | C |

#### (8) KV store: Mooncake

| Signal | 원천 | Kind | Tier | 판정 | 현재 |
|---|---|---|---|---|---|
| `mooncake_store_op_latency_p95{op}` | `vllm:mooncake_store_operation_time_seconds` | duration | T1 | KV put/get 지연 | V |
| `mooncake_store_failed_keys` | `..._failed_keys_total` | errors | T1 | 실패 → 재계산 | V |
| `mooncake_dfs_read/write_latency` | DFS client histogram | duration | T1 | DFS tier 지연 | V |
| `mooncake_dfs_errors{op}` | DFS client | errors | T1 | Read와 write는 **분리**한다(`8257b25` 교훈) | V |
| `mooncake_master_rpc_failures` | master metric | errors | T2 | Shared service. attribution 금지 | V |
| KV hit ratio | engine 또는 store hit/query | efficiency | T1 | Prefix 재사용률 | — |

#### (9) Storage: 3FS (shared)와 local

| Signal | 원천 | Kind | Tier | 판정 | 현재 |
|---|---|---|---|---|---|
| `storage_device_busy` | `rate(node_disk_io_time_seconds_total)` | utilization | T1 | Local device 포화 | ✓ |
| `storage_read/write_latency` | `rate(read_time)/rate(reads_completed)` | duration | T1 | 평균 latency | — |
| `storage_throughput` | `node_disk_*_bytes_total` | rate | T1 | Checkpoint와 dataset 단계 | V |
| `threefs_latency_p95{op}` | ClickHouse(3FS) | duration | T1 | Shared service. **correlation만** | ✓ |
| `threefs_small_io_ratio` | ClickHouse request size 분포 | efficiency | T2 | Small I/O 폭증 | ✓ |
| `checkpoint_save_bandwidth` | bytes / `checkpoint_save` duration | efficiency | T1 | Checkpoint 병목 | C |

#### (10) Network: RDMA/IB, Ethernet, NCCL

| Signal | 원천 | Kind | Tier | 판정 | 현재 |
|---|---|---|---|---|---|
| `rdma_bytes_rate{device}` | `node_infiniband_port_data_*_bytes_total` | utilization | T1 | Link 대비 사용률 (link rate는 topology에서) | ✓ |
| `rdma_xmit_wait_rate` | `rate(node_infiniband_port_transmit_wait_total)` | saturation | **T1(핵심)** | 혼잡. weight sync와 NCCL 지연의 1차 증거 | V(D2 버그) |
| `rdma_errors` | `port_*_errors`, `link_downed` | errors | T1 | Link 문제 | C |
| `net_bytes_rate`, `net_drops` | `node_network_*` | utilization/errors | T2 | Ethernet 경로 | V |
| `collective_baseline_ratio` | NCCL Tests baseline 대비 실측 | efficiency | T2 | 클러스터 기준선 | C |

#### (11) GPU (rollout pool과 trainer pool을 구분)

| Signal | 원천 | Kind | Tier | 판정 | 현재 |
|---|---|---|---|---|---|
| `gpu_sm_active` | `DCGM_FI_PROF_SM_ACTIVE` (없으면 nvidia-smi util로 대체하고 정확도를 `sampled_coarse`로 표시) | utilization | **T0** | GPU busy와 SM 병렬 활성도는 다른 지표다. SM active를 기본으로 하고 대체 source의 의미·단위를 별도로 표시 | ✓(util) |
| `gpu_tensor_active` | `DCGM_FI_PROF_PIPE_TENSOR_ACTIVE` | efficiency | T1 | MFU 교차 검증 | — |
| `gpu_memory_used_ratio` | FB used/total | saturation | T0 | 포화 | ✓ |
| `gpu_clock_throttle` | `DCGM_FI_DEV_CLOCKS_EVENT_REASONS` (구 `CLOCK_THROTTLE_REASONS`) | saturation | T1 | Power/thermal throttling → 특정 GPU straggler | — |
| `gpu_xid_errors` | `DCGM_FI_DEV_XID_ERRORS` | errors | T1 | 하드웨어 오류 | C |
| `gpu_nvlink_bandwidth` | `DCGM_FI_PROF_NVLINK_TX/RX_BYTES` | utilization | T2 | Weight sync, TP 통신 | — |
| `gpu_pcie_bandwidth` | `DCGM_FI_PROF_PCIE_TX/RX_BYTES` | utilization | T2 | Offload, H2D 복사 | — |
| `gpu_util_imbalance` | max−min SM active, 같은 pool 안에서 | derived | T1 | Straggler 탐지 | C |

#### (12) Host

| Signal | 원천 | Kind | Tier | 판정 | 현재 |
|---|---|---|---|---|---|
| `host_memory_available_ratio` | `MemAvailable/MemTotal` | saturation | T0 | 메모리 부족 | ✓ |
| `host_cpu_pressure` | `rate(node_pressure_cpu_waiting_seconds_total)` | saturation | T1 | Dataloader, reward, adv CPU 병목 | — |
| `host_memory_pressure` | `node_pressure_memory_stalled_seconds_total` | saturation | T1 | Swap이 있기 전에 감지 | C |
| `host_io_pressure` | `node_pressure_io_stalled_seconds_total` | saturation | T1 | I/O 경합 | — |
| `host_swap_activity` | `node_vmstat_pswpin/out` | saturation | T1 | 이미 진단 query에 있음 | ✓ |

#### (13) Telemetry self-health (진단 신뢰도의 입력)

| Signal | 원천 | Tier | 용도 |
|---|---|---|---|
| `scrape_freshness{target}` | `time() - timestamp(up)`, collector sample timestamp | **T0** | Stale 판정 |
| `clock_offset`, `clock_uncertainty{node}` | `operations/clock`, chrony | **T0** (multi-node) | Cross-node correlation cap |
| `sdk_dropped_events`, `snapshot_rejections` | io_writer, textfile collector | T0 | Incomplete telemetry 표시 |
| `query_budget_exhausted` | pipeline Issue | T0 | Report limitation |

**T0 합계는 약 25개다.** 기본 설치에 VERL bridge, GPU/host collector, vLLM endpoint, SDK agent span을 더하면 모두 수집된다. 이 25개만 있으면 "어느 phase가 늘었는가"와 "rollout 안에서 engine, tool, long-tail 중 무엇인가", "trainer라면 compute인가 메모리인가"까지 답할 수 있다.

### 4.5 병목 판정 매트릭스 (rule 설계)

모든 rule은 같은 형태를 가진다.

```
조건 1 (L1): phase P의 duration이 baseline 대비 ratio ≥ θ_P, 그리고 P가 critical path 위에 있다.
조건 2 (L2): P를 담당하는 subsystem의 saturation/errors signal이 같은 window, 같은 EntityRef에서 변했다.
반대 증거: 같은 subsystem의 utilization이 낮다. 또는 workload comparability 조건이 다르다.
결과: Candidate(subsystem, phase, strength). Strength는 cap 단계에서 QualityFacts에 따라 제한된다.
```

| 늘어난 phase | 후보 | 강한 증거 (같은 entity와 window) | 보조 증거 | 반대 증거 / 비교 불가 조건 |
|---|---|---|---|---|
| rollout | **Engine capacity** | `vllm_requests_waiting`≥1 지속, `queue_time_p95`↑ | TTFT↑, SM active 높음 | Waiting=0 & running 낮음 |
| rollout | **KV memory** | `kv_cache_usage`≥0.9 그리고 `preemptions_delta`>0 | ITL↑, prefix hit↓ | Preemption=0 |
| rollout | **Long-tail** | `long_tail_ratio`≥3, 끝 구간에서 engine idle ratio↑ | truncation ratio↑, max turn↑ | p50도 같이 늘었다면 전반적인 지연 |
| rollout / tool_interaction | **Tool / sandbox bound** | `agent_environment_wait_ratio`↑, tool p95↑ | sandbox acquire wait↑, CPU throttle↑, OOM | Sandbox 정상 → `tool_service` 후보로 분리 |
| rollout | **Workload 변화 (정상)** | output tokens, turn 수 분포 변화 | — | 진단을 "comparability 불일치"로 종료하고 병목으로 보고하지 않는다 |
| reward | **Reward service** | reward call p95↑, errors | RM queue↑, sandbox 신호 | — |
| actor_update / critic | **Compute 효율** | MFU↓, tokens 동일 | SM active↓, clock throttle | Tokens↑이면 정상 증가 |
| actor_update | **Collective / network** | communication seconds↑, `rdma_xmit_wait`↑ | straggler ratio↑ | RDMA 정상이고 straggler 1개 → 해당 rank·GPU 후보 |
| actor_update | **Straggler entity** | 특정 rank/GPU의 step time ≫ median | 그 GPU의 throttle, XID | — |
| weight_sync | **Transfer 대역폭** | bytes 동일, bandwidth↓ | `rdma_xmit_wait`↑, NVLink↑ | bytes↑이면 모델/설정 변화 |
| weight_sync | **Replica ready 지연** | 특정 replica `ready_lag`↑ | 그 replica의 GPU, network 신호 | — |
| checkpoint_save | **Storage** | save bandwidth↓, 3FS latency↑ (correlation) | device busy, host IO pressure | 3FS는 shared service이므로 strength 상한을 supporting으로 둔다 |
| (async) step | **Rollout bound** | `trainer_wait_for_samples_ratio`↑ | 위의 rollout rule로 하위 원인 탐색 | — |
| (async) step | **Trainer bound** | `rollout_wait_for_weights_ratio`↑ | 위의 trainer rule로 하위 원인 탐색 | — |
| 모든 phase | **Host 자원** | memory available↓, PSI↑, swap | — | — |

Cap(strength 상한) 규칙은 한 곳에 모은다(`pipeline/cap.py`).

- Shared service 신호(3FS, Mooncake master, Ray head)만으로 나온 후보: 최대 SUPPORTING.
- Clock quality가 `unsafe`/`unknown`인 cross-node evidence: 최대 SUPPORTING. 시간 정렬이 필요한 경우 MISSING_EVIDENCE.
- Sample 수 부족 또는 stale: 최대 WEAK.
- Unaccounted time이 step의 20%를 넘음: report 전체에 limitation을 표시한다.
- Entity가 일치하지 않음(다른 engine이나 GPU의 신호): evidence로 쓰지 않고 Issue(ENTITY_MISMATCH)로 남긴다.

### 4.6 Label과 cardinality 정책 (현재 원칙 유지, registry가 강제)

- Prometheus label로 허용: `cluster`, `node`, `role`, `worker_id`, `rank`, `local_rank`, `gpu`, `engine`, `replica`, `device`, `interface`, `phase`, `tool`(종류만), `status`, `run_id`(application 문맥만).
- Event attribute로만 둔다: `trajectory_id`, `request_id`, `sandbox_id`, `container_id`, `prompt_id`, `trace_id`, `span_id`, instance ID.
- Registry의 `selector` 필드가 허용 label만 담을 수 있도록 codegen 단계에서 검증한다. 지금은 test 한 개(`test_examples.py:37`)가 이 역할을 한다.
- Trajectory 단위 KPI(long-tail, env wait ratio)는 span에서 **step 단위로 집계한 뒤** gauge로 내보낸다. Trajectory별 series를 만들지 않는다.

### 4.7 KPI와 rule 검증 방법

- **Fault 시나리오 하나에 rule 하나.** 각 T0/T1 rule은 synthetic demo에 그 rule을 일으키는 fault 하나와, 일으키면 안 되는 counter fault 하나를 가진다. 예: KV 포화 fault에서 `kv_memory` 후보가 STRONG이어야 하고, workload 변화 fault에서는 아무 후보도 나오면 안 된다.
  - 이미 있는 `test_diagnosis_fault_benchmark.py`와 `demos/scenario.py`를 확장한다.
  - Demo는 registry에서 metric 이름을 생성하므로 이름 drift가 원천적으로 사라진다.
- **Confusion matrix를 CI artifact로 남긴다.** (fault, 기대 후보, 실제 후보, strength) 표를 저장하고, rule을 바꿀 때 diff를 리뷰한다.
- **실환경 검증 단계를 구분한다.** synthetic, VM, 실제 GPU/VERL 단일 노드, 물리 multi-node. KPI 문서에는 signal마다 마지막으로 검증된 단계를 표시한다. 현재 AGENTS.md의 "검증 범위를 과장하지 않는다" 규칙을 데이터로 바꾸는 것이다.
- **새 KPI를 추가하는 절차:** registry 항목 → producer(또는 native source) → codegen → fault 시나리오 → panel key → 문서 자동 생성. 이 경로 밖에서 KPI를 추가할 수 없다.

## 5. 검증과 agent 하네스

### 5.1 검증 계층

원문의 unit·contract·golden·property·integration·frontend/browser 구분을 도입한다. 순수 함수 test는 core와 pipeline의 계약, contract test는 registry·schema·query·panel·언어 간 일치, golden은 report와 rows의 동작 보존, integration은 실제 subprocess·HTTP·backend 경계를 검사한다. 기존 회귀를 새 구조에 맞춰 이동하거나 다시 작성할 수 있지만 보호하던 계약의 대응표를 유지한다.

- Golden은 현재 출력의 characterization과 목표 동작의 acceptance를 구분한다. 이미 잘못된 출력은 KNOWN_ISSUES에 기록하고 해당 Behavior WP에서만 의도적으로 바꾼다. 모든 timestamp를 지우지 않고 generated_at 등 비결정 필드만 정규화한다. window·uncertainty·source age·entity처럼 의미를 가진 필드는 검사한다.
- FakePrometheus는 원문의 제한된 식만 지원하고 미지원 식은 명시적으로 실패한다. rate/increase의 reset·경계 sample·extrapolation을 임의로 단순화해 Prometheus와 동등하다고 주장하지 않는다. 지원 연산은 설치한 실제 Prometheus/promtool과 대조하며, 이를 못 했으면 protocol/반환 형태 검증과 의미 검증 상태를 분리한다.
- 최소 golden 시나리오는 정상, KV 포화, rollout long-tail, tool/sandbox throttle, update RDMA, weight sync, async rollout-bound, unsafe clock, partial backend failure, workload 변화다. 아직 producer/rule이 없는 경우 현재 missing 출력을 먼저 고정하고 Phase 7에서 새 expected result를 추가한다.
- Environment 격리는 사용자의 runtime 설정과 test harness 설정을 구분한다. `PROMTOOL`, `XLAYER_TEST_CLICKHOUSE_CONTAINER`, test 전용 tool 경로, 명시적으로 주입한 proxy/config, logger settle fixture를 무조건 지우지 않는다. fixture의 실행 순서에 의존하지 않는 정리·주입 순서를 검사한다.
- Fake clock 검사는 deadline 로직을 입증하고, 실제 timeout/프로세스 검사는 bounded cleanup을 입증한다. 둘 중 하나만으로 다른 경계를 검증했다고 보고하지 않는다.
- 기존 text test는 의미 검증으로 옮긴다. 생성물 drift·문서 링크·package asset·DCO 등 텍스트 자체가 계약인 검사는 유지할 수 있다.

### 5.2 단일 gate

`scripts/check.sh [fast|default|full]`은 현재 저장소의 기존 명령을 통합한 gate다. 아직 없는 test 디렉터리·생성기·mypy 대상은 WP가 도입될 때 연결한다. 없는 경로를 조용히 누락하는 상태가 영구화되지 않도록 status 표와 연결한다.

| 프로파일 | 필수 범위 | 용도 |
|---|---|---|
| fast | 변경 영향이 있는 unit·contract·golden·property, 해당 ruff/mypy 대상과 생성물 drift | 변경 중·commit 전 |
| default | fast, 해당 WP의 관련 regression·계약 검사, 영향받은 Python/shell 문법과 diff check, 영향받은 App의 npm test/typecheck/build | 각 WP 검증·통합 |
| full | default, 전체 CPU suite, 실제 PromQL·격리 ClickHouse, 실제 Grafana/browser journey, docs build·link/diagram 검사 | 최종 완료 및 해당 경계의 필수 검증 |

각 WP는 실제 변경 경로와 실패 경계를 확인하는 관련 test만 실행한다. Status/Issue·identity·time·registry·schema·backend interface·config 기본값·CLI·artifact처럼 공유 계약을 바꾸거나 Phase 경계·최종 완료에 도달하면 통합 tree에서 전체 CPU suite를 실행한다. 가까운 시점에 통합되는 독립 WP의 공유 계약 변경은 관련 test를 각각 확인한 뒤 하나의 전체 CPU 실행으로 묶는다. 이후 제품·test·설정 tree가 같고 문서·상태표만 바뀌었다면 통과한 전체 결과를 재사용한다. 실패가 나면 마지막 통합 변경을 분리해 원인을 확인한다. WP-02의 깨끗한 환경/오염된 환경 전체 suite와 test별 실패·skip 집합 비교 결과는 유지한다. 변경하지 않은 frontend의 같은 SHA 검사 결과는 WP 중간에 재사용할 수 있으나 최종 full은 최종 tree에서 실행한다. `pytest -n auto`는 test의 port·filesystem·shared backend 독립성이 입증된 뒤에만 켜며 직렬 실행과 결과를 대조한다. 시간을 맞추려고 필요한 검사를 삭제하지 않는다. 실제 GPU·VERL·물리 multi-node 검사는 full에 포함하지 않고 G.4의 실환경 표로 관리한다.

Runtime 최소 Python 3.10과 CPU CI의 3.12를 유지한다. 문서용 Python 3.11+ 환경은 runtime과 분리한다. 새 library의 지원 버전을 확인하고, `StrEnum` 대신 Python 3.10에서 동작하는 `str, Enum`을 사용한다. enum의 문자열 직렬화도 기존 artifact 값과 대조한다.

WP-05에서 gate를 만들기 전에는 아래 기존 명령을 사용한다. 문서 WP는 문서 관련 test와 build를 먼저 실행하고, 제품 변경은 관련 test를 실행한다. 공유 계약 변경·Phase 경계·최종 완료에는 아래 전체 CPU 회귀를 추가한다. frontend 변경은 해당 디렉터리에서 npm test/typecheck/build와 관련 browser 검사를 추가한다.

```bash
python -m pytest -q
python -m compileall -q xlayer_telemetry examples
git ls-files -z '*.sh' | xargs -0 -r -n 1 bash -n
```

WP-02 이전에는 원문의 proxy 영향을 명령별 환경에서 분리해 baseline을 확인할 수 있지만 셸 전역 설정은 바꾸지 않는다. proxy·logger·test tool 환경을 명시적으로 주입하는 test는 그대로 보존하고, WP-02 이후 오염된 환경과의 parity를 다시 검증한다.

### 5.3 Gate 도입과 기존 실패

Phase 0에서 먼저 동일 환경의 baseline 실패를 기록하고 각 안정화 WP가 담당한 실패를 해소한다. WP-05의 gate는 WP-01·02·03·04·07·08 결과가 통합된 뒤에 평가한다. 이미 실패한 suite를 이유 없이 모든 WP의 시작 금지 조건으로 삼지 않는다. 새 실패는 해당 WP에서 해결하고, 범위 밖 기존 실패는 원인·담당 항목을 기록한다. 범위 내 미해결 실패가 최종 goal로 넘어가면 완료할 수 없다.

Coverage는 subprocess 포함 여부와 측정 대상 line/branch를 명시한다. 원문의 core/pipeline branch 90%는 목표이며, 숫자를 맞추기 위한 무의미한 test나 미계측 경로 제외를 하지 않는다. 필요한 보장을 직접 assertion으로 확인한다. timeout 수치는 실측을 근거로 unit/integration에 맞게 하나의 설정에서 관리한다.

### 5.4 검토·기록·hook

WP 결과는 status 표와 해당 검증 evidence에 한 번 기록한다. 이미 있는 quality backlog·validation 문서와 연결하며 작은 변경마다 별도 사용자 보고서를 만들지 않는다. agent 지침의 줄 수를 맞추려고 ownership·scope·data safety 규칙을 삭제하지 않는다.

각 WP는 구현 후 diff와 test assertion을 별도 검토 단계에서 다시 읽는다. 별도 worker 사용이 허용된 경우 독립 reviewer에게 맡기고, 그렇지 않으면 같은 agent의 검토임을 명시한다. 독립 검토를 실행하지 않았는데 실행했다고 기록하지 않는다. 별도 worker를 필요로 하는 규칙이 goal 진행을 자동으로 멈추게 하지 않는다.

새 regression의 red 검증은 기준 commit의 격리 worktree에서 수행할 수 있다. 새 test와 필요한 fixture만 가져와 실패가 실제 결함 때문인지 확인한다. 기존 사용자 변경을 stash하거나 test import 실패를 제품 red로 계산하지 않는다. 교체된 구현 결합 test는 대응하는 새 동작 검증을 제시한다.

Hook installer는 Git이 알려 주는 hook 경로와 기존 hooksPath를 확인한다. 기존 hook을 덮어쓰지 않고 보존하거나 연결 방법을 보고한다. hook은 편의 장치이며 CI/gate를 대체하지 않는다. 원격 설정은 사용자가 별도로 승인한 경우에만 변경한다.

## 6. 리팩토링 로드맵 — 전체 작업 단위 명세

### 6.0 공통 규칙

- `[R]`은 외부 동작을 보존하는 내부 교체다. 기존 내부 구조를 보존할 의무는 없다. output golden과 사용자 계약은 불변이며, test의 import·fixture·private API 의존은 새 구조에 맞춰 바꿀 수 있다. 동작 기대값 변경이 필요하면 관련 변경을 `[B]`로 분리해 ADR·검증을 추가한다.
- `[B]`는 의도한 동작 변경이다. ADR에 변경 전/후 계약, 이유, 이전 방법, 영향 test와 golden을 기록한다. 이미 반영된 수정은 현재 test와 이력으로 입증하고 불필요한 새 실패를 만들지 않는다.
- `[H]`는 하네스·개발 도구·문서다. 제품 동작을 슬쩍 바꾸지 않는다. 새 lint를 켜기 위한 기계적 수정도 범위와 검증을 명시한다.
- public CLI, argv/exit code, config, package asset, 저장된 artifact, dashboard UID·URL은 기본적으로 보존한다. 새 schema/의도한 계약 변경은 해당 Behavior WP에서 reader·migration·사용자 안내를 먼저 설계한다.
- 내부 compatibility shim은 전환 중 사용할 수 있다. 외부 import/`python -m` 경로의 shim은 원문대로 최소 한 릴리스의 전환 기간을 둔다. goal 실행이 한 번 끝났다는 이유만으로 릴리스 기간이 지난 것으로 보지 않는다.
- 목표 모듈로 갈 때 기존 구현을 추출하는 방법과 새로 작성하는 방법 중 원문의 설계를 제대로 구현하는 방법을 택한다. 기존 복잡성을 그대로 옮긴 뒤 파일만 나누는 것으로 완료하지 않는다.
- 각 WP의 명시된 입력 외에도 caller·fixture·schema·exports·package data·문서를 검색한다. 연결된 계약을 완성하는 변경은 범위에 포함한다. 파일 수는 검토 신호이며 correctness를 위해 필요한 연결을 누락시키는 상한이 아니다.
- rollback은 이전 release의 entrypoint·artifact reader를 사용할 수 있는지로 검증한다. 이력을 강제 초기화하거나 사용자 데이터를 되돌리는 자동 명령을 실행하지 않는다.

### 6.1 실행 순서와 의존성

아래 표의 화살표만 필수 선행 관계다. 독립 후보는 현재 main의 실제 caller·수정 파일·생성물·fixture·공유 자원을 확인한 뒤 별도 worktree에서 최대 3개까지 시작한다. 같은 파일을 수정하거나 같은 backend/container/output·Git index를 공유하면 직렬로 진행한다. WP 번호는 원문과 동일하게 유지한다.

실행 가능한 WP 사이에서는 사용자 데이터·scope·clock·missing/zero의 정확성, 프로세스 ownership·bounded I/O의 안전성, 실제 사용자에게 보이는 결함, 이를 검증하는 gate, 구조 정리 순서로 우선한다. 우선순위는 이미 필요한 선행 관계를 생략하거나 완료 기준을 낮추지 않는다. 선택 이유와 미룬 독립 WP를 상태표에 짧게 기록한다. Phase 0에서는 WP-03의 HTTP proxy와 WP-07의 shutdown을 높은 위험으로 보고, 현재 main에 이미 반영된 WP-01은 계약 확인·ADR을 병렬로 마친다.

| Phase | 선행 관계와 병렬 후보 | 통합 결과 |
|---|---|---|
| 0 | WP-00 → WP-04 → WP-02; 이후 WP-01·WP-03·WP-07은 파일 충돌이 없으면 병렬 후보. WP-08은 WP-03과 실제 `diagnostics.py`·App 변경 범위를 대조한 뒤 시작한다. WP-05는 WP-01·02·03·04·07·08 통합 후, WP-06은 WP-05 후 | 환경 재현성, 알려진 결함, 실행 gate |
| 1 | Phase 0 후 WP-10; WP-11과 WP-12는 WP-10 후 충돌이 없으면 병렬 후보. WP-13은 WP-11·12 통합 후 | 공용 fake·golden·layering·동작 test |
| 2 | Phase 1 후 WP-20·WP-21·WP-22는 수정 범위가 겹치지 않는 조합만 병렬 후보. WP-23은 세 WP 통합 후 | Core 도메인 모델과 타입 검사 |
| 3 | Phase 2 후 WP-30; WP-31·WP-33·WP-34는 WP-30 후 생성기·생성물 소유가 겹치지 않을 때 병렬 후보. WP-32는 WP-31 후 | Registry와 Python/TS/dashboard/docs/demo 연결 |
| 4 | WP-40 → WP-41 → WP-42 → WP-43 → WP-44 → WP-45 → WP-46 | Backend, pipeline, 공통 evidence와 artifact |
| 5 | WP-50 → WP-51 → WP-52 | Config resolver와 Python lifecycle |
| 6 | WP-60 → WP-61 → WP-62 | URL/Scenes, view-model, reason code와 browser |
| 7 | WP-70 → WP-71 → WP-72 | Agent RL producer·KPI·rule 연결 |
| 8 | WP-80 → WP-81 | 구 구현 정리, 문서·지침·최종 검증 |

Phase 4–8은 표의 화살표대로 진행한다. 병렬 후보라도 시작 전 실제 변경 범위가 겹치면 먼저 끝날 WP를 통합하고 나머지를 진행한다. Worker 간 상태표·main·공유 설치 환경은 변경하지 않는다. 주 작업자는 완성된 branch를 하나씩 통합하고, 통합 직후 필요한 관련 test 및 위 기준에 해당하면 전체 CPU suite를 실행한다.

### Phase 0: 안정화

#### WP-00 `[H]` 실행 계획 반입과 기준점

- **목표:** 이 전체 실행본과 재개 가능한 상태표를 저장소에 둔다.
- **입력:** 실제 main, AGENTS.md, docs/index.md, docs/development/quality-goals.md, quality-backlog.md, 이 실행본.
- **작업:** `docs/development/redesign-plan.md`에 실행본을 반입하고 Maintainers에서 연결한다. 같은 내용을 다섯 파일로 중복 반입하지 않는다. `redesign-status.md`에는 모든 WP 행과 source SHA, 상태, 검증, 결과 commit, 외부 Pending을 둔다. `docs/adr/0000-template.md`와 7.2절 실행 프롬프트를 준비한다. 문서 빌드에 맞는 toctree/orphan/exclude를 적용한다.
- **지침 정렬:** 계획 전체가 AGENTS.md보다 우선한다는 조항은 쓰지 않는다. 원문이 요구한 개발 도구 도입, 목표 구조로의 내부 구현 교체, 로컬 WP 병합 절차를 좁고 명시적으로 기록한다. 사용자 자원·scope·보안 계약은 유지한다.
- **기준점:** 과거 main red를 현재 사실로 복제하지 않는다. 이미 수정된 D1과 GPU freshness 변경을 확인하고 현재 baseline을 기록한다. 8장의 결정 기준과 외부 Pending을 구분한다.
- **완료 기준:** 문서 build와 `tests/test_documentation_links.py`, `tests/test_documentation_diagrams.py` 통과. 모든 WP에 고유 행과 실행 순서가 있고 G.4 완료 조건이 보존된다.

#### WP-01 `[B]` Sandbox first-seen device 계약 확정

- **목표:** 읽을 수 있는 빈 io.stat과 읽을 수 없는 baseline을 구분하고 device delta를 정확하게 유지한다.
- **입력:** `sandbox.py`, `collectors/sandbox_sampler.py`, `tests/test_operational_regressions.py`, `tests/test_sandbox_observability.py`, 원문 D1.
- **작업:** 현재 main의 수정과 기존 parameterized regression을 먼저 확인한다. 시작 시 읽을 수 있고 해당 device가 없으면 시작 counter는 0, unreadable이면 unknown이라는 원문 계약을 ADR로 기록한다. unreadable·reset·새 device의 결과가 누락/실측 0으로 혼동되지 않는지 event producer부터 device_window 소비자까지 확인하고 필요한 부분만 교체한다.
- **호환성:** 기존 empty mapping이 unknown을 표현하는 artifact는 reader에서 계속 읽는다. 새 status가 필요하면 additive field 또는 명시적 schema 변경으로 다루며 기존 데이터를 삭제하지 않는다.
- **완료 기준:** 위 두 test 파일의 관련 test 통과, first-seen readable/unreadable/reset 경계 증거. 이미 충족한 수정은 추가 제품 변경 없이 확인 결과로 완료할 수 있다.

#### WP-02 `[H]` Test 환경 격리

- **목표:** 개발자 환경이 fixture 결과를 바꾸지 않도록 한다.
- **작업:** config KEYS와 runtime proxy·TELEMETRY/XLAYER 환경의 유입 경로를 목록화하고 autouse fixture와 subprocess helper에 공통 정리 정책을 둔다. test harness의 `PROMTOOL`, `XLAYER_TEST_CLICKHOUSE_CONTAINER`와 명시적 tool 경로는 보존한다. test가 의도적으로 설정한 env는 정리 이후 적용한다. 기존 short_logger_settle fixture가 지워지거나 실행 순서에 따라 달라지지 않게 한다.
- **전환:** subprocess env 작성처를 모두 조사하고 영향을 받는 곳을 변환한다. test가 직전에 넣은 config까지 `clean_env()`에서 다시 삭제하지 않는다. import 시 env를 읽는 코드와 fixture 시점의 차이도 검사한다.
- **완료 기준:** proxy 없는 전체 suite와 잘못된 HTTP/HTTPS/ALL_PROXY를 주입한 전체 suite의 실패·skip 집합이 같다. intentional proxy test와 backend-enabled test의 실행이 유지된다. 실패 목록 비교는 같은 code tree에서 한다.

#### WP-03 `[B]` HTTP proxy 정책 정합

- **목표:** loopback·명시적 NO_PROXY는 직접 연결하고 나머지 대상은 사용자의 proxy 설정을 존중한다.
- **입력:** `prometheus.py`, `_http_transport.py`, `_http_redirects.py`, ThreeFSClient, `demos/control.py`와 전체 HTTP caller.
- **작업:** 공통 proxy 정책을 구현하고 실제 opener 경로에 연결한다. localhost·IPv4/IPv6 loopback, NO_PROXY host/domain/port·literal IP CIDR을 검증한다. 일반 backend host가 설정돼 있다는 이유만으로 proxy를 무조건 우회하지 않는다. CIDR 처리를 위해 무제한 DNS lookup을 추가하지 않는다.
- **보존 경계:** credential-safe redirect, clock redirect 금지, response byte limit, deadline, cancel·child cleanup, 오류 정보 비노출을 기존 또는 새 transport에 유지한다. redirect마다 destination 정책을 적용해 처음 URL의 정책이 외부 host까지 새지 않게 한다.
- **완료 기준:** 실제 local fake server 성공과 mock proxy 전달, nonmatching NO_PROXY, IPv6 가능/불가 구분, cross-host redirect credential 보호, partial body·hang deadline regression 통과. 실제 network 대신 URL 문자열 검사만 한 결과는 HTTP 성공으로 기록하지 않는다.

#### WP-04 `[H]` 재현 가능한 개발 의존성

- **작업:** `requirements-dev.txt`에 기존 runtime/test 요구와 원문의 setuptools·pytest-timeout·pytest-xdist·ruff·mypy·coverage를 선언한다. Python 3.10/3.12에 맞는 지원 범위를 확인한다. setup/CI/cache dependency 입력을 함께 갱신하고 runtime package에 개발 도구를 의존시키지 않는다. docs 환경은 별도로 유지한다.
- **보존 경계:** 기존 환경을 불필요하게 다시 만들지 않는다. 새 환경 검증은 작업 전용 경로에서 한다. 사용자가 설치한 도구를 전역으로 교체하지 않는다.
- **완료 기준:** 깨끗한 개발 환경에서 설치·`tests/test_install_tools.py`·CLI smoke 통과. Python 호환성 결과와 docs 환경 분리가 기록된다.

#### WP-05 `[H]` 단일 gate와 hooks

- **작업:** 5.2절의 `scripts/check.sh`를 구현한다. 초기 ruff는 F/E9/B006/B008, mypy는 WP-23 이후 실제 대상에 연결한다. 기존 위반은 증거가 있는 제한 목록으로 관리하고 포괄적 ignore를 추가하지 않는다. timeout·marker 설정을 기존 pytest.ini에 통합한다.
- **Hook:** Git이 결정한 hooksPath를 사용해 fast/default gate를 연결한다. 기존 hook·worktree 공용 설정을 임의로 덮어쓰지 않는다. installer는 반복 실행과 기존 hook 공존을 확인한다.
- **완료 기준:** Phase 0 결함 해결 이후 fast/default exit 0. 격리 fixture에서 검사 실패가 비영 exit로 전파됨을 확인한다. AGENTS.md와 개발 안내의 명령이 실제 gate와 일치한다. 검사 대상이 0개라서 성공한 경우를 차단한다.

#### WP-06 `[H]` CI gate 연결

- **작업:** CPU workflow의 Python 3.10/3.12와 backend-enabled 구분을 유지하며 단일 gate로 연결한다. backend가 없는 job이 full 성공을 주장하지 않게 한다. frontend workflow의 필요한 required check가 항상 보고되도록 workflow-level path filter와 job 조건을 함께 설계한다. workflow가 실행되지 않는데 내부 skip job만 추가하는 방식은 사용하지 않는다.
- **추가:** DCO 검사와 실패 artifact 보존을 연결한다. 병렬 pytest는 공유 자원 격리·직렬 parity 검증 후 활성화한다. timeout은 실제 gate 시간을 근거로 설정한다.
- **완료 기준:** workflow 구조와 호출 명령 검증, 해당 로컬 gate 통과, 변경/비변경 경로의 required job 실행 논리가 설명된다. 원격 실행·Actions 활성화·branch protection은 별도 Pending으로 남긴다. 원격 권한이 없다는 이유로 로컬 workflow 작업을 중단하지 않는다.

#### WP-07 `[B]` Bounded shutdown과 ownership

- **목표:** 원문의 반복 /proc 스캔 비용을 줄이면서 종료 시점의 소유권 보장을 유지한다.
- **입력:** `scripts/run_verl_with_telemetry.sh`의 stop_workload/sidecar_group_is_owned, wrapper ownership·sidecar shutdown test.
- **작업:** 실측으로 반복 scan과 Python 시작 비용을 분리한다. monotonic deadline을 사용하고 필요하면 한 번 실행되는 bounded Python 종료 helper로 교체한다. 매 poll의 전체 scan은 줄이되 종료 중 새 child, leader 종료, PID/PGID 재사용을 처리한다. 그룹 신호 전에는 현재 구성원의 ownership을 다시 입증한다. kill -0만으로 소유권을 인정하지 않는다.
- **시간:** `time.monotonic()` 등 실제 monotonic source를 사용한다. wall clock인 `date +%s%N`을 monotonic 대안으로 쓰지 않는다. cleanup 자체의 비용과 graceful 대기 시간을 따로 측정한다.
- **완료 기준:** 정상·SIGINT/SIGTERM·late fork·reused identity·unreadable /proc·살아남은 child 경계 test, 기존 wrapper/sidecar 회귀 통과. host process 수와 shutdown 시간의 반복 측정치를 기록하고 원문의 5초 grace+작은 overhead 목표를 평가한다. 실제 1,800-process host를 쓰지 못하면 그 규모를 검증했다고 쓰지 않는다.

#### WP-08 `[B]` Pressure card의 evidence·단위 연결

- **입력:** InvestigationView의 PRESSURE_SPECS/PressureCard, distributed.pressureOrder, Python metric_queries·diagnosis_analysis·diagnostic_artifacts.
- **작업:** 원문의 다섯 card에 대해 실제 producer row 형태로 결함을 재현한다. RDMA 등 signal 이름을 canonical 이름과 맞춘다. 단위 정규화는 이름과 변환 계수를 함께 정의하고 sample과 baseline을 같은 스케일로 비교한다. 80 percent와 0.8 ratio는 같은 양이다.
- **누락 처리:** unit이 없을 때 registry/producer 계약으로 유일하게 결정할 수 있으면 그 근거로 변환한다. 결정할 수 없으면 해당 evidence 링크와 수치 delta 가능 여부를 구분하고 unknown을 보존한다. 이름만 같다는 이유로 서로 다른 entity/window/statistic을 연결하지 않는다.
- **완료 기준:** 다섯 card의 positive/negative fixture, percent/ratio·ticks/s·unit unknown·baseline zero·다른 entity·stale/window 불일치 test. npm test/typecheck/build와 실제 Grafana journey에서 연결·단위·context를 확인한다. 새 browser 검사는 기존 CI journey에 연결한다.

### Phase 1: 하네스 기반

#### WP-10 `[H]` 공용 backend fake와 builders

- **작업:** 원문의 `tests/fakes/`에 실제 client 반환 형태(result_quality 포함), label identity, 오류 주입을 갖춘 fake를 만든다. 단일 selector, rate/increase, sum by, 두 selector의 나눗셈만 지원하고 미지원 식은 명시적으로 거부한다. PromQL substring으로 임의 결과를 고르지 않는다. 지원 연산마다 실제 backend 결과와 대조한다.
- **Builder:** run·step·manifest·report의 자주 쓰는 유효 fixture를 만들되 expected result를 제품 계산 결과에서 추출하지 않는다. ClickHouse fake도 동일한 오류·partial 계약을 갖는다.
- **완료 기준:** fake의 positive·reset·boundary·missing·partial·미지원 식 검사와 실제 backend parity. backend 미실행 시 반환 형태 통과와 의미 parity Pending을 별도로 기록한다. 최종 full 전에 parity를 완료한다.

#### WP-11 `[H]` Golden characterization

- **작업:** 5.1절 시나리오로 현재 report와 App rows를 고정한다. 생성 시각만 통제하고 identity/window/quality는 남긴다. 명시적 update 옵션으로만 갱신한다. 잘못된 현재 출력과 아직 없는 rule은 KNOWN_ISSUES에 적는다.
- **완료 기준:** 같은 commit에서 반복 결과가 같고, 의미 필드를 의도적으로 바꾼 fixture는 실패한다. 아직 구현되지 않은 pipeline stage를 빈 golden으로 완료 처리하지 않는다. stage별 golden은 Phase 4에서 실제 연결과 함께 추가한다.

#### WP-12 `[H]` Layer 의존성 검사

- **작업:** 3.1절 목표 구조를 AST import graph로 검사한다. 상대 import, 함수 내부 import, package initializer를 포함한다. 기존 위반은 source/target별 allowlist로 두고 새 위반과 사라진 허용 항목을 실패시킨다. 단순 파일 rename으로 위반을 숨기지 않는다.
- **완료 기준:** SDK import가 collector/analysis를 적재하는 실제 경우도 subprocess smoke로 탐지한다. 목표 구조로 옮긴 경로의 위반은 allowlist에서 제거된다. 동적 import 등 정적 검사의 한계는 명시한다.

#### WP-13 `[H]` 행동 기준 test 재배치

- **작업:** 원문의 리뷰 회차 test를 모듈·행동 기준으로 옮기고 공용 helper를 분리한다. 단순 이름 변경으로 중복 test를 무조건 삭제하지 않는다. assert 의미는 이 WP에서 유지한다.
- **완료 기준:** 이전/새 node ID와 parameter case의 일대일 대응표로 수집 보존을 확인한다. 함수 이름 집합만 비교해 동일 이름·parameter 손실을 놓치지 않는다. 전체 suite와 test 간 import/layout 검사가 통과한다. 관련 CI·문서·후속 WP의 test 경로도 갱신한다.

### Phase 2: Core 도메인 모델

#### WP-20 `[R]` Observation·Status·Issue

- **작업:** 3.3절 모델을 Python 3.10 호환 형태로 구현한다. 값·상태의 허용 조합, finite value, unit/statistic을 생성 경계에서 검증한다. 기존 missing 문자열은 lossless legacy adapter로 변환하고 producer와 parser가 공통 Issue를 사용하게 한다.
- **보존 경계:** OBSERVED/ZERO/PARTIAL의 값 의미와 NO_DATA/QUERY_FAILED/STALE를 구분한다. 알 수 없는 legacy reason을 삭제하지 않고 원문과 unknown 상태를 유지한다. retry 의미 변경은 WP-40으로 분리한다.
- **완료 기준:** 기존 report golden 불변, legacy 왕복·invalid 조합·unknown reason test, 실제 오류 생성과 소비 경로가 새 모델을 사용한다.

#### WP-21 `[R]` EntityRef·Scope·identity

- **작업:** 원문의 EntityRef/Scope와 유일한 env 해석기를 구현한다. node/engine/device/worker/rank/replica를 잃지 않고 query selector·join key·drill-down까지 연결한다. 기존 caller별 hostname/None fallback은 명시적인 인자로 유지한다. 새 core가 SDK/collector를 import하지 않는다.
- **검증:** 원문 3.3절 필드는 예시이므로 실제 계약의 run·local_rank·observer/resource 구분 등 필요한 필드를 누락하지 않는다. alias 정규화와 scope별 projection을 명시하고 서로 다른 scope를 같은 key로 합치지 않는다.
- **완료 기준:** schema scope와 일치, golden 불변, alias·충돌·missing/empty·다른 entity의 negative test, 고정 seed property 왕복 검증. 원본 label을 복원할 수 없는 projection은 lossless라고 주장하지 않는다.

#### WP-22 `[R]` TimeWindow·clock 모델

- **작업:** 원문 TimeWindow와 legacy 변환을 구현하고 분산된 검증을 대체한다. wall/monotonic/calibrated domain, node/reference/session, uncertainty와 raw timestamp를 유지한다. epoch 0·단위·정밀도·finite/order를 검사한다.
- **완료 기준:** 기존 오류와 golden 보존, ns/ms/s 왕복 허용 오차 명시, 다른 monotonic host/reference/session 간 비교 거부, clock discontinuity·stale calibration·불완전 window test. 단위를 값 크기만으로 추정하지 않는다.

#### WP-23 `[H]` Core mypy

- **작업:** core를 strict mypy 대상으로 지정하고 check.sh fast에 연결한다. Any/cast/ignore로 불변식을 우회하지 않는다. 새 enum/dataclass가 Python 3.10과 3.12에서 import·직렬화되는지 검사한다.
- **완료 기준:** core type check와 runtime test 통과. 이후 실제로 이전된 backend/pipeline을 같은 방식으로 추가할 위치가 명시돼 있다.

### Phase 3: Signal Registry와 생성물

#### WP-30 `[R]` Registry v1

- **작업:** 원문의 signals.toml/subsystems.toml과 검증 로더·Python 생성기를 구현한다. DEFAULT_QUERIES, DEFAULT_THRESHOLDS, PROFILE_SIGNALS, SIGNAL_SCOPE, BASELINE_REQUIRED와 query_name/signal_name 매핑을 옮긴다. 최초 전환은 현재 동작하는 T0와 관련 기존 signal부터 시작하고 나머지 T0/T1은 WP-70에서 연결한다.
- **검증:** metric 참조·unit·selector 허용 label·subsystem·source·scope·threshold 형식을 검사한다. metric contract ID와 exporter의 raw name은 구분한다. 새 registry를 runtime에서 읽는지 생성물만 읽는지 명시하고 해당 package asset을 설치본에 포함한다. Python 3.10의 TOML 처리는 기존 tomli 호환 경로를 따른다.
- **완료 기준:** 생성물 --check, golden 불변, 설치 checkout 밖의 registry 사용 smoke, 기존 상수 caller가 생성물을 사용하는지 확인. config/*.toml의 배포 누락을 검사한다.

#### WP-31 `[R]` TS registry와 row schema

- **작업:** 실제 Rule/LLM/replica/storage row 종류를 빠짐없이 조사해 diagnosis-row schema를 만들고 TS discriminated type을 생성한다. type·enum·optional 등 실제 schema가 사용하는 제한된 subset을 정확히 지원하고 미지원 schema keyword는 조용히 버리지 않는다. decoder를 거쳐 현재 raw RecordRow를 교체하고 phase/subsystem/unit/signal 사본을 생성물로 전환한다.
- **보존 경계:** legacy row의 optional field·unknown additive field·개별 손상 record 처리와 partial 결과를 보존한다. 정상 row 하나가 잘못된 이웃 row 때문에 사라지지 않는다. runtime schema validation과 compile-time TS 타입을 구분한다.
- **완료 기준:** 모든 golden rows와 설치된 schema 검증, malformed/legacy row negative test, TS parity·npm test/typecheck/build. field 합집합을 모두 required로 만들어 기존 row를 거부하지 않는다.

#### WP-32 `[R]` Canonical panel key

- **작업:** 원본 dashboard·생성 panel에 stable key를 부여하고 App/Python/registry의 숫자 ID 참조를 key 기반으로 전환한다. Grafana save/load·provision 경로에서 key 보존을 확인한다. 임의 extension field가 보존되지 않으면 source와 함께 배포되는 검증된 mapping으로 제공한다.
- **호환성:** dashboard UID와 기존 URL의 panel ID는 유지한다. key lookup 도입을 이유로 기존 공유 링크를 깨지 않는다. View별 생성·반복 panel의 key uniqueness 범위를 명시한다.
- **완료 기준:** 모든 참조가 실제 provision 결과에 존재하며 중복/누락 key를 검출한다. ID 변경 fixture에서도 내부 lookup이 동작하고 실제 Grafana panel/query/context가 같다.

#### WP-33 `[R]` Registry 기반 문서

- **작업:** signals·rules·config keys include를 생성하고 현재 문서의 사본을 교체한다. metadata에 producer/source·scope·unit·구현/검증 상태를 포함한다. --check로 source와 생성물 drift를 검사한다.
- **완료 기준:** docs build -W, 링크·diagram 회귀, 설치/사용 명령과 실제 CLI 대조. 아직 producer가 없는 항목을 지원 완료라고 생성하지 않는다.

#### WP-34 `[R]` Registry 기반 demo metadata

- **작업:** demos/live.py와 scenario의 signal/metric 이름·단위를 registry에서 가져온다. 기존 값 생성·fault 의미는 유지하고 synthetic 표기를 보존한다. 현재 raw exporter sample과 generated metadata를 연결한다.
- **완료 기준:** demo/golden 회귀, signal 이름 drift 검사, 실제 dashboard query가 demo data를 받는 end-to-end 경로. 생성기와 demo가 같은 오타를 공유해 통과하지 않도록 외부 source contract fixture와 대조한다.

### Phase 4: Backend·진단 pipeline·artifact

이 Phase의 상세 WP는 제공된 원문에서 누락돼 있었다. 아래 명세는 원문 3.3–3.8절의 설계와 기존 WP 참조를 현재 source에 연결한 보완 명세이며 원문 인용이 아니다.

#### WP-40 `[B]` 구조화된 오류와 bounded retry

- **목표:** 원문이 WP-20 후속으로 지정한 NO_DATA/retry 의미를 정리한다.
- **입력:** diagnostics.run_once/run controller, metric_observations, analysis/deadline.py의 worker, backend/worker deadline·ownership test. symbol이 옮겨졌으면 현재 call path를 찾는다.
- **작업:** 문자열 prefix 대신 Status/Issue로 retry를 결정한다. 성공한 조회의 NO_DATA는 같은 완료된 window에 무한 retry하지 않는다. QUERY_FAILED/TIMEOUT/PENDING은 남은 budget과 policy 안에서만 retry한다. live 데이터가 아직 settle 중인 상태는 PENDING으로 명시해 늦게 도착한 정상 데이터를 잃지 않는다. BUDGET_EXHAUSTED는 새 worker를 무한 생성하지 않는다.
- **완료 기준:** no data·late arrival·transient failure·permanent failure·partial success·restart의 호출 횟수와 최종 상태를 assertion으로 확인한다. 기존 worker termination·projection recovery 경로를 유지하고 ADR에 기대 결과 변경을 기록한다.

#### WP-41 `[R]` Backend 계층과 transport 교체

- **목표:** 원문의 backends/base·prometheus·clickhouse·loki와 공통 QueryResult/Deadline을 실제 호출 경로에 적용한다.
- **입력:** prometheus.py, diagnostics.ThreeFSClient/_DeadlineThreeFSClient, _http_transport.py, operations의 private client import, 관련 backend/clock/LLM transport test.
- **작업:** SQL·PromQL·HTTP transport를 분리하고 필요한 interface만 Protocol로 제공한다. 기존 구현을 교체할 수 있으나 DNS/header/body/redirect deadline, byte cap, credential 보호, cancellation·reaping을 보존한다. subprocess 격리 제거는 같은 실패 경계를 입증한 대체 구현이 있을 때만 한다. health/cluster/subsystems가 엔진 private client를 import하지 않게 한다.
- **완료 기준:** 정상·HTTP 오류·invalid JSON·partial body·slow trickle·DNS/child hang·cancel test, 실제 Prometheus/ClickHouse protocol 검사, 기존 public constructor adapter와 error contract parity. fake-only 통과를 실제 transport 검증으로 쓰지 않는다.

#### WP-42 `[R]` Collect·align 경계 분리

- **목표:** 원문 pipeline/collect.py와 align.py를 만들고 수집·시간 정합을 엔진에서 분리한다.
- **입력:** diagnostics.analyze, collect_metrics/collect_query, clock_quality, evidence_quality, storage_series, rollout_replicas, TimeWindow와 registry.
- **작업:** current/baseline window와 source identity를 입력으로 ObservationSet을 만든다. source clock으로 query window를 변환해야 하는 경우 I/O 전에 변환하고 raw/reference window와 uncertainty를 결과에 남긴다. 순수 align 단계가 추가 backend 요청을 숨겨서 수행하지 않게 한다. collection budget·query count·bytes·sample cap과 optional source 상태를 보존한다.
- **완료 기준:** collect/align stage golden, 원래 query window와 source timestamp 비교, no baseline·remote clock unknown·calibration session 불일치·partial source test. metric fetch를 post-query timestamp 보정만으로 대신하지 않는다. Rule 결과 golden은 불변이다.

#### WP-43 `[R]` Select·assess와 공통 EvidenceBundle

- **목표:** 원문 baseline 선택·entity 매칭·quality 평가를 하나의 계약으로 제공한다.
- **입력:** diagnosis_analysis의 workload_matches/select_baseline, evidence_quality, clock_quality, rollout_state/replicas, core identity/time/observation.
- **작업:** pure select·assess 단계를 만들고 execution mode, workload, policy/replica generation, boundary scope, entity가 맞는 observation만 비교한다. shared signal은 귀속 관계를 생성하지 않는다. EvidenceBundle에 안정적인 evidence ID, source query, current/baseline, unit/statistic, raw quality와 missing reason을 담는다.
- **완료 기준:** 같은 entity의 정상 비교, cross-engine/device/rank 반례, old selection·incomplete baseline·stale source·실측 zero·unknown clock 검사. stage golden과 기존 baseline/evidence 회귀가 같고 CPU budget이 무제한 all-pairs 비교로 늘어나지 않는다.

#### WP-44 `[R]` Evaluate·cap·assemble

- **목표:** 원문 단계별 순수 pipeline과 단일 strength cap을 구현한다.
- **입력:** diagnosis_analysis.evaluate_rules, diagnostic_signals, diagnostics.analyze, storage/rollout 후처리, 기존 candidate schema와 strength/state.
- **작업:** subsystem별 rule을 분리해 EvidenceBundle을 소비하게 하고 QualityFacts에 의한 최종 cap을 한 경로에 모은다. 근거와 반대 근거, scope 제한, clock·sampling 제한의 순서와 결과를 characterization으로 보존한다. 기존 후보 state와 LLM hypothesis를 무리하게 하나의 정렬 enum으로 축약하지 않는다. assemble은 pure report 생성만 맡는다.
- **완료 기준:** 단계별 golden, 여러 cap 동시 적용과 중복 적용의 안정성, evidence 삭제/추가 순서 반례, 출력 필드 보존. DiagnosticEngine은 호환 façade로 단계 호출만 담당하고 기존 중복 판정 코드는 제거한다. 50줄 목표를 맞추려고 복잡성을 다른 거대 함수로 옮기지 않는다.

#### WP-45 `[R]` Rule/LLM 공통 evidence 경로

- **목표:** 원문대로 두 진단 방식이 같은 수집·scope·quality 계약을 사용한다.
- **입력:** llm_diagnosis.collect_packet/packet_from_report/model_view/validate_diagnosis, llm_investigation, 공통 EvidenceBundle, LLM evidence semantics test.
- **작업:** 직접 metric을 분석하는 LLM 경로와 Rule 보고서 기반 경로가 공통 bundle builder를 사용하도록 재구성한다. LLM 입력을 Rule이 선택한 후보나 signal로만 제한하지 않는다. 기존 허용된 사용자 query config를 adapter로 지원하고 evidence ID·unit·window·scope·missing을 실제 bundle과 대조한다.
- **보존 경계:** 민감 label/raw payload의 모델 전달 제한과 크기 budget을 명시적으로 유지한다. allowlist 코드를 없앤다는 이유로 모든 raw event를 모델에 보내지 않는다. 모델의 새로운 numeric claim·다른 entity 인용·누락 evidence 참조는 거부한다.
- **완료 기준:** 같은 source 입력의 Rule/LLM observation parity, standalone LLM config 회귀, malformed/unsupported evidence·timeout·rejected answer·multiple invocation projection test. fake model 검증과 실제 모델 호출을 구분한다.

#### WP-46 `[R]` Artifact·schema·공통 row projection

- **목표:** 원문의 artifacts/run_store·diagnosis_io와 유일한 공통 projection 경로를 완성한다.
- **입력:** fileio, jsonl_cache, step_history, operations/run_artifacts·runs, diagnostic_artifacts, llm_investigation, generated row schema.
- **작업:** atomic write·bounded decode·partial JSONL·restart/cache invalidation을 공통화한다. Rule과 LLM이 공통 필드 projector를 사용하되 방법별 실제 정보만 채운다. reader는 기존 schema와 unknown additive field를 처리하고 손상 record를 격리한다. schema는 full test에서 검증하고 runtime write/read 경계 검증도 실제 배포 의존성과 함께 제공한다.
- **완료 기준:** 구 artifact fixture 조회, 새 결과 재시작 후 읽기, truncated/oversized/invalid record·duplicate/conflict·projection repair·writer failure test. 기존 값에 없는 attribution을 default로 만들어 넣지 않는다. schema version 변경이 필요하면 별도 Behavior commit/ADR로 reader 선행·writer 후행 순서를 지킨다.

### Phase 5: Config와 lifecycle 통합

이 Phase는 원문 3.7절을 구체화한 보완 명세다. Bash 구조를 유지하기 위한 재사용보다 목표 Python resolver/supervisor의 완성을 우선한다.

#### WP-50 `[B]` 단일 config resolver와 snapshot

- **입력:** operations/config.py, cli.py, 세 launcher, diagnosis/LLM config loader, examples/config·config docs·config test.
- **작업:** 원문의 ops/config.py와 검증된 ResolvedConfig를 구현한다. CLI > env > TOML/trusted Bash > default 우선순위를 한곳에 모은다. 기존 flat/runtime config와 nested diagnosis config는 입력 adapter로 받는다. 명시적 사용자 설정은 그대로 유지하고 기본값 drift는 원문의 Python canonical 값으로 통일하며 ADR과 전환 안내를 쓴다.
- **Shell 경계:** export snapshot은 정확한 quoting과 민감값 출력 정책을 갖는다. trusted Bash는 기존 신뢰 경계를 유지하고 반복 평가·eval을 추가하지 않는다. shell은 이미 resolve된 snapshot을 사용하며 같은 key의 독립 default를 갖지 않는다.
- **완료 기준:** 모든 entrypoint 우선순위 matrix, space/quote/dollar/newline 값, secret redaction, invalid config·legacy config, installed asset smoke. 명시값 불변과 기본값 변경을 분리해 검증한다.

#### WP-51 `[R]` Process identity와 ownership 공통화

- **목표:** 원문의 ops/process_identity.py에서 identity/lock/ownership/stop 계약을 제공한다.
- **작업:** PID/start time/boot ID/session/owner marker와 lock을 묶어 검사한다. 새 형식 reader부터 도입하고 현재 live state의 legacy 형식은 검증 가능한 범위에서만 읽는다. 소유권이 불명확하면 종료하지 않는다. wrapper·sidecar·controller의 중복 검사를 공통 모듈로 대체한다.
- **완료 기준:** PID·PGID 재사용, stale pidfile, reboot, permission denied, lock contention, late child, leader exit, concurrent shutdown test. WP-07의 bounded shutdown과 안전 조건을 보존한다. 기존 사용자 process는 종료되지 않는 negative test를 둔다.

#### WP-52 `[R]` Python supervisor와 얇은 launcher

- **목표:** 원문대로 lifecycle을 Python supervisor로 옮기고 세 shell launcher의 중복 로직을 제거한다.
- **작업:** `run_telemetry.sh`, `run_verl_with_telemetry.sh`, `verl_local.sh`를 순차 전환한다. 각 전환은 argv·cwd·환경·로그·exit code·signal·logger settle·health artifact parity로 검증한다. Python interpreter 선택과 기존 workload 환경을 telemetry 환경과 구분한다. 실패 cleanup과 session ownership은 WP-51을 사용한다.
- **전환:** shell은 env 준비와 exec 위임만 담당한다. 사용자 명령 형식은 유지하고 package 설치본에서도 helper/supervisor를 찾는다. 모든 caller 전환 후 구 lifecycle 분기를 삭제한다.
- **완료 기준:** 각 launcher 성공·workload 실패·시작 도중 signal·child fork·sidecar crash·logger timeout·실행 충돌 test, 전체 lifecycle regression, installed CLI smoke. 실제 VERL workload 검증은 별도 Pending으로 구분한다.

### Phase 6: Grafana App 구조

이 Phase는 원문 3.9절을 구체화한 보완 명세다. 새 Prometheus/Loki client나 독립 time picker는 만들지 않는다.

#### WP-60 `[B]` Native Router/Scenes 상태 통합

- **입력:** InvestigationView, ShellView/scene factories, app-context·context·time-context, navigation/identity-variable test, 기존 browser journey.
- **작업:** 원문이 지목한 matrix/worker/tab/storage 선택은 지원되는 URL-synced Scenes variable 또는 native router state로 통합한다. URL key와 공유 링크를 유지하고 직접 window.location 읽기·수동 재렌더 counter를 제거한다. theme/timezone/time range는 Grafana native 기능과 충돌하지 않게 처리한다. theme을 별도 variable로 만들기 전에 실제 native 계약을 확인한다.
- **완료 기준:** Reload·Back·Forward·deep link·기존 응답에 없는 선택·run/time 변경·cached page remount·retry 후 metadata 갱신의 실제 browser test. App core가 optional datasource 오류로 막히지 않는다. 필요한 상태 전이 수정은 ADR에 기록한다.

#### WP-61 `[R]` Domain view-model과 UI 분리

- **작업:** 원문의 generated/data/domain/scenes/ui 구조로 InvestigationView와 PhaseMatrix 책임을 나눈다. query runner·variable 수명은 scenes, 순수 projection은 domain, 렌더링은 ui가 담당한다. Python에서 계산하는 clock/workload/candidate 의미를 TS에서 재구현하지 않는다. 반드시 양쪽에서 필요한 순수 계산만 shared golden parity로 관리한다.
- **완료 기준:** 기존 화면 model/row fixture parity, hook·subscription·runner 해제 검사, cached SceneApp에서 stale selection/data가 남지 않음, npm·실제 browser 회귀. 파일만 이동하고 동일한 거대 mutable 조립 로직을 남기는 상태는 미완료다.

#### WP-62 `[B]` Reason code·readiness·품질 표시

- **작업:** 상태 분기와 번역을 구조화된 reason code로 전환하고 legacy 문장 decoder는 입력 경계에 한정한다. 문장 startsWith/equality와 한국어 selector 의존을 제거한다. data-state/data-reason과 실제 URL·DOM·query 완료 조건을 공통 browser helper에서 사용한다.
- **보존 경계:** measured zero/no data/stale/query failure/unknown clock 표시, 단위·scope·time context, loading/error/retry, 접근성을 유지한다. 지원하지 않는 code는 안전한 unknown 표시로 남기고 raw 민감 내용을 노출하지 않는다.
- **완료 기준:** 문구 변경이 판정을 바꾸지 않는 test, 오류→retry→성공과 optional Loki 없음 journey, Light/Dark·1440/1024/390px 화면 확인. 새 검사는 기존 ci_demo.py/CI 경로에서 실행된다.

### Phase 7: Agent RL telemetry·KPI·rules

이 Phase는 원문 4장의 T0/T1 설계를 구체화한 보완 명세다. 새로운 sandbox runtime·scheduler·telemetry backend는 구현하지 않는다.

#### WP-70 `[B]` Producer·contract·source 연결

- **입력:** 원문 4.4절의 모든 T0/T1, metrics.json, registry, adapters/verl·rollout, events/worker_events/sandbox SDK, native-source 설정과 demo.
- **작업:** 각 T0/T1마다 producer/source/version alias, field/metric, unit/statistic, scope/labels, timestamp, query, optional missing, panel을 표로 확정한다. 이미 존재하는 native metric은 지원 버전의 실제 exporter fixture와 대조하고 query를 연결한다. 없는 application 계측은 기존 framework 외부의 명시적인 SDK/span adapter로 제공한다. 원문의 trajectory/tool/acquire/reward/weight-apply/wait 계측을 실제 emit 경로와 사용 예제로 구현한다.
- **경계:** engine running=0에서 SM idle을 만들거나 trainer step으로 rollout policy version을 추정하지 않는다. profiler가 필요한 communication/overlap·rank timer는 명시적 관측 입력으로 받는다. 계측되지 않은 production source는 missing으로 남으며 contract만 만들고 producer 완료로 표시하지 않는다.
- **완료 기준:** 모든 T0/T1에 작동하는 producer/adapter 또는 검증된 native source 연결과 positive/negative fixture가 있다. source가 미설정일 때 core가 유지된다. 고카디널리티 ID는 event attribute에만 남고 label 규모를 검사한다. 실환경 미측정은 표의 validation 열에 분리한다. 구현되지 않은 필수 producer는 goal의 미완료 항목이다.

#### WP-71 `[B]` Step·trajectory·async KPI

- **작업:** 원문 L0/L1과 Agent RL KPI를 구현한다. trajectory duration·long-tail·tool wait·goodput/truncation·policy lag·trainer/rollout wait를 의미가 같은 cohort와 명시적 경계에서 계산한다. sync의 exclusive phase와 nested tool span을 구분하고, async를 phase 합으로 설명하지 않는다. span 누락·중첩·중복·out-of-order·실패·zero denominator를 처리한다.
- **정의:** tool wait는 trajectory별 관련 tool 구간의 합집합/유효 trajectory 구간으로 계산해 병렬 tool 중복을 제거한다. goodput은 생성/학습 사용 cohort가 연결될 때만 계산한다. clipping과 timeout·error·truncation은 별도 근거 없이 동일시하지 않는다. parent rl_step과 child phase를 함께 더하지 않는다.
- **완료 기준:** sync·one-step-off·fully-async fixture, overlapping spans·unknown policy·늦은 완료·다른 epoch/cohort·empty data 반례, bounded aggregation. schema·registry·query·rows·App·docs가 새 KPI를 일관되게 표현한다.

#### WP-72 `[B]` Bottleneck rule과 end-to-end evidence

- **작업:** 원문 4.5절 판정 matrix의 각 후보를 phase slowdown·workload comparability·같은 entity/window의 signal·counter evidence로 구현한다. 임계값은 registry의 단위/방향과 함께 명시하고 원문의 예시값·현재값·실측 보정값을 구분한다. 모델이 임의의 운영 임계값을 외부에서 검증된 수치처럼 기술하지 않는다.
- **Cap:** shared-only evidence, unsafe clock, 부족한 sample, missing source의 strength 제한을 WP-44의 단일 cap 경로에 넣는다. rule의 candidate와 LLM hypothesis를 구분하고 correlation을 확정 attribution으로 올리지 않는다.
- **완료 기준:** 각 rule의 유발 fault와 유발되면 안 되는 counter fault, workload 변화·다른 entity·clock unsafe·late source·partial backend 회복 시나리오가 통과한다. fault/expected/actual/strength confusion matrix를 남긴다. Run→Slow Step→Candidate→Evidence→Timeline/Subsystem의 실제 Grafana 경로와 같은 report/rows를 검증한다. 실환경 recipe와 Pending을 기록한다.

### Phase 8: 정리와 완료 증명

#### WP-80 `[R]` 구 구현 제거·호환 경로·문서 정리

- **작업:** 전환 완료된 내부 모듈·중복 로직·dead generator·중복 test를 제거한다. 각 삭제에 새 호출 경로와 계약 검증을 연결한다. 외부 import/CLI shim은 실제 릴리스 정책을 만족한 것만 제거하며, 아직 필요한 shim은 한 곳에 남기고 제거 조건을 기록한다. shim이 남았다는 이유로 구 내부 구현 전체를 유지하지 않는다.
- **문서:** 과거 validation/review는 출처와 기존 URL/anchor를 보존해 internal 기록으로 정리한다. 사용자 가이드·README·navigation·examples·package asset을 새 구현에 맞춘다. 역사 기록을 삭제하거나 과거 성공/실패를 덮어쓰지 않는다.
- **완료 기준:** reference/import search와 설치본 smoke, old artifact reader·필요한 compatibility entrypoint, 전체 golden/CPU/frontend/browser/docs 통과. 목표 call path에 우회하는 구 구현이 남지 않는다.

#### WP-81 `[H]` 지침 정리와 전체 goal 검증

- **작업:** 원문 AGENTS 축소 방향에 따라 실행 명령·code map·핵심 경계만 남기고 상세 계약은 test/ADR/invariants 문서로 연결한다. 80줄은 가독성 목표이며 안전 규칙을 삭제하기 위한 강제 상한이 아니다. 삭제/이동한 규칙의 대응 위치를 기록한다.
- **최종 검증:** 최종 source tree에서 5.2절 full, Python 3.10/3.12, 설치본 smoke, generator drift, docs·browser 조건을 확인한다. 이전 결과를 재사용했다면 동일 tree와 환경임을 입증한다. 마지막 status 문서 변경은 문서 검사를 다시 수행한다.
- **완료 기준:** G.4 전체 항목 통과, 모든 WP done의 source/검증/병합 근거 일치, 외부 Pending과 실제 실패 구분, cleanup 확인. 로컬 main 최종 SHA와 검증 범위·미실행 실환경 항목을 보고한 뒤 goal을 완료한다. 전체 구현이 끝나지 않았거나 필수 검증이 Pending이면 완료 처리하지 않는다.

## 7. Goal 실행 가이드

### 7.1 운영 모델과 상태

전체 재설계는 하나의 goal이며, WP는 그 안의 검증·commit·로컬 병합 단위다. 선행 WP가 완료되고 실제 변경 범위가 독립적인 WP는 별도 worktree에서 최대 3개까지 병렬로 진행한다. `main` 병합과 상태표는 주 작업자가 직렬로 처리한다. 원격 PR이나 매 WP의 사용자 승인 대기를 필수 단계로 두지 않는다. 명시적인 판단이 필요한 계약 변경만 질문한다.

주 작업자는 매 통합 시점에 준비된 WP의 실제 위험과 사용자 영향을 다시 평가해 다음 worker를 배정한다. 서로 독립적인 높은 우선순위 작업은 동시에 진행할 수 있으며, 먼저 완료된 branch부터 병합한다. 후속 WP가 선행 WP의 결과를 필요로 하면 그 결과가 main에 들어갈 때까지 시작하지 않는다.

진행 상태는 `docs/development/redesign-status.md` 한곳에 기록한다. 각 행은 `WP | 상태 | baseline SHA | 결과 SHA | 검증 명령·결과 | 로컬 병합 근거 | 장애물/후속`으로 구성한다. 상태는 todo, in-progress, verified, done, blocked를 사용한다. verified는 필수 로컬 검증이 끝난 상태, done은 결과 commit의 main 통합까지 확인한 상태다. 실제 hardware validation과 원격 적용은 별도 표로 관리한다.

WP blocked는 원인과 재개 조건을 기록하는 작업 상태다. 실행 환경의 goal 상태 변경에는 별도 도구 규약을 따른다. 단 한 번의 실패로 goal을 임의로 blocked/paused/complete로 바꾸지 않는다. 사용자가 명시적으로 멈추라고 하면 그 요청을 따른다.

### 7.2 최초 실행 프롬프트

아래 `<계획서 경로>`를 이 실행본의 실제 경로로 바꿔 사용한다. 문서 반입 후에는 저장소의 `docs/development/redesign-plan.md`를 사용한다.

```text
<계획서 경로>를 읽고 G.1~G.4의 전체 재설계 목표를 goal로 등록해 실행해줘.

원문의 목표 아키텍처와 Phase 0~8 전체 계획을 따라줘.
현재 저장소 구현을 정답으로 보거나 재사용을 필수 조건으로 두지 말고,
새 구조에 맞지 않는 내부 구현은 교체·삭제해도 돼.
계약 변경은 문서의 Behavior/ADR·전환 규칙대로 처리해줘.

8장의 실행 기준을 적용하고, 6.1의 실제 선행 관계와 변경 파일을 확인해줘.
준비된 WP 중 데이터 정확성·운영 안전성·사용자 결함을 우선해줘.
독립 WP만 별도 worktree의 worker 최대 3개로 구현·관련 검증·검토해줘.
main 병합과 상태표 갱신은 주 작업자가 직렬로 맡아줘.
공유 계약 변경들을 통합한 Phase 경계와 최종 완료에 전체 CPU suite를 한 번 실행해줘.
WP-02의 clean/오염 환경 비교 증거를 유지해줘.
Signed-off-by commit과 로컬 main fast-forward 병합 후 계속 진행해줘.
원격 push·PR·배포·설정 변경은 하지 마.
기존 변경과 사용자 자원을 보존하고, 실행하지 않은 검증은 Pending으로 남겨줘.

Phase 0이나 문서 반입만 끝내고 goal을 완료하지 마.
G.4 전체 완료 조건을 충족한 뒤 최종 결과를 알려줘.
중간에는 현재 WP, 완료한 항목, 필요한 결정이나 장애물을 짧게 알려줘.
```

### 7.3 WP별 세션 절차

1. **상태 확인:** goal objective, 기준 문서, status 표, AGENTS.md와 관련 하위 지침을 읽는다. 실제 branch·작업 트리·main SHA를 확인한다. 기존 변경을 이동·stash·폐기하지 않는다. 독립 worktree로 안전하게 진행할 수 있으면 사용하고 그렇지 않으면 충돌 범위만 질문한다. 원격 pull로 기준을 자동 변경하지 않는다.
2. **선행 확인:** 6.1절에서 해당 WP가 실제로 의존하는 WP가 done인지 결과 commit과 main ancestry로 확인한다. status 문서가 stale이면 Git·검증 증거로 먼저 정정한다. 단순히 표가 done이라고 제품 동작까지 확인됐다고 간주하지 않는다.
3. **branch/환경:** 로컬 main에서 `redesign/wp-<번호>-<slug>` branch와 전용 worktree를 만든다. 이미 이번 WP의 branch가 있으면 소유·기준점을 확인한 뒤 재개한다. 각 worker는 자기 worktree와 출력 경로만 수정한다. 프로젝트 Python 환경·Node·backend/browser 도구를 확인하고 필요한 작업 전용 환경을 준비한다. runtime과 docs 환경을 분리한다.
4. **재확인:** WP 입력과 caller·test·fixture·schema·config·exports·package asset을 읽는다. 원문의 과거 결함이 이미 해결됐으면 같은 경계의 검증으로 확인한다. 현재 코드 경로와 문서가 달라도 의미가 명확하면 위치와 상태를 갱신하고 진행한다.
5. **baseline/red:** 변경 위험에 맞는 baseline을 실행하고 SHA·환경·명령·결과·skip을 기록한다. Behavior regression은 수정 전 실제 결함으로 실패함을 확인한다. 이미 구현된 올바른 동작이나 새 기능의 문서 변경에 인위적인 red를 만들지 않는다.
6. **구현:** 원문의 목표 구조를 구현한다. 현재 helper가 목표에 맞지 않으면 대체하고 모든 caller를 전환한다. 기계적 이동과 의미 변경은 구분해 commit한다. 서로 다른 WP를 검증 없이 한꺼번에 섞지 않는다.
7. **검증:** 각 worker가 WP 완료 기준과 5장의 관련 test를 자기 worktree에서 실행한다. 주 작업자는 통합된 독립 WP의 공유 계약·Phase 경계·최종 완료 검사를 하나의 전체 CPU 실행으로 묶고, 동일한 제품·test tree에서는 반복하지 않는다. WP-02의 두 환경 전체 suite 비교는 예외 없이 유지한다. baseline 실패·새 실패·해결·skip을 구분한다. assertion 약화, test 삭제/무조건 skip, ownership 우회로 통과시키지 않는다. UI 변경은 실제 Grafana/browser, package 변경은 설치본, transport 변경은 실제 경계 검사를 포함한다.
8. **검토:** 전체 diff와 연결 코드를 읽고 새 test가 기대값을 제품 계산으로부터 생성하지 않는지 확인한다. Refactor의 output 불변과 Behavior의 ADR 대응을 확인한다. staged/unstaged 모두 `git diff --check`하고 검증 tree와 commit 대상이 일치하는지 확인한다. 검증 후 code를 바꾸면 영향 검사를 다시 실행한다.
9. **commit/통합:** Worker는 자기 worktree에서 논리적 파일만 stage하고 Signed-off-by commit을 만든 뒤 `git log -1 --format=%B`로 확인한다. 주 작업자는 결과와 겹침을 검토하고 한 branch씩 통합한다. 병렬 branch의 기준 main이 뒤처졌다면 해당 worker branch에 최신 `main`을 merge commit으로 가져와 충돌을 해결·재검증한다. 이 병합 commit에도 Signed-off-by를 넣는다. 그다음 로컬 `main`에서 `git merge --ff-only <작업 branch>`를 실행한다. reset·force push·임의의 이력 재작성으로 우회하지 않는다.
10. **상태 확정:** 주 작업자는 구현 commit이 main에 포함됐는지 확인한 뒤 status의 해당 WP 행을 done으로 바꾸고 결과 SHA·검증 근거를 기록한다. 상태표 수정은 다른 worker와 공유하지 않고 별도 Signed-off-by commit으로 직렬 병합한다. 이번에 만든 병합 완료 branch와 worktree만 정리한다.
11. **계속:** 소유 테스트 자원을 정리하고 다음 WP로 진행한다. 계획 변경이나 사용자의 결정이 없으면 새 목표 등록·매번 승인 질문을 반복하지 않는다. 마지막 WP에서는 G.4 전체를 검증한 뒤 goal 완료 상태와 최종 보고를 남긴다.

중간에 세션이 끊기면 status·Git·검증 artifact로 재개한다. 구현 commit은 main에 있지만 상태 문서만 뒤처진 경우 이를 보완하며 구현을 반복하지 않는다. raw 로그는 Git에서 제외한 작업별 artifact 경로에 두고 상태표에는 필요한 작고 비민감한 근거만 연결한다.

### 7.4 결과 보고

각 WP 진행 보고는 `완료한 동작 / 변경 경로 / 검증 결과 / 남은 장애물 / 다음 WP`를 짧게 기록한다. 최종 보고에는 다음을 포함한다.

- 원문의 목표 구조 중 실제로 구현된 경로와 삭제한 구 구현.
- 최종 main SHA, WP 상태표 위치, 필수 gate의 실제 실행 결과.
- 의도한 계약 변경과 ADR·호환성·전환 안내.
- 미실행 GPU/VERL·물리 multi-node·운영 backend·실제 모델 검증과 재개 명령.
- 원격 push/PR/설정의 미실행 상태, 소유 테스트 자원 cleanup.

Test 숫자만 나열해 완료를 설명하지 않는다. 검증 수준과 입력·상태 전이·실패 경계를 설명한다. 전체 goal의 일부만 끝났으면 완료로 표현하지 않는다.

### 7.5 질문·장애물 처리

일상적인 module 이름·함수 분리·test 위치·원문에 명시된 개발 도구 도입은 실행 범위 안에서 결정한다. 매 단계 승인받지 않는다. 다음 경우에만 영향을 받는 작업을 보류하고 필요한 최소 질문을 한다.

- 원문과 이 실행본이 답하지 않는 사용자 계약·판정 의미를 정해야 한다.
- 명시한 전환 범위를 넘어 기존 데이터의 파괴적 migration이나 사용자 process 종료가 필요하다.
- current branch/main 동시 변경 때문에 기존 작업을 보존하며 병합할 수 없다.
- 필수 도구·권한·network·테스트 자원을 준비할 수 없어 해당 acceptance를 입증할 수 없다.
- 실제 source가 원문의 가정을 지원하지 않아 제품 범위나 metric 의미를 바꿔야 한다.

허용된 로컬 설치·격리 테스트·read-only 조사는 먼저 진행한다. 권한 문제는 실행 환경의 승인 절차를 따르고 '원래 사람만 할 수 있다'고 단정하지 않는다. 질문 대기 중에는 독립적인 조사·문서·검증을 계속할 수 있으나 미승인 계약을 적용하거나 선행 WP의 미완료를 무시하고 통합하지 않는다.

## 8. 실행 기준과 외부 결정

원문 8장의 추천은 기존 사용자 결정이었다고 주장하지 않는다. 아래 표는 이 실행본을 goal로 시작할 때 적용할 구체적인 범위이며, 사용자가 다른 값을 지정하면 변경한다. 모든 추천을 자동 승인하는 일반 규칙은 두지 않는다.

| 항목 | 이 goal의 실행 기준 | 검증/외부 구분 |
|---|---|---|
| Q1 실행 모드 | 원문대로 sync와 fully-async를 모두 모델링하고 one-step-off도 독립 경계로 처리 | CPU/synthetic·adapter 검증 필수, 실제 VERL 모드별 실행은 Pending 표 |
| Q2 Sandbox | Docker/cgroup·외부 runtime의 관측 adapter와 acquire/tool span 제공; 새 runtime/scheduler는 만들지 않음 | 기존 사용자 Docker 자원 재설정 금지, 작업 전용 fixture/자원 사용 |
| Q3 Grafana App | 원문의 optional App 원칙 유지; Phase 6은 전체 goal에 포함 | core·저장 artifact 조회는 App 없이도 동작 |
| Q4 개발 도구 | 원문이 지정한 ruff·mypy·pytest-timeout·pytest-xdist·coverage 도입 | 개발 의존성으로 제한; 최소 Python 유지; xdist는 격리 입증 후 |
| Q5 CI/원격 | workflow와 required-check 논리·DCO를 구현 | Actions 활성화·branch protection·원격 게시의 실행은 별도 요청 필요 |
| Q6 D1 계약 | 원문의 readable baseline first-seen delta 계약을 현재 main과 대조해 유지 | 이미 수정된 assertion은 재수정하지 않음; unreadable/reset/zero 경계 검증 |
| Q7 default drift | 원문대로 Python canonical default로 통일, 명시적 사용자 설정 보존 | WP-50 Behavior ADR에 변경 전후·migration 명시 |

새 rule의 원문 예시 임계값은 검증된 운영 calibration 값이 아니다. 해당 단위·source·fixture와 함께 configurable 값으로 명시하고 실환경 보정 상태를 남긴다. 측정하지 않은 임계값을 모든 cluster의 정답으로 표현하지 않는다.

## 9. 출처와 실행본 작성 근거

제공된 링크 중 `f430cce6c0d1da7cbde6db58fd7cc04b`가 중복돼 한 번만 반영됐다. 아래 순서가 원문 순서이며, 두 문서가 여러 Gist로 나뉘어 있다.

| 원문 | 출처 | 내용 |
|---|---|---|
| 1 | https://gist.github.com/daegyu94/23794dfcaaadb846ef123a16e5007877 | 진단·원칙 |
| 2 | https://gist.github.com/daegyu94/06853c9c84a5fd6211fb285d4ae3dade | 목표 아키텍처 |
| 3 | https://gist.github.com/daegyu94/036d5bc4ef1dd691b2a4de38b8620a50 | Agent RL KPI |
| 4 앞부분 | https://gist.github.com/daegyu94/d4433e0dfae1b0bd15150100160fe3ec | 하네스 |
| 4 뒷부분 | https://gist.github.com/daegyu94/224a893a1e48ab805bb2f712143f12c1 | Phase 0–2 |
| 5 앞부분 | https://gist.github.com/daegyu94/f430cce6c0d1da7cbde6db58fd7cc04b | Phase 3 |
| 5 뒷부분 | https://gist.github.com/daegyu94/2fcd3623c7cff85694106c6b7e1bdcd7 | Phase 8·실행 가이드·결정·부록 |

원본 병합본의 SHA-256은 `d499329142dd298b924fe5d5b59bd1fde8e817323db5b9519f8ff407aea8beb2`이며 수정 전 별도 파일로 보존했다. 실행본 작성일은 2026-10-13이다. Phase 4–7은 제공된 원문에 없었으므로 3–4장의 목표 설계와 실제 source 경로를 근거로 보완했으며 원문에서 복구한 텍스트가 아니다.

작성 중 현재 source에서 확인한 연결은 다음과 같다. 파일이 이동하면 symbol과 call path를 기준으로 재확인한다.

| 목표 | 현재 source 출발점 | 유지해야 할 검증 경계 |
|---|---|---|
| Pipeline | analysis/diagnostics.py, diagnosis_analysis.py, metric_observations.py, evidence_quality.py | baseline·scope·clock·sampling·partial data |
| LLM bundle | analysis/llm_diagnosis.py, llm_investigation.py | 직접 metric 분석, evidence 검증, invocation 분리 |
| Artifact | fileio.py, analysis/jsonl_cache.py, diagnostic_artifacts.py, operations/run_artifacts.py | atomicity·partial JSONL·restart·projection repair |
| Backend | prometheus.py, analysis/diagnostics.py의 ThreeFSClient, _http_transport.py, _http_redirects.py | total deadline·credential·byte limit·cleanup |
| Config/lifecycle | operations/config.py, cli.py, scripts의 세 launcher | 우선순위·argv/env/exit·PID/session ownership |
| Frontend | InvestigationView.tsx, distributed.ts, context/time-context, Scenes factories | URL·cached state·unit scale·evidence·browser recovery |
| Agent RL | adapters/verl.py, adapters/rollout.py, events.py, worker_events.py, sandbox.py, collectors | 명시적 producer·시각·entity·실측/누락 구분 |

이 문서 준비 단계의 검증은 문서 구조·WP coverage·의존 순서·source 참조·충돌 규칙 점검이다. 제품 코드·테스트·실환경 검증을 수행하거나 goal을 시작한 결과가 아니다. 실제 실행의 source SHA와 결과는 WP-00부터 새로 기록한다.

## 부록 A. 원문 baseline 근거 색인

아래 위치와 측정은 원문 기준 commit의 기록이다. 현재 test 수·실패 수·도구 설치 상태를 뜻하지 않는다.

| 주제 | 위치 |
|---|---|
| 722줄 analyze | `xlayer_telemetry/analysis/diagnostics.py:567-1288` |
| 기본 query와 threshold | `analysis/diagnostics.py:54-90` |
| Retry가 no-data와 실패를 구분 못함 | `analysis/diagnostics.py:1373` |
| Strength 강등 분산 | diagnostics 9, rollout_state 8, rollout_replicas 3, diagnosis_analysis 2, robust_differential 2, trajectory_path 2, storage_series 1 |
| LLM 중복 collection | `analysis/llm_diagnosis.py:276-401` |
| Identity env 해석 2벌 | `events.py:52-82`, `metrics/emitter.py:98-139` |
| SDK가 collector 적재 | `sandbox.py:13` |
| Analysis가 dashboard JSON에서 PromQL 차용 | `analysis/canonical_queries.py:22` |
| App signal 이름 불일치 (D2) | `grafana/xlayer-app/src/InvestigationView.tsx:842` vs `analysis/metric_queries.py:101` |
| 생성기 순서 의존 panel ID | `scripts/dashboard_views.py:234-242`, `src/scene-specs.ts:50-52` |
| URL state 분산 | `InvestigationView.tsx:78,808,889`, `PhaseMatrix.tsx:18,21,30,36,175`, `app-scene.ts:81,116`, `module.tsx:37-54` |
| 표시 문장에 걸린 로직 | `src/ui-copy.ts:21-99`, `src/PhaseMatrix.tsx:50` |
| Shell/Python 기본값 drift | `operations/config.py:86-102` vs `scripts/run_telemetry.sh:104,108`, `scripts/validate_stack.sh:12-13` |
| PID 파싱 4벌 | `verl_local.sh:76-84`, `local_llm.sh`, `operations/health.py:40`, `run_verl_with_telemetry.sh:248-262` |
| Red HEAD (D1) | `tests/test_operational_regressions.py:95`, 커밋 `6ddce33` |
| Wrapper poll 비용 (D4) | `scripts/run_verl_with_telemetry.sh:346` 부근 |
| Fix-on-fix 사례 | `packet_from_report`(`e16f16d`→`87df51f`→`8b77fa6`→`eda42d1`→`8172bc2`), `resourceContext`(`a4c5d7f`→`320a51b`→`e1f3c79`→`8eee527`), browser wait(`bdc4ede`→`18e8741`→`1397c93`→`9fdd16a`) |

## 부록 B. 원문 baseline 측정 방법

- 전체 suite: `env -u HTTP_PROXY -u HTTPS_PROXY python -m pytest -q -p no:cacheprovider --durations=30 -rs`. 이 서버에서 약 7분, 4개 실패(D1, D4 2건, D5).
- Proxy 영향 확인: 같은 명령을 proxy 변수를 둔 채 실행하면 122개 실패.
- Metric coverage: `python scripts/metric_coverage.py --json /tmp/cov.json`.
- Import graph와 함수 크기는 AST 스크립트로 측정했다(`ast.walk`로 `Import`/`ImportFrom`과 `FunctionDef.end_lineno - lineno` 집계). 같은 방식으로 다시 측정할 수 있다.
