# vLLM / Ray 연결

**목표:** 기존 native metric endpoint를 Prometheus에 등록하고 serving/orchestration signal을 확인합니다.

## 얻는 것

| Source | Metric | Scope |
| --- | --- | --- |
| vLLM | Queue·TTFT/E2E p95·KV usage·지원되는 offload | Endpoint / model / engine |
| Ray | Task/actor state·logical resource·object store·eviction | Cluster / session / component |

## 준비 조건

- [Monitoring server](monitoring.md) 실행 중.
- Monitoring host에서 native `/metrics` 접근 가능.
- Endpoint metric 이름이 사용하는 version과 일치.

```{admonition} Shared scope
:class: important

Native source에 VERL `run_id`가 자동으로 붙지 않습니다. Ray logical CPU/GPU는 physical utilization이 아니며 서로 다른 engine의 queue·KV·preemption을 합쳐 판정하지 않습니다.
```

## 1. Configure

VERL의 vLLM metrics가 꺼져 있다면 기존 명령에 두 설정을 함께 지정합니다.

```text
actor_rollout_ref.rollout.disable_log_stats=False
actor_rollout_ref.rollout.prometheus.enable=True
```

실제 endpoint는 VERL이 만든 Prometheus 설정의 `rollout` job에서 확인합니다. XLayer의 `prometheus.yml`을 VERL 출력 파일로 지정하지 않습니다.

파일 `~/telemetry/config/native-sources.json`을 준비하고 실제 주소를 등록합니다. 다음 값은 형식 예시이며 배포 주소로 바꿉니다.

```json
{
  "schema_version": 1,
  "sources": [
    {"name": "rollout-0", "kind": "vllm", "target": "10.0.0.11:8000",
     "metrics_path": "/metrics", "labels": {"node": "rollout-0", "role": "rollout"}},
    {"name": "ray-0", "kind": "ray", "target": "10.0.0.11:8080",
     "metrics_path": "/metrics", "labels": {"node": "rollout-0"}}
  ]
}
```

배포에 없는 endpoint는 제거하고, node 이름은 host collector의 논리 이름과 맞춥니다. Target은 scheme/path 없는 `host:port`입니다.

기존 TOML `[telemetry]`에 추가합니다.

```toml
TELEMETRY_SOURCES_FILE = "~/telemetry/config/native-sources.json"
```

## 2. Start / Refresh

```bash
xltel config validate
# Native job을 처음 추가할 때만 server를 재시작합니다.
xltel restart --role server
xltel sources
```

**정상 결과:** 등록한 endpoint마다 `up`과 Explore 링크가 나옵니다. 설정 검증과 실제 scrape 성공은 별도로 확인합니다.

이미 native job이 있다면 파일 수정 후 target만 갱신합니다.

```bash
xltel sources refresh
xltel sources
```

**정상 결과:** Prometheus file discovery가 기본 30초 안에 변경을 읽습니다. 그 후 endpoint의 실제 scrape 상태를 다시 확인합니다.

## 3. Verify

```bash
curl --fail --silent http://10.0.0.11:8000/metrics   | rg 'vllm:num_requests_waiting|vllm:kv_cache_usage_perc'
```

**정상 결과:** 배포가 제공하는 metric과 engine/model label이 보입니다. 없는 metric은 다른 signal의 0으로 채우지 않습니다.

## 볼 화면

- **Subsystems → Agent RL Stage Correlation:** Live rollout engine, vLLM latency, Ray orchestration.
- **Cross-Layer Signals:** 같은 시간의 workload·serving·shared resource 비교.
- **Sources Explore:** dashboard에 없는 native metric 조회.

## Troubleshooting

| 상태 | 확인할 것 |
| --- | --- |
| Target Down | 실제 port·endpoint·firewall·process 종료 여부 |
| Up + No data | Metric 이름/version·engine/model filter |
| 새 endpoint가 없음 | 첫 native job 추가 시 server 재시작 여부 |
| Stale | Scrape interval·producer 갱신·query 시간 |

## 다음

[느린 Step 조사](dashboards.md) · [KV / Storage](kv-storage.md) · [Native schema와 version별 상세](integration-reference.md#register-native-endpoints)
