# XLayer Telemetry

VERL과 vLLM·Ray·sandbox·GPU·network·storage의 telemetry를 **Collect → Correlate → Diagnose**합니다.
Run·step·phase를 기준으로 느린 구간의 bottleneck candidate와 supporting·counter·missing evidence를 조사합니다.

Prometheus·Grafana·Loki와 기존 exporter를 활용하며, 저수준 분석은 PyTorch Profiler·Nsight로 이어집니다.
공유 자원의 동시 변화는 correlation이며 특정 run의 사용량이나 인과관계를 뜻하지 않습니다.
현재 지원 기능과 제약은 [README의 What Works Today](https://github.com/daegyu94/xlayer-telemetry#what-works-today), 수집 조건은 [Metrics / Scope](metrics.md#what-is-actually-collected)에서 확인합니다.
적용 전 확인할 한계와 보완 방법은 [Operating Limits](architecture.md#failure-boundaries-and-operating-limits)에 모았습니다.

## Start Here

| 순서 | 할 일 | 확인할 결과 |
| --- | --- | --- |
| 1 | [Checkout 준비](#prepare-a-checkout) → [Synthetic demo](monitoring.md#try-the-demo) | Exporter target이 up |
| 2 | [기존 VERL 명령 연결](verl-quickstart.md) | 첫 완료 step과 GPU·host metric |
| 3 | [필요한 source 추가](agent-rl.md#choose-the-next-source) | vLLM·Ray·Loki·storage의 실제 evidence |
| 4 | [느린 구간 조사](diagnosis.md#investigation-workflow) | Candidate → Evidence → Timeline → Deep Dive |

이미 VERL을 실행할 수 있다면 2단계부터 시작합니다.
용어는 [Metric·Event·Span과 Scope](diagnosis.md#what-the-signals-mean)에서 확인합니다.
SDK 직접 계측은 [Application Metrics](application-metrics.md), 현재 실환경 검증 범위와 GIF는 [Real VERL Demo](real-verl-demo.md)에 있습니다.

## Prepare a Checkout

Python 3.10 이상과 `venv`가 필요합니다.

```bash
git clone https://github.com/daegyu94/xlayer-telemetry.git
cd xlayer-telemetry
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
xltel init
```

`xltel`은 공식 운영 CLI입니다.
`doctor` → `install-tools` → `up` → `run` → `inspect` → `down` 순서로 연결합니다.
VERL·CUDA·monitoring 도구는 별도 환경을 사용하며 [VERL 연결 가이드](verl-quickstart.md)에서 이어갑니다.
`xltel`은 저장소 밖에서도 사용하며, advanced script 예제는 저장소 루트를 기준으로 합니다.

```{toctree}
:hidden:
:caption: Start Here

Monitoring / Demo <monitoring>
VERL 연결 <verl-quickstart>
CLI Reference <cli>
```

```{toctree}
:hidden:
:caption: Collect

Subsystem 연결 / Sandbox <agent-rl>
Application SDK <application-metrics>
```

```{toctree}
:hidden:
:caption: Correlate & Diagnose

Grafana / Investigation <dashboards>
Diagnosis / Baseline <diagnosis>
Optional Local LLM <local-llm>
```

```{toctree}
:hidden:
:caption: Reference

Architecture / Design <architecture>
Metrics / Scope <metrics>
Real VERL Demo <real-verl-demo>
검증 기록 <validation/README>
Grafana UX Review <grafana-ui-ux-review>
```
