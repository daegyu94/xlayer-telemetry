# Quickstart · 설치

**목표:** `xltel` CLI를 설치하고 자신의 workload에 맞는 다음 경로를 고릅니다.

## 얻는 것

- Monitoring·workload·저장된 Run을 관리하는 공식 `xltel` CLI.
- 기존 VERL·CUDA 환경과 분리된 telemetry Python 환경.

## 준비 조건

| 조건 | 확인 |
| --- | --- |
| Linux ARM64 또는 x86_64 | Managed stack은 Bash·`flock`·`setsid`를 사용합니다. |
| Python 3.10 이상, `venv` | `python3 --version` |
| Git, curl, tar, unzip | Monitoring 도구 설치에 필요합니다. |
| GPU 사용 시 NVIDIA driver | `nvidia-smi`가 동작해야 합니다. Demo에는 GPU가 필요 없습니다. |

## 1. Checkout과 Python 환경 준비

```bash
git clone https://github.com/daegyu94/xlayer-telemetry.git
cd xlayer-telemetry
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
xltel --help
```

**정상 결과:** help에 `up`, `run`, `inspect`, `sources`가 표시됩니다. 이 설치는 VERL·CUDA·monitoring binary를 설치하지 않습니다.

## 2. Config 생성과 검증

```bash
xltel init
xltel config path
xltel config validate
```

**정상 결과:** config 경로가 출력되고 `config validate`가 exit code `0`으로 끝납니다. `init`은 기존 설정을 덮어쓰지 않습니다.

```{admonition} 기존 설정
:class: note

새 설치의 기본 파일은 `~/.config/xlayer/config.toml`입니다. 기존 `config.conf`가 있으면 계속 사용할 수 있습니다. 선택·우선순위는 [Configuration](configuration.md)을 따릅니다.
```

## 3. 다음 경로 선택

| 상황 | 다음 guide | 성공 기준 |
| --- | --- | --- |
| GPU·VERL이 없음 | [Demo 실행](demo.md) | Grafana에서 synthetic Step 127 선택 |
| 기존 VERL 명령이 동작함 | [Connect VERL](verl-quickstart.md) | 첫 완료 step·stage artifact 확인 |
| Workload 없이 자원만 관측 | [GPU & Host](monitoring.md) | Collector target `up` |

## 문제가 생겼다면

| 상태 | 행동 |
| --- | --- |
| `xltel`을 찾을 수 없음 | 설치한 Python 환경을 활성화하고 `python -m pip install -e .` 재확인 |
| Config 검증 실패 | 오류에 표시된 key·type·path 수정 → 다시 validate |
| `doctor`에서 도구가 없음 | 선택한 guide의 config로 `xltel install-tools` 실행 |
| GPU 없는 환경에서 GPU 오류 | Demo config를 사용하거나 `ENABLE_GPU_METRICS = false` 설정 |

## 다음

[GPU 없이 Demo](demo.md) · [기존 VERL 연결](verl-quickstart.md) · [CLI Reference](cli.md)
