# Local LLM 연결 · 선택한 관측값 분석

:::{container} xlayer-page-meta
**Task · Experimental** 이미 수집한 metric packet을 명시적으로 분석합니다. 기본 rule diagnosis를 대체하지 않습니다.
:::

## 얻는 것

- Current / baseline과 entity·scope를 읽은 모델의 조사 가설.
- Supporting / counter / missing evidence ID와 별도 semantic review.
- 입력 packet·모델 digest·prompt version·검토 결과 artifact.

```{admonition} 해석 경계
:class: important

검토를 통과해도 설명의 사실성·causality를 보증하지 않습니다. 모델은 tool·명령을 실행하거나 alert를 변경하지 않습니다. Source가 부족하면 먼저 [rule diagnosis와 coverage](diagnosis.md)를 확인합니다.
```

## 준비 조건

| 조건 | 확인 |
| --- | --- |
| XLayer CLI / checkout | [설치](quickstart.md) 완료 |
| 분석할 관측값 | Prometheus source 또는 저장된 Run의 metric packet |
| 별도 Ollama endpoint / 모델 | [설치 Reference](local-llm-reference.md#install-and-start-the-optional-model) |
| 자원 분리 | 학습 종료 후 분석하거나 별도 진단 GPU 사용; inference도 resource metric에 영향 |

`xltel up`은 Ollama를 시작하지 않습니다. 모델 다운로드·VRAM·모델 선택은 [기존 도구 설치 절차](local-llm-reference.md#install-and-start-the-optional-model)에서 확인합니다.

## 1. Configure

`examples/local-llm/prometheus.json`을 복사하고 실제 endpoint·node/device·query의 unit·scope를 지정합니다.

| 설정 | 선택 기준 |
| --- | --- |
| `current_interval` | 조사할 실제 Step/Update 구간 |
| `baseline_interval` | 비교 가능한 구간; 없으면 `null` |
| `context` | 실제 Run / Step / phase 정보 |
| `topology` | 확인한 구성만 제공. Operation path로 추정하지 않기 |

설정 형식과 rolling query 해석은 [입력 계약](local-llm-reference.md#diagnose-collected-metrics-directly)을 따릅니다.

## 2. Collect / Verify

먼저 모델 호출 없이 입력을 확인합니다.

```bash
python -m xlayer_telemetry.analysis.llm_diagnosis \
  --source-config examples/local-llm/prometheus.json \
  --collect-only --output /path/to/new/observations.json
```

**정상 결과:** observation packet 파일 생성. 다음 항목이 맞아야 분석을 진행합니다.

- [ ] Current / baseline의 실제 시간 구간과 accuracy.
- [ ] 같은 entity를 비교했는지, metric의 unit·scope가 있는지.
- [ ] Missing source·backend error·sampling/clock quality가 표시되는지.

## 3. Analyze

```bash
python -m xlayer_telemetry.analysis.llm_diagnosis \
  --input /path/to/new/observations.json \
  --output /path/to/new/llm-diagnosis.json
```

**정상 결과:** 결과 파일에 원본 observation과 모델/호출 metadata가 보존됩니다. 기본 endpoint/model을 바꿨다면 `--endpoint` / `--model`을 같은 설정으로 지정합니다.

| Field | 확인할 것 |
| --- | --- |
| `diagnosis` | 검토된 결과 |
| `draft_diagnosis` | 최종 판정과 구분할 초안 |
| `semantic_review` | 검토 사유·evidence 참조·호출 metadata |
| `missing_sources` / observation | 누락·query 오류·비교 가능성 |

검토가 거부되거나 실패한 결과를 최종 diagnosis로 취급하지 않습니다.

## 문제가 생겼다면

| 상태 | 다음 행동 |
| --- | --- |
| Endpoint / model 오류 | Ollama 상태·모델 이름·port → [설치 Reference](local-llm-reference.md#install-and-start-the-optional-model) |
| Query timeout / source 없음 | Endpoint·filter·query budget → [failure 계약](local-llm-reference.md#limits-and-failure-handling) |
| Review에서 거부 | 입력 ID·scope·clock·entity 혼합을 확인. 검토를 우회하지 않기 |
| 공유 resource 원인 단정 | [causal claim 검토](local-llm-reference.md#how-causal-claims-are-reviewed)에서 attribution 조건 확인 |

## 다음

[Grafana에서 선택한 Step 분석](local-llm-reference.md#diagnose-a-selected-grafana-step) · [입력/검토/운영 Reference](local-llm-reference.md) · [Evidence로 후보 검증](deep-dive.md)

:::{container} xlayer-legacy-links

<a id="install-and-start-the-optional-model" class="xlayer-legacy-anchor"></a>

[Install and Start the Optional Model](local-llm-reference.md#install-and-start-the-optional-model)

<a id="diagnose-collected-metrics-directly" class="xlayer-legacy-anchor"></a>

[Diagnose Collected Metrics Directly](local-llm-reference.md#diagnose-collected-metrics-directly)

<a id="how-metrics-reach-ollama" class="xlayer-legacy-anchor"></a>

[How Metrics Reach Ollama](local-llm-reference.md#how-metrics-reach-ollama)

<a id="reuse-an-existing-run" class="xlayer-legacy-anchor"></a>

[Reuse an Existing Run](local-llm-reference.md#reuse-an-existing-run)

<a id="diagnose-a-selected-grafana-step" class="xlayer-legacy-anchor"></a>

[Diagnose a Selected Grafana Step](local-llm-reference.md#diagnose-a-selected-grafana-step)

<a id="exercise-bottlenecks-and-negative-cases" class="xlayer-legacy-anchor"></a>

[Exercise Bottlenecks and Negative Cases](local-llm-reference.md#exercise-bottlenecks-and-negative-cases)

<a id="how-causal-claims-are-reviewed" class="xlayer-legacy-anchor"></a>

[How Causal Claims Are Reviewed](local-llm-reference.md#how-causal-claims-are-reviewed)

<a id="limits-and-failure-handling" class="xlayer-legacy-anchor"></a>

[Limits and Failure Handling](local-llm-reference.md#limits-and-failure-handling)

<a id="multi-node-clock-evidence" class="xlayer-legacy-anchor"></a>

[Multi-node Clock Evidence](local-llm-reference.md#multi-node-clock-evidence)

<a id="recorded-validation" class="xlayer-legacy-anchor"></a>

[Recorded Validation](local-llm-reference.md#recorded-validation)

:::
