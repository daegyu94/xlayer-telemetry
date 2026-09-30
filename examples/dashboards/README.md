# Docker Compose Monitoring Example

이미 실행 중인 XLayer node collector를 Docker의 Prometheus·Grafana에 연결하는 선택적 배포 예제입니다.
기본 도입 경로는 [Monitoring Guide](../../docs/monitoring.md)이며, 이 예제는 collector·VERL·vLLM·sandbox runtime을 설치하지 않습니다.

Grafana 화면은 상위 directory의 공통 JSON template에서 `scripts/provision_dashboards.py`가 생성합니다.
별도 resource dashboard 복사본을 유지하지 않으며 metrics-only 화면 5개를 사용합니다.
이 Compose는 Loki를 실행하지 않으므로 완료 step 목록·Bottleneck Summary·Timeline·Run Logs가 필요하면 [Loki를 포함한 기본 server](../../docs/monitoring.md#add-run-logs-with-loki)를 사용합니다.

## Configure Targets

저장소 루트에서 target 파일을 개인 directory로 복사합니다.
`nodes.json`의 `.example` 주소는 실행 가능한 주소가 아니므로 실제 collector 주소로 바꿉니다.

```bash
mkdir -p artifacts/compose-targets
cp examples/dashboards/targets/*.json artifacts/compose-targets/
```

Collector 하나당 별도 group으로 `cluster`와 `nodename`을 붙입니다.
Node 전체 지표에 고정 `run_id`를 붙이지 않습니다.
아래 collector는 기본 XLayer port `19100`을 사용합니다.

```json
[
  {
    "targets": ["gpu-0.internal:19100"],
    "labels": {"cluster": "training-cluster", "nodename": "gpu-0"}
  },
  {
    "targets": ["sandbox-0.internal:19100"],
    "labels": {"cluster": "training-cluster", "nodename": "sandbox-0"}
  }
]
```

주소는 **Prometheus container에서 접근 가능**해야 합니다.
Container 내부의 `127.0.0.1`은 host collector 주소가 아니며, collector가 host loopback에만 bind했다면 host의 관리 주소에서 접근하도록 먼저 설정합니다.
`gpus.json`과 `applications.json`은 별도로 분리한 XLayer 호환 exporter를 등록하는 경우에만 사용하고, 일반 node collector 연결에서는 빈 배열로 둡니다.
같은 endpoint를 여러 파일에 중복 등록하지 않습니다.
DCGM 원본 이름만 제공하는 exporter를 이 파일에 넣어도 XLayer GPU sampler 패널의 값으로 자동 변환되지는 않습니다.

`native.json`은 optional vLLM·Ray endpoint의 Prometheus file discovery 입력입니다.
[Native source 예제](../verl/native-sources.json)의 실제 주소를 수정하고 각 source의 `labels`에 같은 `cluster`를 추가한 뒤 기존 converter로 만들 수 있습니다.
등록할 native source가 없으면 빈 배열을 유지합니다.

```bash
python -m xlayer_telemetry.source_discovery \
  --input /path/to/native-sources.json \
  --output artifacts/compose-targets/native.json
```

`storage.json`은 별도 SMART exporter를 사용한 경우에만 `cluster`·`nodename`·`storage_system`과 실제 주소를 지정합니다.
SMART·native source가 없으면 해당 상세 패널의 N/A는 정상입니다.

## Start and Validate

필수 변경은 target 주소이며, port와 상태 경로는 기본값을 사용할 수 있습니다.
`validate_stack.sh`가 공통 dashboard를 생성하고 Compose를 시작한 뒤 실제 backend를 확인합니다.
GPU·application source가 없더라도 backend 검사는 가능하지만, 이 성공을 workload telemetry completeness로 해석하지 않습니다.

```bash
TARGET_DIR="$PWD/artifacts/compose-targets" \
  bash scripts/validate_stack.sh
```

Prometheus는 `http://127.0.0.1:9090`, Grafana는 `http://127.0.0.1:3000`입니다.
Grafana 로그인은 `admin`이며 password는 `GRAFANA_ADMIN_PASSWORD`로 설정합니다.
기본 예제 password `change-me`는 개인 검증용이므로 자신의 배포에서는 바꿉니다.
Port가 이미 사용 중이면 `PROMETHEUS_PORT`와 `GRAFANA_PORT`를 지정합니다.
모든 target의 up을 필수로 검사하려면 `REQUIRE_TARGETS_UP=1`을 추가합니다.

Service는 검증 후에도 실행됩니다.
종료할 때는 시작할 때 사용한 `COMPOSE_PROJECT_NAME`과 동일한 값을 유지합니다.

```bash
docker compose -f examples/dashboards/compose.yaml down
```

`down`은 named data volume을 보존합니다.
Dashboard 생성 경로는 `artifacts/compose-dashboards`, 검증 결과는 `artifacts/telemetry-validation/summary.json`이며 `DASHBOARDS_DIR`·`OUTPUT_DIR`로 바꿀 수 있습니다.
직접 `docker compose up`을 사용하는 경우에는 먼저 `python scripts/provision_dashboards.py --output artifacts/compose-dashboards`를 실행합니다.
Generated JSON은 Git에 추가하지 않습니다.
