# ADR-0001: Sandbox device counter의 첫 관측과 누락 구분

- 상태: 승인
- 적용 WP: WP-01

## 결정

읽을 수 있는 빈 `io.stat`은 해당 device의 시작 counter가 0인 기준이다.
이후 처음 등장한 device의 counter는 0에서 증가한 delta로 기록하며, 값 0도 실측값으로 보존한다.
시작 또는 종료 시 `io.stat`을 읽을 수 없으면 device delta를 만들지 않고 `counter_status=unavailable`로 기록한다.
같은 device의 counter가 감소하면 감소한 항목의 delta는 생략하고 `counter_status=reset`과 `reset_major_minors`를 기록한다.
유효한 항목이 하나도 남지 않은 device는 `io_devices`에 넣지 않으며, 해당 device의 mapping은 `unknown`이다.

## 이유

기존 수정은 읽을 수 있는 빈 기준 뒤 첫 I/O를 보존했지만, 모든 counter가 감소한 device를 `io_devices[device]={}`로 기록해 `observed`로 분류했다.
이는 reset 또는 읽기 실패를 실측 0과 혼동하게 한다.
빈 기준, 읽기 실패, counter reset은 서로 다른 증거 상태이다.

## 대안

처음 등장한 device를 버리면 실제 I/O를 누락한다.
읽기 실패나 counter reset을 0으로 처리하면 측정하지 않은 delta가 만들어진다.

## 검증

`tests/test_sandbox_observability.py`에서 readable-empty, unreadable baseline/final sample, reset의 event와 `device_window` 조회를 검사한다.
`tests/test_operational_regressions.py`에서 첫 device의 원래 producer 경로를 검사한다.

## 전환

기존 `io_devices={}` artifact는 계속 읽는다.
`device_mapping` 정보가 없는 빈 mapping은 읽기 실패 여부를 알 수 없으므로 조회에서 `unknown`으로 해석한다.
`reset_major_minors`는 추가 필드이며 기존 artifact에 없으면 reset 기록이 없는 것으로 해석한다.
사용자의 설정 변경은 필요하지 않다.
