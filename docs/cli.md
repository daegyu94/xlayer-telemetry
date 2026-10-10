# xltel CLI

:::{container} xlayer-page-meta
**Reference** 명령·옵션·exit / artifact 계약
:::

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
재실행은 요청한 release URL과 기존 manifest의 SHA256이 모두 일치하는 archive를 재사용하며 architecture·version이 다르거나 손상되거나 검증 기록이 없는 archive는 다운로드합니다.
이는 local cache의 integrity 검사이며 publisher signature 검증은 아닙니다.
`xltel`만 실행하면 command 목록과 시작 순서를 보여 줍니다.

## Commands

![xltel의 setup·lifecycle·workload·source·config·clock command 그룹](figures/diagrams/cli-commands.svg)

| Command | 주요 option / 하위 command |
| --- | --- |
| `init` | 기존 config를 보존하며 초기 설정 생성 |
| `doctor`, `status` | `--json`, `--role all\|server\|node` |
| `doctor --correlation` | `--diagnostics-config FILE.json` 또는 `DIAGNOSTICS_CONFIG`; [관측 대상·clock 사전 검사](time-alignment.md#correlation-preflight) |
| `install-tools`, `up`, `down`, `restart` | `--role all\|server\|node` |
| `run` | `--mode auto\|sync\|async`, `--run-id ID`, `--output DIR`, `--node NAME`, `-- COMMAND` |
| `inspect` | `[RUN_ID \| RUN_DIR]`; `--execution-graph`, `--root-span TRACE/SPAN`은 [저장 SDK 관계 / trajectory path](performance-diagnosis.md) 조회 |
| `logs` | `[server\|node]`, `--follow`, `--lines N` |
| `sources` | `--json`, `refresh`, `threefs --window-seconds N` |
| `config` | `path`, `show`, `validate`, `migrate --output FILE.toml` |
| `cluster validate` | `--inventory FILE.toml/FILE.json`, `--live`, `--correlation`, `--json` |
| `cluster render` | `--inventory FILE`, `--output NEW_DIR`, `--json`; 기존 설정을 생성만 함 |
| `app` | `install`, `status`, `update`, `rollback`; [package·signing·restart 경계](app-deployment-reference.md#설치--상태--update) |
| `runs` | `list`, `compare RUN_A RUN_B`, `publish`; [검색·시간 filter·저장 artifact](run-comparison.md) |
| `completion` | `bash\|zsh\|fish` |
| `clock serve` | `--reference-id ID`, `--bind ADDRESS`, `--port PORT` |
| `clock calibrate` | `--url URL`, `--reference-id ID`, `--node NODE`, `--file FILE` |
| `clock status` | `--node NODE`, `--file FILE` |

`status`는 현재 system, `inspect`는 저장된 run 결과를 확인합니다.
`status`의 Start Here 링크는 cluster·node를, `run/inspect`의 Run Overview 링크는 Run을 선택한 상태로 Grafana를 엽니다.
`inspect`는 최근 CLI run 경로를 기억하므로 custom `--output` 결과도 찾을 수 있습니다.
새 CLI run의 링크는 manifest에 저장한 cluster·observer node를 사용하므로 현재 config를 바꿔도 실행 당시 문맥을 유지합니다.
이 문맥이 없는 기존 manifest는 현재 cluster 설정을 사용합니다.
`sources`는 run 없이 native endpoint를 조사하고 `sources threefs`는 설정한 ClickHouse의 shared-service window를 조회합니다.
Backend error나 source down은 nonzero, source 미설정은 정상적인 optional 상태입니다.

`cluster validate`는 설정 Error `2`, 확인할 Warning `1`, 정상 `0`을 반환합니다. Inventory만 검증할 때는 `init`이나 서비스 설치가 필요하지 않습니다. `--config` 또는 `XLAYER_CONFIG`를 지정하면 해당 runtime의 backend/Diagnosis 설정을 사용하되 cluster/target/topology/native source는 Inventory 입력을 검증합니다. [검사 범위](configuration.md#cluster-configuration-validation)와 [생성·배치 절차](multi-node.md#1-configure)를 구분해 확인합니다.

## Configuration

새 설치의 기본 config는 `~/.config/xlayer/config.toml`이며 `init`은 기존 파일을 덮어쓰지 않습니다.
기본 경로에 기존 `config.conf`가 있으면 그 파일을 계속 선택하므로 기존 설치가 자동으로 전환되지 않습니다.
`--config`나 `XLAYER_CONFIG`로 어느 형식이든 지정할 수 있습니다.

```bash
xltel config path
xltel config show
xltel config validate
xltel --config /absolute/path/verl-local.conf status
XLAYER_CONFIG=/absolute/path/verl-local.conf xltel status
```

| 확인할 항목 | 규칙 |
| --- | --- |
| Config 파일 선택 | `--config` → `XLAYER_CONFIG` → 기본 경로 |
| 알려진 설정 값 | Command option → 같은 이름의 environment → config → default |
| Bash path 계산 | 계산 결과를 명시한 설정으로 취급 |
| `config show` | Resolved JSON; workload argument 공개하지 않음 |
| CUDA·backend credential | Child environment로 전달; config 출력·lifecycle snapshot에서 제외 |
| `sources threefs` credential | TOML `[environment]` 또는 trusted Bash `export`; 같은 terminal 값 우선 |
| 3FS config 검증 | 조회 전 URL 문자열·database identifier·filter object·credential 환경 변수 이름 검증 |
| Optional 3FS | `threefs` 생략·`null`·빈 object는 미설정 유지 |

### TOML Configuration

TOML은 command를 실행하지 않는 declarative 설정입니다.
`[telemetry]`는 기존 uppercase 설정 이름, `[workload]`는 argument 배열, `[environment]`는 child process에 전달할 추가 환경 변수를 받습니다.
알려지지 않은 설정과 잘못된 type은 시작 전에 거부합니다.

```toml
[telemetry]
TELEMETRY_HOME = "~/telemetry"
RUN_ID = "auto"
ENABLE_LOGS = true
# ENABLE_GPU_METRICS = false
# EXECUTION_MODE = "async"
# TELEMETRY_SOURCES_FILE = "~/telemetry/config/native-sources.json"
# DIAGNOSTICS_CONFIG = "~/telemetry/config/diagnostics.json"
# PROMETHEUS_RETENTION = "7d"

[workload]
# command = ["/path/to/verl-env/bin/python", "-m", "verl.trainer.main_ppo"]

[environment]
# CUDA_VISIBLE_DEVICES = "0,1"
```

Path 설정의 `~`만 확장하며 `$HOME`, `$VARIABLE`, command substitution은 해석하지 않습니다.
Boolean은 `true`/`false` 또는 기존 `"0"`/`"1"` 문자열, age 설정은 양의 number 또는 문자열을 사용합니다.
추가 environment 값은 문자열이며 같은 이름의 현재 terminal 환경 변수가 우선합니다.
Python 3.11 이상은 `tomllib`, Python 3.10은 package 설치 시 포함되는 `tomli`로 읽습니다.

기존 Bash config는 **trusted executable 파일**입니다.
자신이 관리하는 assignment와 `VERL_COMMAND=(...)` 배열을 사용하고 service 시작이나 오래 걸리는 command를 넣지 않습니다.
다음 명령은 현재 resolved 설정과 command를 private TOML로 저장하고 원본을 유지합니다.

```bash
xltel --config ~/.config/xlayer/config.conf config migrate \
  --output ~/.config/xlayer/config.toml
xltel --config ~/.config/xlayer/config.toml config validate
export XLAYER_CONFIG="$HOME/.config/xlayer/config.toml"
```

Migration에는 현재 environment override와 계산된 absolute path가 반영됩니다.
원본의 `export` 값은 복사하지 않으므로 필요한 값은 terminal 환경 또는 `[environment]`에 옮깁니다.
Command argument도 저장되므로 생성된 파일의 `0600` 권한을 유지합니다.
이동할 host에서 경로를 재검토하고, 기존 `.conf`는 확인 후 직접 보관하거나 `XLAYER_CONFIG`로 TOML을 선택합니다.

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
`ENABLE_LOGS=1`·`TELEMETRY_SOURCES_FILE`·`DIAGNOSTICS_CONFIG`의 연결 조건은 [VERL Quickstart](verl-reference.md#add-sources-when-needed)에 있습니다.

## Health and Ownership

`status`는 PID·시작 시각·boot ID, backend HTTP 응답, Prometheus의 해당 cluster/node target과 sample age를 구분합니다.
GPU freshness는 collector textfile의 producer timestamp, `VERL saved snapshot`은 저장된 run의 trainer snapshot을 확인합니다.
Snapshot은 manifest의 run·observer와 내용의 identity가 일치하는 값 중 producer timestamp가 최신인 것을 사용합니다.
JSON의 `metrics.verl.scope=stored_artifact`와 `latest_run.workload.recorded_at`은 현재 process 상태와 구분하기 위한 정보입니다.
이는 Prometheus에 모든 sample이 도착했다는 보증이나 workload correctness 판정이 아닙니다.
종료된 run의 stale sample은 workload failure로 취급하지 않습니다.

| 상태·조회 | 해석과 다음 행동 |
| --- | --- |
| `endpoint_only` | 응답은 확인했지만 ownership은 미확인; 외부 Docker/systemd process는 `down`이 종료하지 않음 |
| 전체 healthy | 이 config의 managed server/node·필수 endpoint/target·활성 GPU freshness·등록 native scrape 성공 필요 |
| `status --role node` | 해당 node의 scrape/GPU·활성 Loki 검사; remote Grafana나 다른 engine 상태로 collector health를 바꾸지 않음 |
| Target 조회 | Collector/native는 한 번의 target 결과 공유 |
| Source `invalid_config` | 수정 안내와 core 상태 유지; `config validate`는 거부 |
| 3FS 조회 | `status`에서 ClickHouse 자동 query하지 않음; `sources threefs` 사용 |
| 3FS timeout | 각 distribution/counter 요청의 DNS·header·전체 body 제한; worker cleanup은 별도로 최대 0.4초 |
| 인증한 redirect | Scheme·host·port 변경을 거부; 최종 endpoint 지정 |
| Counter만 실패 | 수집한 distribution은 보존하고 `missing_sources`에 누락 기록 |

## Runtime Boundary

![xltel이 Python 조회 로직과 기존 lifecycle·VERL wrapper를 재사용하는 구조](figures/diagrams/cli-runtime.svg)

Lifecycle 변경은 기존 `flock`과 PID ownership 검사를 사용합니다.
Resolved config snapshot은 background child가 읽을 수 있도록 private `state/cli-configs/`에 보존합니다.
`up`이 이미 실행 중이면 새 stack을 만들지 않으며 부분 실행 상태에서는 없는 role만 추가합니다.
실패하면 이번 호출이 시작한 role만 정리하고 이전부터 실행 중인 role은 보존합니다.

### Host Roles for Multi-node Deployment

각 host에서 `--role server` 또는 `--role node`로 필요한 process만 관리할 수 있습니다.
생략한 `all`은 기존 단일 host 경로입니다.
같은 host의 두 role은 lifecycle lock과 PID ownership 검사를 공유하지만 한 role의 종료·재시작은 다른 role을 건드리지 않습니다.

Monitoring host의 `[telemetry]` 설정 예시입니다.

```toml
CLUSTER_NAME = "training-cluster"
TELEMETRY_TARGETS = "gpu-a=10.0.0.10,sandbox-a=10.0.1.10"
# Remote node가 log를 보내는 경우에만 private interface에 노출합니다.
# ENABLE_LOGS = true
# LOKI_LISTEN_ADDR = "10.0.0.20"
```

각 collector host는 같은 cluster 이름과 고유 `NODE_NAME`, local interface의 `NODE_ADDR`를 설정합니다.
Log 수집을 켠 remote node는 `LOKI_URL = "http://10.0.0.20:13100"`으로 monitoring host를 지정합니다.
GPU 없는 sandbox·storage node는 `ENABLE_GPU_METRICS = false`를 사용합니다.

```bash
# Monitoring host
xltel doctor --role server
xltel install-tools --role server
xltel up --role server
xltel status --role server

# Each collector host
xltel doctor --role node
xltel install-tools --role node
xltel up --role node
xltel status --role node
xltel down --role node
```

| 운영 항목 | 동작 |
| --- | --- |
| `status --role server` | Local collector/GPU를 요구하지 않고 등록한 전체 collector target 확인 |
| `status --role node` | Local ownership/freshness·configured endpoint·해당 node scrape 확인 |
| Process만 살아 있음 | Backend 또는 target이 없으면 degraded |
| Server bind | Prometheus/Grafana는 loopback; 조회 URL이 listen address를 바꾸지 않음 |
| 별도 검증 stack | `PROMETHEUS_PORT`·`GRAFANA_PORT`·`LOKI_PORT`·필요한 `ALLOY_PORT` 분리 |
| Default port | 19090·13000·13100; Alloy 12345 |
| URL / generated config | URL 미지정 시 local port에 맞춤; datasource·startup check·Loki도 같은 port 사용 |
| Remote query | Private SSH tunnel 등 별도 접근 경로 준비 |
| Collector / remote Loki | `:19100`·`:13100`은 신뢰된 private network에서 노출 |
| Grafana plugin | 기본 auto-download 비활성; 필요하면 `GF_PLUGINS_PREINSTALL_DISABLED=false`, 설치·종료 비용 확인 |
| Plugin 파일 | 공유 tools 대신 각 stack의 `state/server/grafana-plugins/` |

## Shell Completion

Completion은 command tree에서 생성하며 config·backend를 조회하지 않습니다.
명령을 출력할 뿐 shell 설정 파일은 자동 수정하지 않습니다.

```bash
# Bash, current session
source <(xltel completion bash)

# Zsh, after initializing completion
autoload -Uz compinit
compinit
source <(xltel completion zsh)
```

Fish에서는 `xltel completion fish | source`로 현재 session에 적용하거나 출력물을 `~/.config/fish/completions/xltel.fish`에 저장합니다.
Command·subcommand·option·enum 값과 config/output 경로를 완성하며 `run --` 이후 workload command의 completion은 제공하지 않습니다.

## Stopping and Preserved Data

`xltel down`은 이 config가 소유한 background service를 종료하고 PID record를 제거합니다.
Service는 CLI와 별도 session에서 실행되며 PID·start time·boot ID 검증과 lifecycle lock을 유지합니다.
Config, run 결과, diagnosis, backend 데이터와 log는 다음 조회와 재시작을 위해 보존합니다.
`lifecycle.lock`과 resolved config snapshot은 작은 운영 파일이며 `down`이 user 데이터를 삭제하지는 않습니다.
