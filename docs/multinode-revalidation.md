# 실환경 Multi-worker · Multi-job · Multi-model 재검증

실제 VERL workload의 완료와 XLayer의 telemetry 관측을 별도로 판정합니다. 이 절차는 기존 cluster에서 두 Run의 동시 application 관측·identity·artifact·Grafana 선택을 확인하며, framework나 운영 서버를 자동 수정하지 않습니다.

## 준비 조건

- 검증할 XLayer commit과 Python·Ray·VERL·transfer_queue·vLLM 버전을 기록합니다. 과거 revision의 성공을 현재 코드의 성공으로 대신하지 않습니다.
- GPU 자원과 다른 사용자의 workload를 확인하고, 이번 검증용 config·port·state·Run directory를 별도로 준비합니다. 기존 cluster나 monitoring stack을 reset하지 않습니다.
- Single-job control에 필요한 model weights·dataset과 실제로 다른 weights를 가진 두 번째 model을 준비합니다. Model directory/weight hash와 framework의 실제 model 인자를 기록하며 `model_name` 변경만으로 multi-model 성공을 판정하지 않습니다.
- Monitoring Grafana는 **13.1.7**이어야 합니다. [배포·업그레이드](app-deployment-reference.md#managed-grafana-upgrade)를 따르고 `/api/health`의 실제 version과 App의 `react19=false`를 확인합니다. 현재 XLayer는 12.1 선택을 허용하지 않습니다.

## Configure

[Multi-node 설정](multi-node.md)을 작업 전용 config에 적용합니다. `CLUSTER_NAME`·`NODE_NAME`·`PROMETHEUS_URL`과 **`TELEMETRY_RUNS_ROOT`**를 확인하며, 두 Run directory를 같은 조회 root 아래에 둡니다. `RUN_ROOT`는 이 설정 키가 아닙니다.

```bash
xltel --config /path/to/test-config.toml config validate
xltel --config /path/to/test-config.toml status --json
xltel --config /path/to/test-config.toml doctor --role server --correlation \
  --diagnostics-config /path/to/test-diagnostics.json
```

**성공 기준:** Status에 Grafana `version=13.1.7`·`version_matches=true`가 보이고 correlation preflight가 통과합니다. Diagnostics inventory에는 monitoring reference·GPU/producer·storage 의존 node를 포함합니다. `blocked`이면 raw telemetry 검증은 진행할 수 있지만 cross-layer diagnosis와 baseline resource delta는 보류합니다.

```{admonition} 관측 단위
:class: important

VERL file logger bridge는 driver 관측입니다. Launcher의 `TELEMETRY_NODE`는 trainer rank가 배치된 물리 node를 증명하지 않습니다. Multi-worker workload는 실제 trainer rank 수와 배치 로그로 확인하고, per-rank metric·worker ↔ GPU mapping이 없으면 `Not instrumented`로 남깁니다. `rollout.n`은 prompt당 sample 수이며 process replica 수가 아닙니다.
```

## Start

기존에 성공한 VERL argv를 유지하고 공식 CLI를 통해 Run metadata를 기록합니다. 다음 두 명령의 `<existing VERL argv>`는 실제 실행 가능한 framework 명령으로 바꿉니다. 이 CLI의 `--model`은 비교 metadata이며 framework의 model weights 설정을 바꾸지 않습니다.

```bash
xltel --config /path/to/test-config.toml run --run-id verify-A \
  --model actual-model-A --workload-fingerprint controlled-workload-v1 \
  -- <existing VERL argv>

xltel --config /path/to/test-config.toml run --run-id verify-B \
  --model actual-model-B --workload-fingerprint controlled-workload-v1 \
  -- <existing VERL argv>
```

동시 시나리오는 서로 다른 terminal에서 실행합니다. CLI는 model·fingerprint와 config의 cluster/observer를 manifest에 보존합니다. Fingerprint에는 dataset·batch·token/context·tool mix·concurrency·execution mode를 반영하고 비교 조건이 바뀌면 값을 바꿉니다. [Run 비교 계약](run-comparison.md)을 따릅니다.

| 순서 | 시나리오 | 별도 확인 |
| --- | --- | --- |
| S1 | 단일 Job·단일 trainer rank | 정상 종료·logger/history 일치 |
| S2 | 단일 Job·2개 이상 trainer rank | 실제 배치와 rank 수·완료 |
| S3 | 같은 model의 두 Job 동시 | 두 application Run 동시 관측·각 Job 완료 |
| S4 | 서로 다른 실제 weights의 두 Job 동시 | Weight identity·engine label·각 Job 완료 |
| S5 | Multi-worker × multi-job × multi-model | 자원에 맞는 배치·S3/S4와 같은 격리 기준 |
| S6 | 두 peer에 step이 생긴 뒤 한쪽 중지·재시작 | Controller 생성자/후발 Job 양 방향·새 Run ID |

S6에서는 이번 검증에서 생성한 launcher process만 중지합니다. 상대 Run의 step 증가·application series·node scrape·artifact가 이어지고 기존 사용자 process와 Ray node가 유지되는지 확인합니다. 기동 단계에서만 중지한 결과로 실행 중 peer stop 검증을 대신하지 않습니다.

## Verify

### 두 Run의 application series 동시 관측

두 Job이 실제 실행 중이었던 공통 구간을 선택합니다. `--start`·`--end`는 **Prometheus reference clock의 Unix seconds**이며, 실행 host의 시각과 clock offset을 섞지 않습니다. 범위는 최대 1시간, query step은 1–300초입니다.

```bash
python examples/multinode/verify_live_runs.py \
  --config /path/to/test-config.toml \
  --run-id verify-A --run-id verify-B \
  --start START_UNIX_SECONDS --end END_UNIX_SECONDS --step 2 \
  > /path/to/new-evidence/application-overlap.json
```

**성공 기준:** Exit 0·`status=observed_overlap`·선택 window의 sampled `run_id` count가 2입니다. JSON은 두 Run의 실제 label과 query 품질을 보존하며, 한 Run만 보이거나 warning·identity 누락이면 exit 1로 남깁니다. Backend query 실패도 성공이나 값 0으로 바꾸지 않습니다.

```{admonition} Visibility ≠ progress
:class: warning

Prometheus range query에서 series가 보이는 것은 sampled visibility이며, retained sample이 포함될 수 있습니다. 동시에 training이 진행됐다는 증거는 각 Run의 logger·완료 step event·lifecycle과 별도로 대조합니다. Native engine/model label만으로 application Run의 소유권을 추론하지 않습니다.
```

### Artifact·workload·비교

```bash
xltel --config /path/to/test-config.toml inspect verify-A
xltel --config /path/to/test-config.toml inspect verify-B
xltel --config /path/to/test-config.toml runs compare verify-A verify-B --json
```

- 각 Run의 유효 logger record와 `telemetry-events/verl-steps.jsonl`을 step identity·payload 기준으로 대조합니다. 줄 수만 같아도 누락·중복·같은 ID의 충돌을 놓칠 수 있으며 validation step 0도 포함합니다.
- 정상 종료는 workload exit 0·health `complete`·logger settle을 확인합니다. 의도적 중지는 exit code·`partial` artifact 보존을 확인하며 `observed`·`delayed`를 training 진행으로 읽지 않습니다.
- Manifest의 model·cluster·fingerprint·execution mode를 확인합니다. Model이 다른 pair는 동등 workload baseline이 아니며, metadata 누락 시 제한된 비교와 delta 보류를 유지합니다. Clock gate가 막히면 resource correlation은 보류합니다.
- Identity는 각 관측 scope에서 검증합니다. Shared node/GPU/3FS metric에 `run_id`를 붙여 특정 Job 사용량으로 만들지 않습니다. Exact span이 없는 bridge interval은 approximate로 유지합니다.
- MFU `0.0`은 수치 계약상 유효한 zero입니다. 실제 성능 판정에 쓰려면 deployed producer의 계산과 지원 조건을 확인하며 임의로 missing으로 바꾸지 않습니다.

### Grafana 실제 화면

Native dashboard와 선택적 App/Scenes를 각각 검증합니다. Run A ↔ B, 실제 worker/engine 선택, Reload·Back·공유 URL을 수행하고 **발생한 datasource query의 Run·entity predicate와 시간창**을 확인합니다. Dropdown option이 보이는 것만으로 drill-down 성공을 판정하지 않습니다.

종료된 Run의 now window에서 `N/A`인 것과 live Run의 실제 `0`을 구분합니다. 종료 Run의 고정 historical window는 backend retention 안에서 별도로 조회하고, 저장 artifact는 `inspect`로 확인합니다. App 사용 시 시간대·Light/Dark·좁은 화면에서도 Run·Step·window 문맥을 확인합니다. Loki가 꺼져 있다면 log panel 검증은 `Not run`으로 남깁니다.

## Troubleshooting: XLayer 밖의 조치

| 증상 | 가이드와 재개 조건 |
| --- | --- |
| Monitoring reference가 앞서거나 clock evidence 누락 | 운영 담당자가 reference와 dependency host의 시간 동기화를 확인하고 누락 node의 exporter를 등록합니다. `node_timex_sync_status=1`만으로 reference와의 alignment를 보장하지 않습니다. 수정 후 preflight 재실행; threshold 확대·calibration으로 우회하지 않습니다. |
| 두 Job 중 하나가 `transfer_queue.kv_list`에서 멈춤 | Stack·actor name/namespace·owner/lifetime·cleanup log를 수집해 framework 측에 전달합니다. Job별 controller 지원 revision 또는 승인된 격리 Ray cluster에서 control을 재실행합니다. 기존 shared cluster reset이나 site-packages patch는 이 절차에 포함하지 않습니다. |
| 기존 Grafana가 12.1 | 일관된 DB/config backup 후 운영 담당자가 13.1.7로 upgrade합니다. 운영 DB migration/restore는 별도 검증입니다. 현재 XLayer에서 12.1을 선택해 우회할 수 없습니다. |
| Per-rank/agent-worker metric이나 exact span 없음 | Framework 계측이 필요한 영역으로 기록합니다. Driver label·추정 node·synthetic span으로 빈 영역을 채우지 않습니다. |

TransferQueue singleton과 cleanup의 시간적 일치는 강한 원인 후보이지만, 격리 control로 재현이 사라지는지까지 확인해야 해결을 입증할 수 있습니다. 관측한 timeout 범위의 non-progress를 기록하며 이 결과만으로 모든 VERL 환경의 동시 완료가 불가능하다고 일반화하지 않습니다.

## Next: 판정과 정리

Workload 완료·application 동시 관측·cross-job 격리·clock/correlation·UI를 각각 `PASS / FAIL / Blocked / Not instrumented / Not run`으로 기록합니다. 두 Job이 모두 완료하지 못했다면 telemetry 확인 범위와 별도로 workload concurrency를 Pending으로 유지합니다.

작은 증거 묶음에 commit SHA·실제 version/API health·model weight identity·placement·manifest/logger/history/health·query JSON·clock inventory/reference·UI query/window와 screenshot을 보존합니다. 실제 GPU 검증과 CPU/synthetic 검증을 구분하고, 이번 작업의 process·temporary config만 정리합니다. 기존 사용자 process·cluster·monitoring과 Run evidence는 보존하며 [검증 기록](validation/README.md)의 범위와 비교합니다.
