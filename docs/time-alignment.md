# Time Alignment

**목표:** 필요한 관측 노드를 명시하고 clock 품질이 충분한 시간 구간만 correlation에 사용합니다. XLayer는 시스템 시간이나 NTP 서비스를 설정하지 않습니다.

(correlation-preflight)=
## Correlation Preflight

### 준비 조건

| 관측 대상 | 등록할 clock host |
| --- | --- |
| Trainer / observer | Diagnostics의 `node` 또는 `NODE_NAME` |
| GPU / CPU / memory | `compute_node`와 실제 host 관측 node |
| Rollout / vLLM / Ray / Mooncake client | `rollout_node`; 별도 host이면 `clock.nodes` |
| Mooncake Master | Companion profile의 `prometheus.mooncake_master_node` |
| 3FS | ClickHouse server뿐 아니라 실제 timestamp producer의 `threefs.clock_nodes` |
| Monitoring / Prometheus | `clock.monitoring_node` |
| Dedicated tool / sandbox | `sandbox.node`와 별도 실행 host의 `clock.nodes` |

같은 물리 host의 여러 역할은 같은 논리 node 이름으로 등록합니다. 모든 해당 host의 Node Exporter가 `TELEMETRY_TARGETS`에 등록되어야 합니다. 이 목록은 설정한 observation inventory이며 자동 발견한 topology·dependency나 Run의 resource 소유 관계가 아닙니다.

Monitoring host에도 clock metric을 제공하는 node collector가 필요합니다. Server만 실행했다면 GPU가 없는 host의 config에 `ENABLE_GPU_METRICS=0`을 설정하고 `xltel up --role node`를 추가합니다.

### 1. 시스템 동기화 확인

멀티노드는 운영자가 NTP/chrony 또는 system clock을 동기화하는 PTP를 구성합니다. Chrony 환경에서는 각 host에서 다음 읽기 전용 명령을 확인합니다.

```bash
chronyc -n tracking
chronyc -n sources -v
chronyc waitsync 10 0.01 0 1
```

**정상 결과:** 선택한 reference가 있으며 `Leap status`가 `Normal`이고 `waitsync`는 exit 0입니다. `0.01`은 예시 remaining-correction 허용값입니다. 실제 조사 구간에 맞춰 정합니다. `timedatectl`의 NTP enabled flag만으로 통과시키지 않습니다. Chrony의 `rtcsync`와 Node Exporter timex 수집도 확인합니다. PTP는 NIC의 PHC뿐 아니라 실제 timestamp에 쓰는 `CLOCK_REALTIME`을 확인해야 합니다.

### 2. Observation inventory 설정

[Multi-node diagnosis 예제](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/multinode/diagnostics.json)의 node mapping과 기존 diagnostics 설정을 사용합니다. `clock.monitoring_node`에는 실제 Prometheus host를, `clock.nodes`에는 다른 설정에서 빠진 service/tool host를 추가합니다.

```json
"clock": {
  "monitoring_node": "monitor-a",
  "nodes": ["tool-a"],
  "require_sync": true,
  "max_skew_seconds": 1,
  "max_uncertainty_seconds": 1,
  "max_sample_age_seconds": 30
}
```

`clock.nodes`는 실제 별도 관측 host가 있을 때만 지정합니다. 최대 32개 host를 검사하며 query는 기존 30초 budget을 공유합니다. Monitoring과 collector를 시작한 뒤 사전 검사를 실행합니다. 설치 전에 쓰는 기본 doctor는 바뀌지 않습니다.

Clock query는 metric별 series 수·출처 label·출처별 evaluation 수와 backend warning/info·폐기된 sample을 보존합니다. Production Prometheus 응답에서 한 출처의 충분한 evaluation과 metric 간 동일 출처를 확인하지 못하면 raw 값은 유지하고 `unknown`으로 보류합니다. 두 evaluation은 실제 두 scrape나 연속 coverage를 보장하지 않습니다.

### 3. Doctor로 검증

```bash
xltel doctor --correlation --diagnostics-config ./diagnostics.json --json
# DIAGNOSTICS_CONFIG를 이미 설정했다면:
xltel doctor --correlation
```

| Preflight 결과 | 의미 / 행동 |
| --- | --- |
| `pass` | 선언된 host의 sampled clock screening 통과; 이후 각 Step/Phase는 다시 검사 |
| `needs_attention` | Target·clock metric·monitor identity·evaluation 부족·복수 출처·부분 query 응답 확인 |
| `blocked` | Unsynchronized·큰 offset/uncertainty·stale/clock variation 해결 후 재검사 |
| `not_configured` | `DIAGNOSTICS_CONFIG` 또는 명시한 JSON 설정 필요 |

**정상 결과:** `correlation_preflight.status=pass`와 필요한 host별 `aligned`가 표시됩니다. 기본 installation check도 통과한 doctor의 exit code는 0, 부족한 조건이 있으면 1입니다. Raw metric이나 workload 실행을 삭제·종료하지 않습니다.

```{admonition} Correlation 허용 범위
:class: important

Kernel sync flag·NTP offset·maxerror·scrape 상대 offset·sample age를 함께 검사합니다. Uncertainty/offset variation의 예산은 `min(max_uncertainty_seconds 또는 max_skew_seconds, interval / 10)`이며, 조사 구간보다 오래된 clock sample은 보류합니다. Kernel maxerror는 보고된 보수적 오차이며 UTC confidence가 아닙니다. PTP/daemon에서 이 증거를 제공하지 못하면 자동 pass로 바꾸지 않습니다.
```

단일 node의 monotonic span duration은 NTP와 별개로 보존합니다. 멀티노드에서는 `enabled=false`·`require_sync=false`·application calibration으로 OS clock 검사를 우회하지 않습니다. 과거 clock 상태, raw 3FS timestamp, producer collection 시각은 현재 preflight 결과로 복원하거나 재작성하지 않습니다.

### 조사 화면에서 확인

- Scenes는 저장된 clock status·검사된 resource node·uncertainty·age를 재사용합니다. Step에서 통과했어도 더 짧은 Phase 예산이 부족하면 `Clock unverified`이며 값·phase delta를 보류합니다.
- Async trainer update에 같은 Run/Step 번호의 다른 worker rollout을 소유 관계로 연결하지 않습니다. 기존 explicit parent span 관계와 raw call duration은 유지합니다.
- Query evaluation 수는 scrape 수가 아닙니다. Rolling lookback·source freshness unknown·누락된 sample은 supporting/missing으로 남기며, 알려진 resolution 부족의 baseline delta를 보류합니다.
- 실제 source timestamp 확인에는 기존 `sampling.check_source_freshness=true`를 사용합니다. 단일 source를 추출할 수 없는 computed query의 freshness는 계속 unknown입니다. NTP pass가 metric의 collection time이나 scrape coverage를 보장하지 않습니다.
- Related Metrics·기존 상세 dashboard의 raw data는 계속 읽을 수 있습니다. Correlation ≠ Attribution ≠ Causality입니다.

검사의 source 계약은 [Node Exporter timex](https://github.com/prometheus/node_exporter/blob/v1.9.1/collector/timex.go)와 [time-sync 문서](https://github.com/prometheus/node_exporter/blob/v1.9.1/docs/TIME.md), 운영 확인은 [chronyc](https://chrony-project.org/doc/4.8/chronyc.html)를 기준으로 합니다. Sampled screening은 scrape 사이의 모든 clock jump를 증명하지 않습니다.

## Optional Userspace Time Alignment

OS clock을 바꿀 권한이 없어도 XLayer의 Step·Span을 monitoring host의 공통 시간축에 표시할 수 있습니다.
각 node가 monitoring host와 timestamp를 교환하여 offset과 uncertainty를 계산하며, `sudo`나 추가 package는 필요하지 않습니다.
이 기능은 선택 사항이며 timestamp mapping을 추가합니다. 멀티노드 NTP/clock 사전 조건을 대신하지 않습니다.

## Configure a Common Reference

1. **Prometheus와 같은 clock을 쓰는 monitoring host**에서 reference를 실행합니다.
   아래 listener는 foreground로 실행되며 `Ctrl+C`로 종료합니다.
   `MONITOR_PRIVATE_IP`는 monitoring host에 실제 할당된 private/VPN IP로 바꾸며, client의 `--url`에도 같은 주소를 사용합니다.
   같은 host에서만 시험할 때에는 양쪽 주소를 `127.0.0.1`로 지정합니다.

   ```bash
   xltel clock serve --bind MONITOR_PRIVATE_IP --port 19120 --reference-id monitor-1
   ```

2. 각 workload/sandbox node에서 자신의 calibration 파일을 만듭니다.
   `--node`는 collector·SDK의 node 이름과 일치해야 합니다.

   ```bash
   xltel clock calibrate \
     --url http://MONITOR_PRIVATE_IP:19120 --reference-id monitor-1 \
     --node gpu-a --file "$HOME/telemetry/state/clock.json" \
     --ttl 300 --interval 30
   ```

   `--interval`을 생략하면 한 번만 측정합니다.
   Continuous refresh는 foreground로 실행되며, 실패해도 기존 파일을 지우지 않습니다.
   파일이 만료되면 SDK는 보정을 중단하고 time alignment를 `unknown`으로 기록합니다.
   이 optional process는 `xltel up/down`의 관리 대상이 아니므로 사용자가 시작한 terminal/service에서 종료합니다.

3. 해당 node의 `xltel` TOML config에 파일 경로를 추가하고 workload/collector를 시작합니다.
   기존 실행 명령은 바뀌지 않습니다.

   ```toml
   [telemetry]
   NODE_NAME = "gpu-a"
   TELEMETRY_TIME_CALIBRATION_FILE = "~/telemetry/state/clock.json"
   ```

   SDK를 직접 사용하는 process에는 `TELEMETRY_TIME_CALIBRATION_FILE` 환경변수로 전달합니다.
   Remote Ray worker나 dedicated sandbox도 **자신의 node/boot에 해당하는 파일**을 사용해야 합니다.
   Launcher의 environment가 모든 remote process에 자동 전파된다고 가정하지 않습니다.

4. Diagnosis config에 reference를 명시합니다.
   이 설정은 해당 reference와 Prometheus scrape clock이 같다는 운영자의 확인입니다.

   ```json
   {
     "clock": {
       "calibration_reference": "monitor-1",
       "max_skew_seconds": 1,
       "require_sync": true
     }
   }
   ```

   기존 diagnosis config에 병합합니다.
   `clock.enabled=false`로 설정해도 이미 보정된 record의 reference 검증은 생략하지 않습니다.
   보정된 step 없이 이 설정만 켜면 `unknown`으로 보류합니다.

확인 명령:

```bash
xltel clock status --node gpu-a --file "$HOME/telemetry/state/clock.json"
xltel run -- <existing VERL command>
xltel inspect
```

## What Changes

![Four-timestamp exchange가 원본 시각을 보존하며 공통 reference 시간축을 만드는 과정](figures/diagrams/clock-alignment.svg)

| 데이터 | 원본 | Investigation에서 사용하는 시간 |
| --- | --- | --- |
| VERL step | `observed_at`, `ingested_at` 보존 | `analysis_window`, `window_start_ms/end_ms`, `correlation_observed_at` |
| SDK span/event | `start/end_time_unix_nano`, `timestamp_unix_nano` 보존 | `event_time_unix_nano`, `start/end_time_ms`, correlation fields |
| SDK/GPU/sandbox freshness | JSONL/snapshot의 local timestamp 보존 | freshness gauge를 reference time으로 변환; 보정 불가 시 gauge 생략 |
| Prometheus resource metric | 기존 scrape timestamp와 metric 값 | 변경 없음 |
| 3FS ClickHouse / 외부 profile·log | 기존 producer timestamp | 자동 보정하지 않음 |

Span duration은 기존 monotonic clock으로 계산합니다.
Trace/span ID, parent 연결, run·step·worker·node context와 Prometheus label은 바뀌지 않습니다.
Loki의 XLayer Step/Event stream은 보정된 조사 시각을 사용하지만, 일반 application stdout log의 timestamp는 다시 쓰지 않습니다.
원본 record와 `time_alignment.raw_window`는 offline 조사에 남습니다.

Grafana Timeline은 `exact`와 `calibrated`를 구분하고 calibrated lane에 `± uncertainty`를 표시합니다.
VERL file logger에서 추정한 step은 보정 후에도 `calibrated_approximate`이며 정밀한 phase trace가 되지 않습니다.
Timestamp 없는 replay/backlog도 여전히 `unknown`입니다.
SDK calibration cache는 fork 이후 child에서 lock과 조회 상태를 초기화하므로 parent의 읽기 작업을 기다리지 않습니다.

## Quality and Failure Boundaries

Client send/receive를 `t1/t4`, reference receive/send를 `t2/t3`라고 하면 다음을 계산합니다.

```text
offset = ((t2 - t1) + (t3 - t4)) / 2
reference_time = node_time + offset
network_RTT = monotonic_elapsed - (t3 - t2)
uncertainty = network_RTT / 2 + measurement_error + assumed_drift
```

왕복 sample 중 RTT가 가장 작은 것을 선택합니다.
Client의 HTTP worker가 request 시작과 전체 응답 수신 시각을 기록하므로 worker 시작·IPC 대기 시간은 network RTT에 포함하지 않습니다.
주입된 client clock은 두 sampling bracket 사이에서 request 경계를 보간하므로 허용된 drift가 있어도 시작·수신 순서와 receive anchor를 보존합니다.
sampling bracket·시작·반환 구간의 drift·보간 조정 오차는 `measurement_error`에 더하며, 반환 지연은 실제 수신 이후 TTL에 포함합니다.
`RTT/2`는 nonnegative network delay와 안정적인 clock을 가정한 path asymmetry 범위이며, UTC 정확도를 보장하는 confidence score가 아닙니다.
`--drift-ppm` 기본값은 100이고, TTL 동안 허용할 clock drift의 운영상 가정입니다.
이 가정을 보장할 수 없으면 TTL을 줄이거나 시스템 동기화를 사용합니다.

Reference mismatch, node/boot mismatch, 만료, 파일 손상, 관측한 clock jump, coverage 부족이면 보정하지 않습니다.
Reference 재시작·감지된 clock jump는 session을 바꾸며 다른 session의 baseline 비교는 보류합니다.
Reference clock이 측정 사이에 바뀌는 것을 즉시 알아낼 수는 없으므로 짧은 TTL과 주기적 refresh가 필요합니다.

Diagnosis는 uncertainty가 **`min(max_skew_seconds, interval / 10)`**을 넘으면 candidate·resource baseline delta를 보류합니다.
Rule과 LLM 입력은 같은 검사를 사용하고 OS clock screening 결과도 별도로 보존합니다.
짧은 span·step은 calibration이 있어도 분석 해상도가 부족할 수 있습니다.

Refresh 파일에는 최근 최대 32개의 calibration만 보존합니다.
한 calibration의 유효 구간 안에 전체 step/span이 들어와야 하므로 TTL보다 긴 구간이나 계측 시작 전 구간은 보정할 수 없습니다.
SDK는 configured file을 process당 초당 최대 한 번 읽고 network request를 실행하지 않습니다.
파일은 node-local regular file이어야 하며 FIFO·잘못된 입력은 workload를 멈추지 않고 `unknown`으로 처리합니다.
느린 filesystem의 synchronous read 지연 자체까지 격리하지는 않습니다.

## Scope and Deployment Limits

`clock.calibration_reference`는 Prometheus **scrape-time** query에 적용합니다.
Exporter가 explicit sample timestamp를 내보내거나 remote-write가 별도의 producer timestamp를 보존한다면 이 가정을 사용할 수 없습니다.
3FS의 `TIMESTAMP`, 외부 profile, 일반 log까지 보정됐다고 해석하면 안 됩니다.
3FS는 기존 `threefs.clock_nodes` 검사와 producer clock 동기화가 계속 필요합니다.
Calibrated 조사에서 보정되지 않은 remote tool/sandbox event는 time join에서 제외합니다.

Listener는 TLS/authentication을 제공하지 않습니다.
Loopback·private network·Tailscale 등 신뢰하는 연결에서 사용하고 public internet에 직접 노출하지 않습니다.
Client의 `--timeout`은 sample마다 DNS·headers·전체 body·worker 시작·IPC를 포함하는 총 deadline이며, byte가 조금씩 도착해도 연장되지 않습니다.
매우 짧은 deadline은 정상 endpoint에서도 worker 시작 중 만료될 수 있으며, timeout 이후 worker 정리에는 별도의 짧은 상한이 적용됩니다.
응답은 4096 bytes로 제한하고 redirect는 따라가지 않으며, SDK가 reference outage를 기다리는 구조는 아닙니다.
Listener는 연결을 받은 뒤 2초의 absolute deadline을 적용하고, request line·header가 느리게 들어와도 해당 연결만 닫아 다음 client를 처리합니다.

이 보정은 resource attribution을 추가하지 않습니다.
같은 시간대의 GPU·NIC·NVMe·shared-service 변화는 여전히 supporting correlation입니다.
Logical clock은 이벤트 순서·parent 연결을 표현할 수 있지만 resource metric의 Unix time과 맞추는 기능을 대신하지 않습니다.
가능하면 NTP/chrony를 기본으로 사용하고, 이 기능은 시스템 clock을 바꾸기 어려운 환경에서 활용합니다.

## Correlation Limits / TBD

| 현재 가능한 것 | 남은 경계 |
| --- | --- |
| 선언한 node/entity의 sampled time-window 조회 | 실제 물리 multi-node·GPU·Agent RL에서 아직 검증하지 않음 |
| Exact/calibrated span과 approximate Step 구분 | Async overlap·같은 Step 번호만으로 execution 소유 관계를 만들지 않음 |
| Source freshness·rolling window·clock budget의 제한 표시 | Query evaluation은 actual collection 시각·연속 coverage가 아님 |
| 기존 explicit parent/span 관계 조회 | Upstream을 통과하는 operation-level tracing과 end-to-end propagation은 TBD |
| Shared/node/worker/cgroup 관측 범위 보존 | 정확한 Resource/Run attribution은 TBD; [Storage 계측 과제](storage-correlation.md) 참고 |

새 request/trace ID를 Prometheus label로 추가하거나 upstream 프레임워크를 수정하지 않습니다. 이번 기능은 운영 전제 조건과 기존 관측의 시간적 연관을 검증하는 PoC입니다.

2026-10-08 검증에서는 CPU 1,474개·frontend 99개·CI helper 17개, 실제 Grafana의 clock 보류/복귀와 multiworker·Storage 여정, 문서 browser 9개가 통과했습니다. 실제 Prometheus API의 synthetic 4-node preflight는 20개 요청·약 0.75초였습니다. 물리 NTP 또는 production query 성능 검증은 아닙니다. {download}`검증 기록<validation/cross-layer-correlation-20261008.json>`에서 환경과 한계를 확인합니다.

계산의 기준은 [NTP four-timestamp model (RFC 5905)](https://www.rfc-editor.org/rfc/rfc5905.html#section-8)이며, NTP daemon이나 protocol 자체를 구현하는 것은 아닙니다.
