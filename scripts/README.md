# Checkout Scripts

기본 사용자 interface는 `xltel`입니다.
설치·config·health·run 조회는 [CLI Reference](../docs/cli.md), VERL 연결은 [Quickstart](../docs/verl-quickstart.md)를 따릅니다.
아래 script는 기존 사용자와 role별 배포를 위한 advanced/internal entrypoint로 유지합니다.
CLI에 필요한 launcher와 dashboard는 package에도 포함됩니다.
Host별 server/collector lifecycle도 `xltel up|down|status --role server|node`로 관리할 수 있습니다.
TOML은 CLI가 읽고 내부 Bash snapshot으로 전달하므로 아래 script를 직접 사용할 때는 기존 Bash config 형식을 유지합니다.

## Compatibility Path

```bash
bash scripts/verl_local.sh --config /absolute/path/verl-local.conf up
bash scripts/verl_local.sh --config /absolute/path/verl-local.conf status
bash scripts/verl_local.sh --config /absolute/path/verl-local.conf down
```

기존 script의 `status`는 process 생존만 확인하며, backend·freshness 확인은 `xltel status`를 사용합니다.
기존 config는 `xltel --config FILE ...`로 재사용합니다.
Lifecycle은 같은 `flock`·PID 시작 시각·boot ID 검사와 process cleanup을 공유합니다.
서로 다른 config라도 `TELEMETRY_HOME`이 같으면 같은 local stack을 가리킵니다.

## Script Selection

| Script | 목적 | 필요한 경우 / 실행 방식 |
| --- | --- | --- |
| `setup.sh` | Checkout의 `.venv`에 SDK를 editable 설치하고 CPU test 환경 준비 | 최초 준비; `PYTHON`으로 Python 3.10 이상 선택 |
| `check_tools.sh` | Telemetry 환경에서 PATH의 선택 도구 설치 여부 조회 | 설치 점검; 누락된 선택 도구를 자동 설치하지 않음 |
| `verl_local.sh` | 단일 host의 config 기반 install/up/status/run/inspect/down | CLI가 재사용하는 local lifecycle; server·node를 background로 관리 |
| `install_telemetry_tools.sh` | Linux ARM64/x86_64 monitoring binary 다운로드 | 개별 host 준비; `TOOLS_DIR` 지정 |
| `run_telemetry.sh` | node/server/storage role 실행 | Multi-node 배포 또는 개별 process 조사; foreground |
| `run_verl_with_telemetry.sh` | 기존 VERL 명령에 file logger·bridge·선택 diagnostics 연결 | 기존 launcher에서 직접 wrapper 호출; foreground |
| `provision_dashboards.py` | 활성 datasource에 맞는 dashboard JSON 생성 | Server와 Compose 검증에서 자동 호출 |
| `validate_stack.sh` | 선택적 Compose stack 시작 후 target·backend 검증 | Docker/Compose 사용 시; 검증 후 service는 실행 상태 유지 |
| `local_llm.sh` | 선택적 Ollama 설치·시작·모델 다운로드·종료 | 명시적으로 local LLM 진단을 사용할 때; 기본 monitoring과 독립 |
| `run_profile.sh` | 작은 PyTorch DDP profile·collective 예제 실행 | CUDA PyTorch와 할당한 GPU/node가 있는 profiling 검증 |
| `run_nccl_baseline.sh` | 외부 nccl-tests binary를 MPI로 실행 | MPI·nccl-tests가 준비된 통신 baseline; PyTorch profile과 별개 |
| `docs.sh` | 선택적 문서 환경 설치·strict build·local preview | Python 3.11 이상; telemetry 환경과 독립 |
| `render_diagrams.py` | D2 원본을 SVG로 생성하거나 변경 누락 검사 | 문서 그림 변경 시; pinned D2 설치·checksum 검증 지원 |
| `docs-browser/` | 문서 그림·caption·확대 링크의 browser regression | 문서 변경 시 사용하는 선택적 Playwright 검사 |
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

## Documentation Website

문서는 [GitHub Pages](https://daegyu94.github.io/xlayer-telemetry/)에서 읽습니다.
`docs/*.md`가 GitHub와 웹사이트의 공통 원본이며 Sphinx·MyST·Furo로 빌드합니다.
문서 build 환경만 Python 3.11 이상이 필요하고 runtime SDK의 Python 3.10 지원은 유지합니다.

```bash
bash scripts/docs.sh install
bash scripts/docs.sh serve
```

`http://127.0.0.1:18080`에서 확인하고 `Ctrl+C`로 종료합니다.
수정 후 명령을 다시 실행하면 빌드하며 preview는 자동 reload하지 않습니다.
`DOCS_PORT`로 port, `DOCS_ENV`로 가상환경 경로를 바꿀 수 있습니다.

`bash scripts/docs.sh build`는 broken internal reference도 실패로 처리합니다.
`.github/workflows/docs.yml`은 PR에서 빌드를 검사하고 main의 문서 변경을 GitHub Pages에 배포합니다.
처음 배포할 때 저장소 `Settings > Pages > Source`를 `GitHub Actions`로 설정합니다.

### Documentation Diagrams

`docs/diagrams/*.d2`가 그림의 원본이고 `docs/figures/diagrams/*.svg`가 GitHub·문서 사이트에서 사용하는 생성 결과입니다.
공통 색상·글꼴은 `_style.d2`에 있으며 D2 0.9.0과 ELK layout을 고정해 재생성합니다.
SVG는 alt text와 title·description을 포함합니다.
모바일에서는 전체 그림을 화면 폭에 맞추며 그림 자체를 눌러 원본 SVG를 엽니다.
Keyboard로 링크를 선택해 Enter로 열 수도 있습니다.
Alt text는 caption과 accessible link 이름에 사용하며, D2 편집 위치는 이 절에서만 안내합니다.
Alt text는 그림의 목적을 한 문장으로 설명하며, 상세 설명은 본문에 둡니다.

```bash
python scripts/render_diagrams.py --install
# Edit docs/diagrams/*.d2, then regenerate:
python scripts/render_diagrams.py
python scripts/render_diagrams.py --check
```

`--install`은 공식 release의 고정 SHA256을 확인하고 `artifacts/docs-tools/`에만 D2를 설치합니다.
Linux/macOS의 x86_64·ARM64를 지원하며 다른 환경은 같은 version을 설치한 뒤 `--d2 PATH`로 지정합니다.
D2 원본과 SVG를 함께 commit하며 docs CI가 재생성 결과와 일치하는지 확인합니다.
일반 문서 열람·Sphinx build·telemetry 실행에는 D2가 필요하지 않습니다.
생성물은 `artifacts/docs-site/`에 두며 Git에 추가하지 않습니다.

문서 사이트의 모바일 표시·caption·keyboard 확대·SVG geometry는 Playwright로 검사합니다.

```bash
npm ci --prefix scripts/docs-browser
./scripts/docs-browser/node_modules/.bin/playwright install chromium
bash scripts/docs.sh build
XLAYER_DOCS_SITE=artifacts/docs-site npm test --prefix scripts/docs-browser
```

기존 Chrome으로 검사하려면 `CHROME_PATH`를 지정합니다.
Node·Playwright는 browser 검사에만 필요하며 문서 build·열람이나 D2 생성에는 필요하지 않습니다.
GitHub Markdown과 GitHub Pages는 같은 D2 SVG를 표시합니다.
