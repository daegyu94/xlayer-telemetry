# ADR-0005: Rule과 LLM의 공통 evidence assessment

- 상태: 승인
- 적용 단계: R-03

## 결정

Rule, standalone LLM 수집, saved-report LLM은 같은 `EvidenceBundle` decoder로 entity·time window·관측 상태를 해석한다.
수집 transport와 query budget은 유지하고, 선택한 관측의 sampling assessment·comparison 제한·candidate cap을 pure stage로 적용한다.
유효한 zero와 stale raw value는 artifact에 남기지만 stale current value는 Rule 판정이나 LLM hypothesis의 supporting/counter evidence로 사용할 수 없다.

Partial sampling과 withheld comparison을 인용하는 LLM hypothesis는 모델 입력의 `evidence_limits`에 제시한 해당 issue code를 `missing_evidence`에 보존해야 한다.
Code는 관측 ID와 같은 entity에서 계산하며 다른 engine·GPU·worker의 quality를 빌려오지 않는다.
Model input에서 확인할 수 없는 제한을 모델이 만들어내도록 요구하지 않는다.

Rule의 `strong_signal`은 sampling·workload·producer 제한에 따라 낮아질 수 있다.
LLM은 Rule strength나 confidence를 부여받지 않으며 accepted hypothesis로 남는다.
Clock이 unsafe 또는 unknown이면 두 경로 모두 temporal diagnosis를 허용하지 않고 raw resource context는 보존한다.

## 이유

기존 Rule은 stale signal을 제거하고 sampling 경고로 strong signal을 낮췄지만 LLM validator는 수치 존재만 확인했다.
같은 관측이 Rule에서는 제한된 evidence이고 LLM에서는 제한 없는 hypothesis의 근거가 될 수 있었다.
공통 typed assessment를 실제 판정과 projection에 연결해 이 차이를 해소한다.

## 호환성

기존 report·observation packet의 schema version과 measurement allowlist를 유지한다.
Saved report에 없는 raw series·unit·statistic·clock 측정은 재구성하지 않는다.
명시된 current `observation_status`는 packet에 보존하며 baseline 상태는 baseline 값과 sampling quality에서 독립적으로 해석한다.
Stale baseline은 현재값만 사용하는 hypothesis를 막지 않지만 해당 baseline 제한 code는 missing evidence로 남겨야 한다.
Investigation row의 `observation_status`는 additive field이고 current/baseline의 scalar 또는 stats object를 그대로 보존한다.
Standalone LLM의 custom query, 모델 evidence reference 검증, input/output budget과 saved-report Rule 결과 차단은 유지한다.

## 검증

`tests/test_diagnosis_pipeline.py`에서 모든 현재 Rule ID의 관측 scenario·unsafe clock·빈 입력, stale/partial 근거 제한, entity별 quality, comparison withholding, cap 순서와 idempotence를 확인한다.
기존 public-entrypoint·baseline·clock·sampling·saved-reader·projection regression과 strict mypy를 함께 실행한다.
전체 CPU suite와 최종 integration 검증의 실행 결과는 재설계 진행 표에 기록한다.
