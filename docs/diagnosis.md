# Baseline, Candidate, Evidence

**목표:** 같은 실행 조건의 baseline과 current를 비교하고 판정의 근거·누락·한계를 읽습니다.

## 준비 조건

- Run artifact와 step history.
- 필요한 Prometheus·선택적 3FS source.
- Grafana projection을 보려면 Loki; 저장된 JSON 확인에는 Loki가 필요 없습니다.

## 1. 진단 연결

[Diagnostics 예제](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/verl/diagnostics.json)를 개인 config 경로에 복사합니다. Endpoint·cluster·node를 실제 배포로 바꾸고 사용하지 않는 `threefs`는 제거합니다.

기존 TOML `[telemetry]`:

```toml
DIAGNOSTICS_CONFIG = "~/telemetry/config/diagnostics.json"
```

```bash
xltel config validate
xltel run -- /path/to/verl-env/bin/python -m verl.trainer.main_ppo ...
xltel inspect RUN_ID
```

**정상 결과:** 새 Run의 `diagnostics/latest.json`에 verdict·comparison·candidate·missing 정보가 기록됩니다. Workload exit code와 telemetry 실패는 별도로 확인합니다.

```{admonition} Optional source
:class: note

3FS 연결은 기존 ClickHouse의 database·metricName·filter·credential 설정이 필요합니다. Native endpoint 등록만으로 3FS service p99가 생기지 않습니다.
```

## 2. Comparable baseline 확인

| 조건 | 확인 |
| --- | --- |
| 기본 선택 | 같은 Run·node·worker·boundary scope의 최근 이전 유효 step 5개에서 duration median에 가까운 interval |
| Workload | Model·batch·sequence length·concurrency·cache 상태·topology 비교 가능성 |
| Entity | 같은 GPU device / vLLM engine / 3FS metricName |
| Time / sample | Clock·source age·query window·sampling quality |
| Missing baseline | 값을 만들지 않으며 strong 판정 조건을 제한 |

**정상 결과:** Current와 Baseline의 단위·entity·scope가 비교 가능합니다. [What changed?](dashboards.md#3-what-changed--candidate--evidence)의 Signal 링크로 두 interval을 따로 엽니다.

## 3. Candidate와 Verdict 읽기

| 표시 | 의미 | 다음 행동 |
| --- | --- | --- |
| `weak_signal` | 주요 증상만 관측 | 나머지 조건·missing 확인 |
| `supporting_signal` | 복수 근거가 있지만 필수 조건 누락 또는 반대 근거 | Source·entity·counter evidence 확인 |
| `strong_signal` | Rule의 필수 측정 조건 충족 | Attribution·반증·실제 path 확인 |
| `bottleneck_suspected` | 후보 발견 | Evidence → Timeline → subsystem |
| `no_anomaly_observed` | 조회한 데이터·기준에서 후보 없음 | Source coverage·missing 확인 |
| `insufficient_data` | 판단 근거 부족 | Missing·clock·sample 보완 |

```{admonition} Correlation ≠ causality
:class: important

수치 confidence를 계산하지 않습니다. Strong signal도 시간적 상관이며 Run의 공유 사용량이나 확정 원인이 아닙니다.
```

## 4. Supporting / Counter / Missing evidence

| Evidence | 읽을 field | 질문 |
| --- | --- | --- |
| Supporting | Current/baseline·source·scope·entity | 후보를 어떤 실제 관측이 지지하는가? |
| Counter | 반대 변화·headroom·조건 불일치 | 후보를 반증하는 근거가 있는가? |
| Missing | Missing source·unknown unit·attribution gap | 무엇을 아직 측정하지 못했는가? |
| Quality | Boundary accuracy·clock uncertainty·query step·rate window | 이 비교의 정밀도는 충분한가? |

**정상 결과:** 모든 값이 원본 query·scope와 연결됩니다. 단위가 없으면 Unknown으로 남고 missing을 measured zero로 대체하지 않습니다.

## 5. 결과 확인과 다음 조사

```bash
xltel inspect RUN_ID
```

| Artifact / UI | 역할 |
| --- | --- |
| `diagnostics/latest.json` | 완전한 최신 diagnosis·sample metadata |
| `diagnostics/diagnostics.jsonl` | 같은 record의 revision 이력 |
| `diagnostics/investigation/*.jsonl` | Loki가 읽는 final projection |
| Bottleneck Summary | Selected record의 What changed / candidate / evidence |
| Timeline / profiler | 같은 구간의 추가 확인·반증 |

## 결과가 비어 있거나 늦으면

| 상태 | 확인 |
| --- | --- |
| `provisional` | Settle/retry·missing source·baseline; 장애라고 단정하지 않음 |
| 후보 표만 없음 | `latest.json`의 final 상태·projection·Loki |
| Source query 실패 | `query_execution`·deadline·response limit |
| Storage strong evidence 없음 | 실제 storage node/device·network capacity·같은 3FS metricName |
| Clock unsafe/unknown | Resource delta 비교 보류; clock과 missing 읽기 |

## 다음

[Slow Step Investigation](dashboards.md) · [Deep Dive](deep-dive.md) · [Rule·query budget·revision 상세](diagnosis-reference.md) · [Optional Local LLM](local-llm.md)

:::{container} xlayer-legacy-links

<a id="cross-layer-diagnosis" class="xlayer-legacy-anchor"></a>

[Cross-Layer Diagnosis](diagnosis-reference.md#cross-layer-diagnosis)

<a id="investigation-workflow" class="xlayer-legacy-anchor"></a>

[Investigation Workflow](dashboards.md)

<a id="practice-with-a-synthetic-candidate" class="xlayer-legacy-anchor"></a>

[Practice with a Synthetic Candidate](demo.md)

<a id="what-the-signals-mean" class="xlayer-legacy-anchor"></a>

[What the Signals Mean](concepts.md)

<a id="semantic-model-and-adapter-boundary" class="xlayer-legacy-anchor"></a>

[Semantic Model and Adapter Boundary](diagnosis-reference.md#semantic-model-and-adapter-boundary)

<a id="optional-exporter-metric-profiles" class="xlayer-legacy-anchor"></a>

[Optional Exporter Metric Profiles](diagnosis-reference.md#optional-exporter-metric-profiles)

<a id="source-contracts-and-remaining-gaps" class="xlayer-legacy-anchor"></a>

[Source contracts and remaining gaps](diagnosis-reference.md#source-contracts-and-remaining-gaps)

<a id="backend-query-budget" class="xlayer-legacy-anchor"></a>

[Backend Query Budget](diagnosis-reference.md#backend-query-budget)

<a id="baseline-and-rule-state" class="xlayer-legacy-anchor"></a>

[Baseline and Rule State](diagnosis-reference.md#baseline-and-rule-state)

<a id="select-a-comparable-workload" class="xlayer-legacy-anchor"></a>

[Select a Comparable Workload](diagnosis-reference.md#select-a-comparable-workload)

<a id="read-sampling-quality" class="xlayer-legacy-anchor"></a>

[Read Sampling Quality](diagnosis-reference.md#read-sampling-quality)

<a id="clock-and-node-selection" class="xlayer-legacy-anchor"></a>

[Clock and Node Selection](diagnosis-reference.md#clock-and-node-selection)

<a id="data-and-ui-boundaries" class="xlayer-legacy-anchor"></a>

[Data and UI Boundaries](diagnosis-reference.md#data-and-ui-boundaries)

<a id="follow-the-data-path" class="xlayer-legacy-anchor"></a>

[Follow the Data Path](diagnosis-reference.md#follow-the-data-path)

<a id="targeted-deep-dive" class="xlayer-legacy-anchor"></a>

[Targeted Deep Dive](diagnosis-reference.md#targeted-deep-dive)

<a id="reproduce-the-integration-checks" class="xlayer-legacy-anchor"></a>

[Reproduce the Integration Checks](diagnosis-reference.md#reproduce-the-integration-checks)

:::
