# Optional Local LLM Diagnosis

수집한 메트릭과 실행 문맥을 local open-weight 모델에 전달해 병목을 직접 진단할 수 있습니다.
모델은 current/baseline 관측값, entity label, observation scope를 읽고 스스로 bottleneck hypothesis를 만듭니다.
Rule catalog, threshold, 사전 정의된 후보명이나 기존 rule 판정은 모델 입력에 포함하지 않습니다.
이 기능은 명시적으로 실행하는 experimental 선택 기능이며 기존 rule diagnosis와 독립적으로 동작합니다.
진단 요약·설명·missing evidence·다음 확인 항목·관측 한계와 검토 사유는 한국어를 기본으로 작성합니다.
GPU utilization, KV cache, rollout, cgroup, I/O pressure 같은 technical term과 metric 이름·ID·unit은 영어로 유지하며 어색한 번역은 요구하지 않습니다.
후보 제목은 `Possible storage device saturation`처럼 English technical noun phrase를 사용합니다.
특히 `attribution`은 특정 workload에 관측된 사용량을 연결하는 의미이므로 `속성`·`속성화`로 번역하지 않습니다.
같은 설명을 한국어와 영어 문장으로 중복 나열하지 않고, 필요한 term만 영어로 둡니다.

```text
Prometheus series + selected intervals + workload context
  |
  +--> Scoped observation packet
         |
         +--> Ollama + local model on one GPU
                |
                +--> Draft hypotheses
                       |
                       +--> Evidence review in a fresh model context
                              |
                              +--> Accepted or revised LLM diagnosis JSON
```

현재 연결 방식은 CLI에서 선택한 구간을 분석하는 것입니다.
모델이 진단을 생성하고 XLayer가 응답 형식, evidence 참조와 candidate의 관측 구간 존재 여부를 검사합니다.
제목·설명에 명시한 observation ID도 evidence 목록에 있는지 검사하고, 요약·제목·설명의 존재하지 않는 `m<number>` ID를 거부합니다.
인용된 observation은 현재 구간의 실제 값이 있어야 하며, 같은 ID를 supporting evidence와 counter evidence 양쪽에 넣을 수 없습니다.
서로 다른 engine·worker·rank의 별개 signal을 한 candidate의 근거로 합치는 응답도 거부합니다.
Entity 비교에서는 label에 있는 cluster·node를 함께 사용하고 worker·rank에는 role·producer도 포함하므로, 이름이 같아도 node나 cluster가 다른 대상을 합치지 않습니다.
이 검사는 label로 확인할 수 있는 일부 혼합을 막는 것이며, label이 생략된 entity의 동일성이나 shared-resource 관계를 증명하지 않습니다.
[Ollama structured output](https://docs.ollama.com/capabilities/structured-outputs)의 JSON schema를 API와 prompt 양쪽에 전달합니다.
형식 검사가 통과해도 설명의 사실성이나 인과관계가 증명되는 것은 아닙니다.
초안이 형식 검사를 통과하면 같은 모델을 새 대화 문맥으로 호출해 관측값과 초안을 대조합니다.
검토는 근거 없는 사실·원인 단정을 수정하거나 결과를 거부하며, 실패한 검토의 초안을 최종 결과로 채택하지 않습니다.
자동 step 호출, Grafana 표시, alert 변경과 명령 실행은 포함하지 않습니다.

실행 backend는 Ollama를 선택했습니다.
한 monitoring host에서 모델 다운로드·GPU 적재·structured JSON API를 관리하기 쉽고, XLayer는 HTTP 요청만으로 연결할 수 있습니다.
현재 workflow에 필요한 권한은 전달받은 관측값을 분석하는 것뿐이므로 agent의 tool 실행 기능은 사용하지 않습니다.

## Install and Start the Optional Model

[Checkout 준비](../README.md#prepare-a-checkout)를 마친 환경에서 저장소 루트로 이동합니다.
Helper는 고정된 Ollama release를 `$HOME/telemetry/tools`에 설치하고 공식 release checksum을 대조합니다.
모델 weight는 `$HOME/telemetry/models/ollama`에 저장하며 system service나 VERL Python 환경을 변경하지 않습니다.
Linux, `curl`, `tar`, `zstd`, `sha256sum`, `setsid`, `flock`과 GPU에 맞는 NVIDIA driver가 필요합니다.

```bash
mkdir -p "$HOME/telemetry/config"
cp -n examples/local-llm.conf "$HOME/telemetry/config/local-llm.conf"
```

복사한 파일의 `LLM_GPU`를 진단용 GPU index 또는 UUID로 지정합니다.
`nvidia-smi -L`에서 UUID를 확인할 수 있습니다.
기본 모델 `qwen3.5:27b`는 약 17GB를 다운로드하는 Q4_K_M 모델이며 Apache 2.0 license를 사용합니다.
Weight 크기 외에 context와 실행 buffer도 VRAM을 사용하므로 실제 적재 결과를 확인해야 합니다.
기본 설정은 32K context, 동시 요청 한 개, thinking을 포함한 출력 최대 16,384 token입니다.
모델의 일반 thinking 권장 sampling 값을 사용하되, context와 출력 한도는 이 PoC에 맞게 제한합니다.
이는 긴 context를 사용한 공식 benchmark 조건과 같지 않습니다.
[모델 배포 정보](https://ollama.com/library/qwen3.5:27b), [모델 실행 권장값](https://huggingface.co/Qwen/Qwen3.5-27B), [GPU 지원](https://docs.ollama.com/gpu)을 참고합니다.

```bash
bash scripts/local_llm.sh --config "$HOME/telemetry/config/local-llm.conf" install
bash scripts/local_llm.sh --config "$HOME/telemetry/config/local-llm.conf" up
bash scripts/local_llm.sh --config "$HOME/telemetry/config/local-llm.conf" pull
```

`up`은 `http://127.0.0.1:11434`에 별도 inference server를 시작하고 `$HOME/telemetry/state/local-llm/`에 PID와 log를 보관합니다.
CUDA는 지정 GPU만 보도록 제한하고, Vulkan을 통한 다른 GPU 탐색과 Ollama cloud inference는 비활성화합니다.
`TELEMETRY_HOME`으로 설치·모델·상태의 공통 경로를 바꿀 수 있으며 이후에도 같은 config를 사용합니다.
`LLM_PORT`를 바꿨다면 진단·평가 명령의 `--endpoint`에도 해당 주소를 전달합니다.
`LLM_PORT`는 1–65535 범위이며, 같은 상태 directory의 install/up/down은 동시에 실행되지 않습니다.
`LLM_MODEL`을 바꿨다면 진단·평가 명령에도 `--model`로 같은 모델을 지정합니다.

```bash
bash scripts/local_llm.sh --config "$HOME/telemetry/config/local-llm.conf" status
bash scripts/local_llm.sh --config "$HOME/telemetry/config/local-llm.conf" down
```

Inference를 실행한 뒤 `status`에서 모델이 실제로 GPU에 적재됐는지 확인합니다.
모델은 5분 동안 사용하지 않으면 내려가며 server는 `down`까지 유지됩니다.
같은 GPU에서 학습한다면 저장된 결과를 학습 종료 후 분석하거나 진단 GPU를 분리합니다.
진단 inference도 GPU·CPU·memory 관측값에 영향을 주기 때문입니다.
일반 `verl_local.sh up`은 이 서비스를 시작하지 않습니다.

## Diagnose Collected Metrics Directly

[Prometheus source 예제](../examples/local-llm/prometheus.json)를 복사하고 endpoint, node label과 device 이름을 실제 배포에 맞춥니다.
조사할 application·vLLM·network·storage query를 추가하면서 각 query의 unit과 scope를 명시합니다.
이 설정은 무엇을 측정하는지 설명하며 병목 판정 조건을 정의하지 않습니다.
Host metric만 있는 최소 예제로는 workload 정보가 필요한 학습 병목을 충분히 설명할 수 없습니다.

```bash
python -m xlayer_telemetry.llm_diagnosis \
  --source-config examples/local-llm/prometheus.json \
  --output "$HOME/telemetry/llm-diagnosis.json"
```

`lookback_seconds=60`이면 최근 1분을 current, 바로 전 1분을 baseline으로 사용합니다.
Baseline을 사용하지 않으려면 `baseline_interval: null`을 지정합니다.
명시한 baseline은 current 구간을 자동 생성해도 유지됩니다.
이전 구간이 정상 상태이거나 같은 양의 작업을 수행했다고 자동으로 가정하지 않습니다.
특정 slow step을 조사할 때에는 source config에 Unix `start`, `end`, `accuracy`를 가진 `current_interval`과 `baseline_interval`을 직접 지정하고 `context`에 run·step·phase 정보를 넣습니다.
확인된 data path는 `topology`로 전달할 수 있습니다.

Collector는 series의 전체 label을 보존하고 label이 같은 경우에만 baseline을 연결합니다.
입력은 raw timeline이 아닌 query 결과의 요약 통계이며, sample count는 원본 scrape 수가 아닌 query 평가 표본 수입니다.
`[1m]` PromQL은 선택 구간 밖의 표본을 포함할 수 있습니다.
`kind: "counter"`는 reset을 고려한 표본 간 증가량을 추가하며 Prometheus의 extrapolation을 적용하지 않습니다.
Source 누락, baseline 부재, query 실패는 명시적으로 남기고 rule evaluator는 호출하지 않습니다.

모델이 읽을 입력을 먼저 확인하고 보존하려면 `--collect-only`를 사용합니다.

```bash
python -m xlayer_telemetry.llm_diagnosis \
  --source-config examples/local-llm/prometheus.json \
  --collect-only --output "$HOME/telemetry/observations.json"
python -m xlayer_telemetry.llm_diagnosis \
  --input "$HOME/telemetry/observations.json" \
  --output "$HOME/telemetry/llm-diagnosis.json"
```

결과에는 진단과 함께 model digest, Ollama version, inference 설정, input hash, prompt version이 기록됩니다.
`diagnosis`는 검토를 마친 결과이고 `draft_diagnosis`는 감사용 초안입니다.
`semantic_review`에는 `accept`·`revise` 판정, 수정 사유, 검토 prompt version·seed·input hash·token·latency가 기록됩니다.
`requested_language: "ko"`는 기본 출력 언어를 나타내며 schema key와 assessment 값은 기존 식별자를 사용합니다.
원본 observation packet도 결과 파일에 포함하므로 evidence ID의 실제 수치와 scope를 확인할 수 있습니다.
`input_sha256`은 원본 packet의 hash이며 `model_input_sha256`과 `model_input_bytes`는 Ollama에 전달한 observation payload를 식별합니다.
`prompt_tokens`는 system prompt와 schema를 포함한 전체 입력의 token 수이므로 payload 크기와 다릅니다.
최상위 token 수는 첫 생성 호출의 값이며 검토 호출의 token 수는 `semantic_review`에서 별도로 확인합니다.
최상위 `latency_seconds`는 생성과 검토를 합한 시간이므로 이전 한 번의 호출과 같은 조건의 속도 비교가 아닙니다.
각 candidate는 자유롭게 생성한 제목·설명, evidence ID, 반대 근거, missing evidence, observation scope와 다음 확인 항목을 포함합니다.
고정된 candidate catalog나 numeric confidence는 사용하지 않습니다.
원래 observation과 기존 rule 결과는 덮어쓰지 않습니다.

## How Metrics Reach Ollama

XLayer는 원본 observation packet을 저장하고, 모델 호출 시 반복되는 JSON 필드를 표 형태로 정리합니다.
`observation_columns`가 각 열의 의미를 지정하고, `observation_rows`의 같은 위치에 값이 들어갑니다.
모든 series가 공유하는 `source`·`unit`·scope 등은 `common_observation_fields`에 한 번만 넣습니다.
Metric 값, baseline, label, observation scope, sample 통계와 서로 다른 PromQL query는 유지합니다.
안정된 metric이나 반대 근거가 될 수 있는 series를 자동으로 제거하거나 값의 해상도를 낮추지 않습니다.
`current`와 `baseline`은 유한한 숫자, `null`, 또는 `min`·`mean`·`max`·`last`·`sample_count`·`sampled_increase`·`max_series_delta`로 구성한 numeric statistics를 받습니다.
빈 statistics, 값이 없는 statistics, `sample_count=0`은 측정값 부재로 취급하며 숫자 `0`은 유효한 관측값입니다.
숫자 문자열, boolean, `NaN`·무한대와 observation 내부의 임의 필드는 입력 오류로 처리합니다.

```json
{
  "representation": "observation_table_v1",
  "common_observation_fields": {"source": "prometheus"},
  "observation_columns": ["id", "signal", "unit", "observation_scope", "labels", "baseline", "current"],
  "observation_rows": [
    ["m1", "step_duration_seconds", "seconds", "application", {"node": "gpu-0"}, 10, 20],
    ["m2", "gpu_utilization_percent", "percent", "device", {"node": "gpu-0", "gpu": "0"}, 90, 45]
  ]
}
```

실제 입력에는 이 표와 함께 current/baseline interval, run 문맥, topology, missing source와 관측 한계도 포함됩니다.
모델 입력의 최상위 필드는 이 관측 문맥으로 제한하므로 임의로 추가된 rule 판정이나 verdict는 전달하지 않습니다.
Ollama에 전달하는 observation payload는 최대 8KiB이고 원본 packet은 최대 32KiB, 64 series까지 허용합니다.
범위를 넘으면 잘라서 전달하지 않고 query selector를 좁히도록 오류를 반환합니다.
검토 입력은 같은 관측 표에 초안을 추가하며 최대 16KiB로 제한합니다.
검토 입력이 한도를 넘으면 검토하지 않은 진단을 저장하는 대신 오류로 처리합니다.
Ollama 응답의 `prompt_tokens`와 `generated_tokens`를 구분해 확인할 수 있습니다.
이전 검증에서 과도하게 추론한 증거 부족 사례는 약 997 prompt token이었으므로 입력 길이만으로 그 오류를 설명할 수 없습니다.

## Reuse an Existing Run

저장된 `diagnostics/latest.json`도 측정값의 입력으로 활용할 수 있습니다.
Adapter는 `comparison.signals`와 필요한 문맥만 선택하고 `verdict`, `findings`, rule `candidates`, threshold를 제외합니다.
다만 저장된 comparison은 이미 series를 집계하거나 특정 entity를 선택했을 수 있으므로 모든 engine/device 구분이 중요하면 직접 수집 경로를 사용합니다.
시간 구간이 없거나 `unknown`이면 step과 자원의 시간적 상관을 확정할 수 없습니다.
이 경우 모델이 생성한 candidate는 거부하고, 후보가 없는 관측 요약·증거 부족 응답만 조사 결과로 활용합니다.

```bash
python -m xlayer_telemetry.llm_diagnosis \
  --input "$RUN_ROOT/diagnostics/latest.json" \
  --output "$RUN_ROOT/diagnostics/llm.json"
```

### Diagnose a Selected Grafana Step

Bottleneck Summary의 `Step record ID`를 복사하고 run 파일에 접근할 수 있는 host에서 명시적으로 실행합니다.
Ollama가 다른 monitoring host에 있다면 `--endpoint`로 주소를 지정합니다.

```bash
python -m xlayer_telemetry.llm_diagnosis \
  --run-root "$RUN_ROOT" --record-id RECORD_ID
```

선택한 step의 최신 final revision에서 관측값·baseline·sampling quality를 가져오고, rule candidate·threshold는 모델에 전달하지 않습니다.
기본 output은 run의 `diagnostics/llm-*.json`이며 `--output`으로 변경할 수 있습니다.
성공한 검토 결과만 기존 Alloy 경로인 `diagnostics/investigation/llm-*.jsonl`에 projection합니다.
Draft, rejected response와 실패는 Grafana candidate로 내보내지 않습니다.

Bottleneck Summary에서 `Method=llm`을 선택하면 model·생성 시각·hypothesis·evidence ID를 확인할 수 있습니다.
Rule 결과는 `Method=rule`로 계속 확인하고, LLM hypothesis에 rule의 `strong_signal`이나 numeric confidence를 부여하지 않습니다.
Alloy가 해당 run root를 읽고 있어야 Grafana에 표시되며 오래된 interval은 Loki retention과 과거 event 수용 설정의 영향을 받습니다.
CLI와 dashboard는 모델을 자동 호출하지 않습니다.
`--collect-only`는 선택한 step의 입력만 저장하고 projection하지 않습니다.

## Exercise Bottlenecks and Negative Cases

평가 명령은 synthetic 메트릭을 만들고 실제 로컬 모델을 호출한 뒤 각 입력과 출력을 저장합니다.
정상 구간, storage device pressure, network-limited storage, rollout queue, KV pressure, host memory pressure, communication, sandbox I/O, straggler, 부족한 증거, 서로 다른 engine, 불명확한 timestamp, workload 크기 증가를 다룹니다.
Case 이름과 기대 결과는 모델에 전달하지 않습니다.

```bash
PYTHONPATH=. python examples/local-llm/evaluate.py \
  --output "$HOME/telemetry/llm-evaluation"
```

`--generate-only`는 모델 없이 입력만 만들고, `--case sandbox_io`는 해당 사례만 선택하며, `--repeats 3`은 seed를 바꿔 반복합니다.
`summary.json`은 응답 형식, 예상 assessment와의 일치, primary candidate의 evidence 참조를 검사합니다.
예상 assessment 불일치, 필수 evidence 누락, 모델 호출·응답 검증 실패 중 하나라도 있으면 평가 명령은 종료 코드 `1`을 반환합니다.
증거 부족 사례의 기대 판정은 `insufficient_evidence`이며, 기대 판정이 없는 사례는 `assessment_match: null`로 남아 수동 검토가 필요합니다.
재실행은 선택한 사례·반복의 기존 결과를 정리하고 새 결과를 기록하며, 현재 실행의 평가 범위는 `summary.json`으로 확인합니다.
Evidence coverage는 counter evidence도 포함하며 그 수치를 올바르게 해석했는지까지 증명하지는 않습니다.
설명과 다음 조사 항목을 직접 검토하고 특히 다른 engine·누락 timestamp·shared resource·workload 크기 변경을 잘 구분하는지 확인합니다.
Synthetic 병목은 `data_origin=synthetic`인 가상 관측값이며 실제 storage·GPU에 부하를 주입한 결과는 아닙니다.

## How Causal Claims Are Reviewed

관측된 변화와 원인 가설은 서로 다른 종류의 설명입니다.
Step duration 증가와 storage busy 증가가 동시에 측정돼도 storage가 step을 늦췄다는 원인 증명은 아닙니다.
또한 낮은 GPU utilization은 GPU stall time·idle time·dependency wait의 직접 측정값이 아닙니다.

검토 호출은 observation packet과 생성된 초안을 읽고 수치·entity 소속·scope·evidence 참조·원인 단정 여부를 점검합니다.
같은 local model을 별도 대화 문맥에서 사용하며 tool이나 외부 서비스에는 접근하지 않습니다.
초안이 근거 자료로 취급되지 않도록 관측값과 구분해 전달합니다.

| 초안의 문제 표현 | 필요한 수정 |
| --- | --- |
| `Storage I/O contention delaying training` | `Possible storage I/O contention`처럼 원인 가설을 이름으로 표시 |
| `GPU utilization fell, indicating compute stalls` | Utilization 감소를 관측 사실로 기록하고 stall time은 미측정으로 구분 |
| `Storage caused the slowdown` | 동시에 변한 근거를 제시하고 storage가 기여했을 가능성과 확인에 필요한 증거를 설명 |

검토가 `accept`이면 초안을 그대로 유지해야 하며, `revise`이면 수정 사유와 실제 변경이 있어야 합니다.
수정 결과에도 기존 evidence·시간 구간·entity 검증을 다시 적용합니다.
후보 제목은 `Possible `로 시작해야 하고 일부 causal action verb는 제목에서 거부합니다.
요약의 직접적인 원인 설명과 candidate 설명의 조건 없는 causal 표현에도 보수적인 English 문자열 검사를 적용합니다.
한국어의 `때문에`, `원인입니다`, `유발` 등의 일부 표현도 검사하고 가능성·가설·부정 표현과 구분합니다.
가설·가능성·부정 표현은 구분하지만 이 문자열 검사는 표현 형식의 보호 장치이며 자연어 인과관계를 완전히 판별하는 도구는 아닙니다.
한국어 조사 뒤에 붙은 `m1은`·`m999가` 형태의 observation ID도 evidence 검사 대상입니다.
최종 summary·explanation과 검토 사유에는 한국어 문장이 있어야 하며 technical term만 필요한 목록과 후보 제목은 영어로 둘 수 있습니다.
검토가 거부되거나 완료되지 않거나 수정 결과가 검증을 실패하면 진단 명령도 실패합니다.

생성과 검토는 기본 600초 budget을 공유하며 검토를 건너뛰는 성공 경로는 없습니다.
같은 모델의 두 번째 검토도 오류를 놓칠 수 있으므로 검토 통과를 사실성이나 causality의 증명으로 해석하지 않습니다.
최종 가설을 판단할 때는 `missing_evidence`와 참조된 실제 측정값을 확인합니다.

## Limits and Failure Handling

32K context에서 추론·출력 공간을 남기기 위해 모델 입력 크기를 제한합니다.
잘못된 응답, 존재하지 않는 evidence 참조, 출력 token 한도 초과, timeout은 선택적 진단 명령의 실패로 처리하며 정상 판정을 만들어 내지 않습니다.
Ollama의 `done=true`, `done_reason=stop`과 요청한 model 이름이 일치하는 assistant 응답만 받아들입니다.
Tag를 생략한 model 이름과 `:latest`는 같은 이름으로 처리합니다.
이 검사는 현재 [Ollama chat API](https://docs.ollama.com/api/chat)의 완료 응답 계약을 따릅니다.
현재 구간의 측정값이 전혀 없으면 `no_issue_observed` 판정을 거부하고, `insufficient_evidence` 응답에는 candidate를 허용하지 않습니다.
기존 rule 결과와 telemetry 수집에는 영향을 주지 않습니다.

진단 CLI는 성공·실패 결과를 임시 파일에서 atomic replacement로 기록합니다.
실패하면 종료 코드 `1`을 반환하고 지정한 output을 `record_type=llm_diagnosis_failure`인 기록으로 교체하므로 이전 성공 결과가 최신 진단으로 남지 않습니다.
실패 기록에는 reason·요청 model과 검증된 입력이 있으며, 모델 응답을 거부한 경우 final output도 보관하되 thinking text는 저장하지 않습니다.
결과를 읽는 도구는 `record_type=llm_diagnosis`를 확인한 뒤 `diagnosis`를 읽어야 합니다.
Input과 output을 같은 경로로 지정하는 명령은 입력 보존을 위해 실행 전에 거부합니다.

현재 validator는 일부 entity 혼합과 evidence ID 누락을 막지만 자유 서술의 scope·인과 해석·조사 명령의 정확성까지 검증하지 못하므로 모델의 결론과 참조된 측정값을 함께 읽어야 합니다.
모델에 tool 실행이나 자동 remediation 권한은 없습니다.
이 PoC는 Ollama API의 structured JSON output과 thinking을 사용하며, 다른 backend·자동 호출·Grafana 표시·추가 evidence 조회는 실측 유용성과 실패 유형을 평가한 뒤 확장할 수 있습니다.

## Multi-node Clock Evidence

여러 node의 metric을 비교할 때는 clock alignment를 별도 evidence로 전달합니다.
Saved diagnosis의 `clock_quality`는 rule 결과 없이 observation packet에 보존되고 compact table에도 포함됩니다.
Current 또는 baseline의 clock 상태가 `unsafe`/`unknown`이면 LLM response도 `insufficient_evidence`만 허용합니다.
Unchecked 입력은 동기화가 검증된 입력을 뜻하지 않습니다.

직접 수집하는 source config에는 `cluster`와 `clock.nodes`를 추가하여 workload boundary를 만든 node와 관련 GPU·rollout·sandbox·storage producer를 모두 지정합니다.
각 PromQL에도 같은 cluster와 실제 node/source 조건을 사용합니다.
Clock threshold와 검사 한계는 [Clock and Node Selection](diagnosis.md#clock-and-node-selection)에 설명합니다.

```json
{
  "cluster": "training-cluster",
  "clock": {
    "nodes": ["gpu-a", "gpu-b", "storage-a"],
    "require_sync": true,
    "max_skew_seconds": 1,
    "max_sample_age_seconds": 30
  }
}
```

이 fragment를 기존 source config에 병합합니다.
Source config의 interval과 queries 설정은 그대로 필요합니다.
Clock 상태가 불명확하면 LLM이 임의로 timestamp를 보정하거나 시간 overlap을 추론하지 않습니다.

## Recorded Validation

아래 기록은 당시 prompt·설정과 실행 범위의 관찰 결과입니다.
현재 버전의 일반적인 진단 정확도나 새 학습 run의 검증 결과를 뜻하지 않습니다.
기본 사용에는 [입력 수집](#diagnose-collected-metrics-directly)과 [실패 처리](#limits-and-failure-handling)를 먼저 읽습니다.

<details>
<summary>과거 local model 검증 결과 보기</summary>

### Validation Snapshot (2026-09-29)

Ollama 0.34.4와 `qwen3.5:27b` Q4_K_M을 24GB RTX PRO 4000 Blackwell 한 개에서 실행했습니다.
먼저 16K context / 6,144 output token으로 평가했으며 모델 전체가 GPU에 적재되고 선택한 GPU의 사용 메모리는 약 17.4GiB였습니다.
[검증 기록](validation/local-llm/validation-summary.json)에 model digest, seed, prompt version, 각 사례의 결과와 실패를 보관합니다.
이 결과는 한 번씩 실행한 소규모 fixture 평가이며 일반적인 진단 정확도 benchmark가 아닙니다.

| 검증 | 관찰 결과 |
| --- | --- |
| Synthetic 13개 | 모델 응답 12개 완료, 1개 출력 한도 초과. 최종 validator가 11개를 채택하고 timestamp가 없는 후보 1개를 추가 거부했습니다. |
| 정상·병목 | 정상은 후보 없이 판정했고, storage·network·rollout·KV·host memory·communication·sandbox·straggler 사례에서 관련 후보를 찾았습니다. |
| Evidence 참조 | 확인한 병목 8개 중 6개가 primary evidence coverage를 충족했습니다. Communication의 RDMA와 straggler의 peer ID는 evidence 배열에서 빠졌습니다. |
| 부족한 증거 | Step duration과 GPU utilization만 있는 사례에서 외부 dependency를 추측했습니다. 보수적인 증거 부족 판정으로 볼 수 없습니다. |
| Engine 분리 | A/B를 구분해 서술했지만 node 수준 후보 하나로 묶었습니다. Shared-resource 관계까지 확인된 것은 아닙니다. |
| Timestamp 부재 | 모델이 시간적 상관을 주장해 기대 판정에 실패했습니다. 실제 응답에 interval-presence 검사를 적용해 후보가 거부되는 것을 확인했습니다. |
| 작업량 증가 | 출력 6,144 token 한도에 도달했습니다. 작업량 증가와 병목을 올바르게 구분했다고 주장하지 않습니다. |

완료된 synthetic 응답은 약 96–182초가 걸렸습니다.
일부 응답은 원인 관계를 과하게 표현하거나 관측하지 않은 실행 방식을 가정하므로, 구조 검증 통과를 설명의 정확성으로 해석하면 안 됩니다.
개발 중 정상 판정의 모순과 불필요한 추가 가설도 발견해 schema 설명과 일반적인 evidence 지침을 보완했으며, 위 표는 최종 prompt로 실행한 사례만 집계합니다.

기본 설정은 이 평가 이후 **32K context / 16,384 output token**으로 늘렸습니다.
24GB GPU에서 100% GPU 적재를 유지했고 peak 메모리는 18,710MiB(약 18.3GiB)였습니다.
초기 출력 한도에 도달했던 저장된 VERL 입력과 작업량 증가 사례를 재검증해 각각 약 180초, 218초에 `no_issue_observed` 응답을 얻었습니다.
작업량 증가 응답은 token 수와 duration의 비례 증가를 설명했습니다.
13개 전체를 새 예산으로 다시 실행한 결과는 아니며, 앞 표의 초기 평가와 구분해 기록합니다.

실측 입력은 이전 VERL run의 저장된 step 3 메트릭과 이번에 Node Exporter/Prometheus로 수집한 host CPU·memory·disk 메트릭입니다.
두 입력 모두 `no_issue_observed`였으나, VERL 입력은 hardware metric과 raw series가 부족하며 host 입력은 학습 중 수집한 데이터가 아닙니다.
새 VERL 학습이나 실제 병목 주입 검증으로 해석하면 안 됩니다.
Ollama를 종료한 상태에서 기본 CPU 테스트 **166개**가 통과했고, 실제 종료 시 진단 GPU 메모리가 16MiB로 돌아오는 것도 확인했습니다.

### Input and Evidence Validation (2026-09-30)

같은 저장된 synthetic packet 다섯 개를 compact 입력과 prompt version 6으로 다시 실행했습니다.
[검증 기록](validation/local-llm/input-optimization-validation.json)에 원본·전송 payload 크기, 이전 version 4와 새 prompt token 수, 결과와 남은 한계를 보관합니다.
한 사례당 한 번 실행한 결과이며 새 VERL 학습이나 일반적인 진단 정확도 측정은 아닙니다.

| 사례 | 원본 packet -> Ollama payload | 실제 결과 |
| --- | --- | --- |
| 증거 부족 | 1,151 -> 1,005 bytes | `insufficient_evidence`, 후보 없음. 요약에는 외부 wait 가능성의 추정 표현이 남음. |
| Storage device | 4,323 -> 2,408 bytes | Storage latency·device busy를 인용. 제목의 data-loading 인과 표현은 측정 이상으로 강함. |
| 서로 다른 engine | 4,960 -> 2,688 bytes | Engine A/B의 별개 signal을 한 후보로 합쳐 validator가 거부. |
| Straggler | 4,906 -> 2,640 bytes | Peer rank ID를 counter evidence에 포함. Rank와 node의 소속 관계는 입력에 없어 서술이 과도함. |
| Communication | 4,322 -> 2,407 bytes | Weight-sync duration과 RDMA activity ID를 함께 인용. |

여러 series가 있는 사례의 전체 prompt token 수는 이전 version 4 대비 약 24–27% 줄었습니다.
이 수치에는 표 인코딩뿐 아니라 변경된 system prompt도 함께 반영됩니다.
반대로 증거 부족 사례는 입력 payload가 작아 추가 지침 때문에 prompt token이 997개에서 1,059개로 늘었습니다.
생성 token 수 역시 감소하지 않아 inference가 빨라졌다고 말할 수 없습니다.
별도의 64-worker-series fixture는 11,867-byte 원본 packet을 3,750-byte payload로 전달하며 모든 series를 보존하는지 실제 호출 경로의 mocked test로 확인했습니다.

### Review Validation (2026-09-30)

입력 타입·evidence 가용성·entity namespace·Ollama 완료 응답·실패 파일 처리와 평가 종료 코드를 보완했습니다.
이 초기 검증 단계에서 전체 CPU 테스트 226개가 통과했으며 이 중 LLM 진단 테스트는 68개였습니다.
이 테스트는 모델 없이 누락값, 불량 입력, entity 혼합, 미완료 응답과 재실행 실패를 검증합니다.

같은 `qwen3.5:27b`와 Ollama 0.34.4로 storage device와 증거 부족 synthetic 입력을 prompt version 6·7에서 각각 한 번 실행했습니다.
두 version 모두 storage는 `bottleneck_suspected`와 필수 evidence 참조를 충족했고, 증거 부족은 후보 없이 `insufficient_evidence`를 반환했습니다.
Version 7의 지침은 제목을 가설로, 요약을 관측 사실로 표현하고 낮은 GPU utilization만으로 기다리는 원인을 단정하지 않도록 보완했습니다.
그러나 실제 storage 제목에는 인과관계를 과하게 표현하는 부분이, 요약에는 utilization에서 compute stall을 추정하는 부분이 남았습니다.
증거 부족 요약에서는 이전의 external-resource wait 추정이 사라졌지만 직접 측정하지 않은 GPU idle 가능성은 언급했습니다.

[검증 기록](validation/local-llm/review-validation.json)에 두 version의 응답·input hash·token·latency와 수동 검토 결과를 보관합니다.
최종 version 7의 두 호출은 약 161초와 146초가 걸렸으며, 이 소규모 평가를 일반적인 정확도 개선이나 inference 속도 개선으로 해석하지 않습니다.
실제 CLI의 backend 연결 실패가 이전 성공 파일을 실패 기록으로 바꾸고 종료 코드 `1`을 반환하는 것도 확인했습니다.
Prometheus는 검증 당시 실행되어 있지 않아 query 오류를 `missing_sources`로 남기는 경로만 확인했으며 새 host 측정값이나 VERL 학습을 검증한 것은 아닙니다.

#### Evidence Review Validation (2026-09-30)

영어 출력으로 근거 검토를 검증한 단계에서 전체 CPU 테스트 242개가 통과했으며 LLM 경로 테스트 84개를 포함했습니다.
검토의 수정·거부, 검토 실패 시 초안 미채택, 잘못된 최종 evidence, 원인 단정 표현과 시간 budget을 검증합니다.

Prompt version 8과 review version 1을 실제 Ollama 모델로 실행한 storage 사례에서 검토가 `revise`를 반환했습니다.
초안의 제목은 storage saturation이 I/O latency와 GPU idle time을 늘린다고 표현했고 설명은 I/O contention을 주된 지연 원인으로 기술했습니다.
검토는 GPU idle time이 미측정이고 원인 단정이 부적절하다고 지적해 제목을 `Possible storage device saturation`으로 수정하고 설명에는 실제 수치의 동시 변화만 남겼습니다.
Storage latency와 device busy의 필수 evidence 참조는 유지됐고, 수정 결과는 최종 표현 검사도 통과했습니다.
생성과 검토의 합산 시간은 약 407초였으며 이 중 검토는 약 286초였습니다.
이 결과는 한 synthetic 입력의 실제 모델 호출이며 새로운 학습 run이나 실제 I/O 부하 검증은 아닙니다.
같은 단계의 증거 부족 사례에서는 검토가 초안의 idle/wait time 증가 주장을 제거하고 `Stall or wait time was not measured`로 수정했습니다.
최종 assessment는 후보 없는 `insufficient_evidence`였고 두 호출의 합산 시간은 약 344초였습니다.

한국어 기본 출력과 용어 지침까지 반영한 최종 코드는 전체 CPU 테스트 255개와 이 중 LLM 경로 테스트 97개를 통과했습니다.
Prompt version 10·review version 3의 실제 storage 응답은 한국어 설명과 English metric 이름·technical term을 사용하고 `attribution`도 영어로 유지했습니다.
검토는 원인 관계를 표현한 제목과 미측정 GPU 대기 주장을 수정하고, stable throughput이 storage queueing과 공존할 수 있다는 점을 들어 부적절한 counter evidence도 제거했습니다.
최종 제목은 `Possible storage I/O contention`이었으며 필수 evidence 참조와 한국어·용어·인과 표현 검사를 통과했습니다.
생성과 검토는 약 315초가 걸렸습니다.
[검증 기록](validation/local-llm/review-validation.json)에 영어 기준 검증, 초기 한국어 번역 문제와 최종 한국어 결과를 함께 보관합니다.
검토 사유의 일부 표현은 여전히 사람의 교정보다 어색할 수 있으며, 세 차례의 개별 사례 검증으로 한국어 문장 품질 전체를 보장하지 않습니다.

</details>
