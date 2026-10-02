# xltel CLI

`xltel`은 monitoring lifecycle, workload wrapper와 결과 조회를 위한 공식 CLI입니다.
`up`은 server·node collector를 시작하고, `run`은 기존 VERL 명령에 telemetry를 붙입니다.
`down`은 이 config로 시작한 monitoring process만 종료하며 VERL·Ray·vLLM workload를 종료하지 않습니다.

## Basic Workflow

Telemetry Python 환경에 package를 설치하면 console command가 생깁니다.
VERL은 기존 training 환경의 Python이나 launcher로 실행합니다.

```bash
python -m pip install -e .
xltel init
xltel doctor
xltel install-tools
xltel up
xltel status
xltel run -- /path/to/verl-env/bin/python -m verl.trainer.main_ppo ...
xltel inspect
xltel down
```

`doctor`는 설치 전 누락된 binary와 해결 명령을 표시합니다.
`install-tools`는 최초 한 번 필요하며 NVIDIA driver·VERL·3FS는 설치하지 않습니다.
`xltel`만 실행하면 command 목록과 시작 순서를 보여 줍니다.

## Commands

```text
xltel
  +-- init
  +-- doctor [--json]
  +-- install-tools
  +-- up / down / restart
  +-- status [--json]
  +-- run [--mode auto|sync|async] [--run-id ID] [--output DIR] [--node NAME] -- COMMAND
  +-- inspect [RUN_ID | RUN_DIR]
  +-- logs [server|node] [--follow] [--lines N]
  +-- sources [--json]
  |     +-- refresh
  |     +-- threefs [--window-seconds N]
  +-- config
        +-- path / show / validate
```

`status`는 현재 system, `inspect`는 저장된 run 결과를 확인합니다.
`inspect`는 최근 CLI run 경로를 기억하므로 custom `--output` 결과도 찾을 수 있습니다.
`sources`는 run 없이 native endpoint를 조사하고 `sources threefs`는 설정한 ClickHouse의 shared-service window를 조회합니다.
Backend error나 source down은 nonzero, source 미설정은 정상적인 optional 상태입니다.

## Configuration

기본 config는 `~/.config/xlayer/config.conf`이며 `init`은 기존 파일을 덮어쓰지 않습니다.
기존 `verl-local.conf`도 지정할 수 있습니다.

```bash
xltel config path
xltel config show
xltel config validate
xltel --config /absolute/path/verl-local.conf status
XLAYER_CONFIG=/absolute/path/verl-local.conf xltel status
```

Config 선택은 `--config` > `XLAYER_CONFIG` > 기본 경로 순서입니다.
알려진 설정 값은 command option > 같은 이름의 environment variable > config > 기본값 순서입니다.
Bash config 안에서 계산한 경로는 그 계산 결과를 명시한 설정으로 취급합니다.
`config show`는 resolved setting을 JSON으로 보여 주며 workload argument는 공개하지 않습니다.
Config가 export한 CUDA 설정·backend credential은 child environment로 전달하며 config 출력과 저장된 lifecycle snapshot에는 포함하지 않습니다.

Config는 **trusted Bash 파일**이며 읽을 때 실행됩니다.
자신이 관리하는 assignment와 `VERL_COMMAND=(...)` 배열을 사용하고 service 시작이나 오래 걸리는 command를 넣지 않습니다.
TOML과 shell completion은 아직 제공하지 않습니다.

새 config의 도구·state·run parent는 `$HOME/telemetry/` 아래입니다.
`RUN_ID=auto`이면 실행마다 고유 ID를 만들며 collector가 같은 run parent를 계속 읽습니다.
기존 config에 명시한 `RUN_ID`·`RUN_ROOT`는 유지하고, 고정 ID를 사용하면 새 run마다 변경해야 합니다.
기존 Bash config의 named run 기본 경로인 `$HOME/telemetry-runs/<RUN_ID>`도 유지합니다.

```bash
xltel run --mode async --run-id exp-001 -- /path/to/verl-env/bin/python ...
xltel inspect exp-001
```

`--output`이 `TELEMETRY_RUNS_ROOT` 밖이면 artifact는 저장되지만 현재 collector가 자동으로 읽지는 않습니다.
Collector의 run parent를 바꾸면 `restart`가 필요합니다.
`ENABLE_LOGS=1`·`TELEMETRY_SOURCES_FILE`·`DIAGNOSTICS_CONFIG`의 연결 조건은 [VERL Quickstart](verl-quickstart.md#add-sources-when-needed)에 있습니다.

## Health and Ownership

`status`는 PID·시작 시각·boot ID, backend HTTP 응답, Prometheus의 해당 cluster/node target과 sample age를 구분합니다.
GPU freshness는 collector textfile의 producer timestamp, VERL freshness는 최근 저장된 trainer snapshot을 확인합니다.
이는 Prometheus에 모든 sample이 도착했다는 보증이나 workload correctness 판정이 아닙니다.
종료된 run의 stale sample은 workload failure로 취급하지 않습니다.

`endpoint_only`는 endpoint를 조회했지만 process ownership은 확인하지 않았다는 뜻입니다.
Docker·systemd로 따로 시작한 server가 응답하더라도 `xltel down`은 이를 종료하지 않습니다.
`status`의 전체 healthy 상태에는 이 config의 managed server·node, 필수 endpoint·target, 활성 GPU의 fresh sample과 등록된 native source의 scrape 성공이 필요합니다.
3FS는 `sources threefs`로 직접 조회하며 `status`는 ClickHouse query를 자동 실행하지 않습니다.

```bash
xltel status --json
xltel doctor --json
xltel logs server --follow
```

JSON은 stdout, 오류 안내는 stderr로 나갑니다.
일반 명령은 성공·healthy일 때 0, unhealthy/runtime failure일 때 1, 사용법·config error일 때 2를 반환합니다.
`run`은 이 구분보다 실제 workload exit code를 우선하며 SIGINT/SIGTERM cleanup도 기존 wrapper를 사용합니다.

## Runtime Boundary

```text
xltel
  +-- Python config / health / artifact and source inspection
  +-- existing local lifecycle launcher
  |     +-- server: Prometheus / Grafana / optional Loki
  |     +-- node: GPU / Node Exporter / textfile / optional Alloy
  +-- existing VERL wrapper
        +-- original workload
        +-- bridge / manifest / health / optional diagnostics
```

Lifecycle 변경은 기존 `flock`과 PID ownership 검사를 사용합니다.
Resolved config snapshot은 background child가 읽을 수 있도록 private `state/cli-configs/`에 보존합니다.
`up`이 이미 실행 중이면 새 stack을 만들지 않으며 부분 실행 상태에서는 `status`·`logs` 확인 후 `restart`합니다.

`xltel`은 Linux 단일 host 편의 interface이며 multi-node 배포에는 [Monitoring Guide](monitoring.md#expand-to-multiple-nodes)의 role별 script와 target 설정을 사용합니다.
기존 Bash script는 계속 동작하고 [Scripts](https://github.com/daegyu94/xlayer-telemetry/blob/main/scripts/README.md)에 advanced interface를 정리했습니다.
일반 wheel 설치에도 CLI가 사용하는 launcher·dashboard를 포함하며 synthetic demo와 profiling 예제는 checkout에서 사용합니다.
