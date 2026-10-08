# Demo 실행 · GPU 없이 조사 흐름 익히기

:::{container} xlayer-page-meta
**Tutorial** GPU 없이 수집 → Step → Evidence → Grafana 확인
:::

**목표:** 별도 synthetic stack에서 Run → Step → Baseline → Evidence → Timeline을 따라갑니다.

## 얻는 것

- VERL·vLLM·Ray 없이 채워지는 metric graph.
- Synthetic Step 127, candidate, tool/sandbox span과 lifecycle event.
- 실제 Prometheus·Grafana·Loki·Alloy를 사용하는 조사 경로.

```{admonition} Synthetic
:class: important

실제 GPU·VERL·3FS 성능 측정이 아닙니다. Live resource curve와 precomputed diagnosis는 독립적인 예제입니다. 3FS evidence는 fixture이며 ClickHouse에 query하지 않습니다.
```

## 준비 조건

- [Quickstart](quickstart.md)의 CLI 설치 완료.
- 저장소 루트와 활성화한 telemetry Python 환경.
- Demo port `23000`, `29090`, `23100`, `22345`, `29110` 사용 가능.

## 1. 전용 Config 준비

```bash
mkdir -p "$HOME/.config/xlayer"
cp -n examples/synthetic-demo.toml "$HOME/.config/xlayer/demo.toml"
export XLAYER_CONFIG="$HOME/.config/xlayer/demo.toml"
xltel config validate
```

**정상 결과:** validate가 exit code `0`으로 끝납니다. 기존 `demo.toml`이 있다면 파일을 읽고 의도한 `TELEMETRY_HOME`·`TOOLS_DIR`·port인지 확인합니다.

## 2. 도구 설치와 시작

```bash
xltel doctor
# 도구가 없는 경우에만 설치합니다.
xltel install-tools
DEMO_LIVE=1 DEMO_PORT=29110 xltel up
xltel status
```

**정상 결과:** `Monitoring ready`와 Grafana 주소가 출력됩니다. `status`에서 Prometheus·Grafana·Loki가 healthy이고 collector target이 up입니다. Rate는 최소 두 scrape 후 나타납니다.

## 3. 완료 Step과 Evidence 생성

Metric 수집 시작 후 약 30초가 지나면 새 directory에 fixture를 생성합니다.

```bash
python -m xlayer_telemetry.demos.diagnosis   --output "$HOME/telemetry-demo/runs/verl-agent-demo"   --run-id verl-agent-demo --node gpu-node-0
xltel inspect verl-agent-demo
```

**정상 결과:** `Synthetic step 127 with 3 candidates`가 출력됩니다. Inspect에 Step 127과 `storage_queue_saturation`이 나타납니다. Output이 이미 있으면 새 demo state·run directory를 선택합니다.

## 4. Grafana에서 확인

1. [Run Overview](http://127.0.0.1:23000/d/telemetry-overview)를 엽니다.
2. Cluster `demo-b300`, Run `verl-agent-demo`, Resource `All`을 선택합니다.
3. 완료 step 목록의 **127 · Duration 18.4 s**를 누릅니다.
4. **What changed? → Candidate → Evidence**를 읽습니다.
5. **Cross-Layer Timeline → Subsystems → Data & Storage**로 이동합니다.

**정상 결과:** selected record·observer·시간이 유지됩니다. Shared metric은 Run 사용량으로 표시되지 않으며, `synthetic` origin을 확인할 수 있습니다.

## 5. 종료

```bash
xltel down
unset XLAYER_CONFIG
```

**정상 결과:** `Monitoring stopped`가 출력됩니다. Config·Run·log artifact는 남습니다.

## 문제가 생겼다면

| 상태 | 행동 |
| --- | --- |
| Port 사용 중 | Demo config의 port와 `DEMO_PORT`를 함께 변경 |
| Rate만 비어 있음 | 두 번 이상 scrape 후 다시 확인 |
| 완료 step·candidate가 없음 | Fixture path·Alloy·Loki·시간 범위 확인 |
| Logs가 없음 | Log directory `verl-agent-demo`, Run context `verl-agent-demo` 확인 |
| Source가 N/A | Fixture coverage와 필터 확인; N/A를 0으로 해석하지 않기 |

## 다음

[느린 Step 조사](dashboards.md) · [기존 VERL 연결](verl-quickstart.md) · [Fixture coverage / 상세 검증](monitoring-reference.md#check-dashboard-coverage)
