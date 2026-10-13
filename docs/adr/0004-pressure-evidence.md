# ADR-0004: Pressure 카드의 Step evidence 연결

- 상태: 승인
- 적용 WP: WP-08

## 결정

Pressure 카드의 다섯 신호는 진단 producer의 canonical signal과 연결한다.
선택한 Step의 같은 cluster, 구간, entity, observation scope, window statistic에서 나온 supporting evidence만 카드에 표시한다.
Evidence의 현재값과 baseline은 같은 단위로 변환한 뒤 변화율을 계산한다.
카드의 최신 sample은 구간 `max`나 `mean`과 다른 통계이므로 baseline 변화율의 현재값으로 쓰지 않는다.
단위를 알 수 없는 경우에도 entity와 구간이 일치한 evidence의 존재는 표시하고 수치 변화율은 Unknown으로 둔다.

## 이유

기존 RDMA 카드의 signal 문자열은 producer의 `rdma_transmit_wait_ticks_per_second`와 달라 evidence를 찾을 수 없었다.
또한 Grafana `percent`, `percentunit`, `suffix:ticks/s`와 producer의 `percent`, `ratio`, `ticks/s`를 문자열로 비교해 같은 관측을 끊었다.
최신 sample과 구간 통계의 이름이 같다는 이유로 baseline을 빼면 서로 다른 통계를 비교하게 된다.

## 대안

Panel의 unit 문자열을 producer 값으로 그대로 대조하는 방식은 Grafana 표시 형식 차이를 해결하지 못한다.
구간 통계를 최신 sample로 대신하는 방식은 관측 의미를 바꾸므로 선택하지 않았다.

## 검증

다섯 카드의 producer row 형태, percent/ratio와 ticks/s 변환, unit unknown, baseline zero, 다른 entity/scope/statistic, 오래된 sample과 window 불일치를 `grafana/xlayer-app/tests/distributed.test.ts`에서 확인한다.
`npm test`, `npm run typecheck`, `npm run build` 및 격리 Grafana/browser 경로의 결과는 WP-08 진행 상태에 기록한다.

## 전환

저장된 report 형식은 바꾸지 않는다.
이전 report에 unit이나 statistic이 없으면 알려진 canonical signal의 producer 계약으로 해석한다.
알 수 없는 unit의 수치 변화율은 표시하지 않는다.
