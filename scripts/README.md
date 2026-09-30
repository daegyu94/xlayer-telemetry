# Checkout Scripts

기본 VERL 연결에는 `setup.sh`와 config 기반 `verl_local.sh`만 사용합니다.
나머지 스크립트는 개별 collector 실행, 선택 기능 또는 검증을 위한 entrypoint입니다.
Script는 Python package에 포함되지 않으므로 checkout을 유지합니다.

## Common Path

```bash
bash scripts/setup.sh
. .venv/bin/activate

bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" install
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" up
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" status
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" run
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" inspect
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" down
```

Config 준비와 VERL 명령 작성은 [VERL Quickstart](../docs/verl-quickstart.md)를 따릅니다.
`status`는 server·node process가 살아 있는지 확인하며, sample freshness나 subsystem 수집을 보증하지 않습니다.
두 process 모두 실행 중이면 exit code 0, 하나라도 실행 중이 아니면 1을 반환합니다.
`inspect`는 저장된 run 결과를 읽고, 실시간 수집 상태는 Grafana와 Prometheus Targets에서 확인합니다.
`up`·`down`은 같은 상태 directory에서 동시에 실행되지 않도록 `flock`을 사용합니다.
서로 다른 config라도 `TELEMETRY_HOME`이 같으면 같은 local stack을 가리킵니다.

## Script Selection

| Script | 목적 | 필요한 경우 / 실행 방식 |
| --- | --- | --- |
| `setup.sh` | Checkout의 `.venv`에 SDK를 editable 설치하고 CPU test 환경 준비 | 최초 준비; `PYTHON`으로 Python 3.10 이상 선택 |
| `check_tools.sh` | Telemetry 환경에서 PATH의 선택 도구 설치 여부 조회 | 설치 점검; 누락된 선택 도구를 자동 설치하지 않음 |
| `verl_local.sh` | 단일 host의 config 기반 install/up/status/run/inspect/down | 기본 VERL 연결; server·node를 background로 관리 |
| `install_telemetry_tools.sh` | Linux ARM64/x86_64 monitoring binary 다운로드 | 개별 host 준비; `TOOLS_DIR` 지정 |
| `run_telemetry.sh` | node/server/storage role 실행 | Multi-node 배포 또는 개별 process 조사; foreground |
| `run_verl_with_telemetry.sh` | 기존 VERL 명령에 file logger·bridge·선택 diagnostics 연결 | 기존 launcher에서 직접 wrapper 호출; foreground |
| `provision_dashboards.py` | 활성 datasource에 맞는 dashboard JSON 생성 | Server와 Compose 검증에서 자동 호출 |
| `validate_stack.sh` | 선택적 Compose stack 시작 후 target·backend 검증 | Docker/Compose 사용 시; 검증 후 service는 실행 상태 유지 |
| `local_llm.sh` | 선택적 Ollama 설치·시작·모델 다운로드·종료 | 명시적으로 local LLM 진단을 사용할 때; 기본 monitoring과 독립 |
| `run_profile.sh` | 작은 PyTorch DDP profile·collective 예제 실행 | CUDA PyTorch와 할당한 GPU/node가 있는 profiling 검증 |
| `run_nccl_baseline.sh` | 외부 nccl-tests binary를 MPI로 실행 | MPI·nccl-tests가 준비된 통신 baseline; PyTorch profile과 별개 |
| `capture_dashboard_demo.py` | 지정한 Grafana run/step 화면을 GIF로 기록 | 선택적 Playwright·ffmpeg; workload나 측정값을 생성하지 않음 |

`setup.sh`·`check_tools.sh`는 script 위치를 기준으로 checkout을 찾으므로 다른 directory에서도 절대 경로로 호출할 수 있습니다.
SDK 설치는 해당 checkout의 telemetry `.venv`에만 적용되며 VERL Python 환경은 변경하지 않습니다.
그 외 script의 상대 경로 옵션은 사용 예제의 working directory를 따르므로 기본적으로 저장소 루트에서 실행합니다.

Monitoring 설치에는 `curl`, `tar`, `unzip`, `sha256sum`이 필요합니다.
중단된 다운로드는 `.part`에 남고 완료된 archive만 교체됩니다.
`downloaded-archives.sha256`은 받은 파일의 digest 기록이며 upstream release checksum과 대조한 결과는 아닙니다.
Ollama 설치 helper는 별도로 공식 release checksum을 대조합니다.

## Detailed Guides

- [Monitoring / multi-node deployment](../docs/monitoring.md)
- [Local LLM diagnosis](../docs/local-llm.md)
- [Profiling / NCCL baseline](../docs/dashboards.md#practice-with-a-synthetic-profile)
- [Grafana recording](../docs/real-verl-demo.md#refresh-the-recording)
- [Examples 선택 안내](../examples/README.md)
