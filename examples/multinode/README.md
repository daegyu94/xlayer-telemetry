# CPU deployment contracts

GPU 없이 서로 다른 localhost·PID·파일 경로의 Monitoring + 두 collector container를 검증합니다. 기존 `cluster render`, `xltel up/down --role node`, SDK, Node Exporter, Alloy, Prometheus, Loki와 Diagnosis Engine을 사용합니다. 새로운 운영 서비스나 exporter가 아닙니다.

```bash
docker build --tag xltel-cpu-contract:local \
  --file examples/multinode/cpu-contract.Dockerfile examples/multinode
PYTHONPATH=. python examples/multinode/validate_cpu_contracts.py \
  --tools /path/to/installed-monitoring-tools --image xltel-cpu-contract:local \
  --output /tmp/new-xltel-cpu-contract
```

Tools에는 Node Exporter 1.9.1, Prometheus 3.5.0, Loki와 Alloy의 기존 executable이 필요합니다. Output은 새 directory이며 생성한 container·network만 정리하고 작은 JSON·log와 test state는 해당 경로에 남깁니다. 확인 후 그 directory만 삭제합니다. Monitoring은 test network 안에서만 listener를 열고 host port는 loopback에 한정하며 운영 bind·인증 정책은 변경하지 않습니다.

| 검증 | 범위 |
| --- | --- |
| Config / identity | 같은 worker 0·GPU 0 metadata, 서로 다른 Node의 SDK 값과 실제 collector relabel 분리 |
| Node status | 생성된 remote Prometheus URL로 실제 target URI 확인 |
| Logs / spans | 실제 SDK JSONL → Alloy → Loki의 Node / Run 분리 |
| Raw native metric | HTTP exposition → native source 등록 → 실제 Prometheus scrape/relabel → query / diagnosis |
| 부분 장애 | UP이지만 metric 누락, 부분 Replica, 없는 histogram, HTTP 지연·503·sample limit, counter reset |
| Node 전환 | Node-b stop/restart 동안 Node-a 유지 |
| Collector child 종료 | 기존 fail-fast 정책대로 해당 node-role sibling 종료; 다른 Node와 외부 native source 유지 |
| Clock | 검증되지 않은 multi-node clock이면 raw 관측 유지·후보 보류 |

Native fixture는 [vLLM metric 정의](https://github.com/vllm-project/vllm/blob/2d69083c4a09dfeb17d761ae96d2e571a628309c/vllm/v1/metrics/loggers.py)의 waiting gauge·KV usage ratio와 Prometheus counter exposition 형식을 재현합니다. 수치와 model label은 synthetic이며 실제 vLLM을 실행하거나 특정 버전의 모든 metric을 검증하는 테스트가 아닙니다. Fixture에는 XLayer job/node/cluster/telemetry_source label을 넣지 않으며 실제 등록·scrape 단계가 붙여야 합니다.

같은 host kernel의 container는 물리 multi-node clock·GPU·RDMA·3FS 성능 검증이 아닙니다. Ray·Mooncake의 운영 버전 전체 exposition, 실제 source upgrade, 장시간 workload overhead와 operation attribution은 별도 실환경 검증으로 남깁니다. [KVM 검증](validate_vms.py)은 독립 guest kernel을 추가로 검사하는 기존 경로입니다.
