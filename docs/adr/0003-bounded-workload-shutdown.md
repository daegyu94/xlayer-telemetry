# ADR-0003: Bound workload shutdown with one monotonic cleanup process

- 상태: 승인
- 적용 WP: WP-07

## 결정

VERL wrapper는 기존과 같이 현재 소유한 process session에 TERM을 전달한다.
종료 유예와 KILL 확인은 한 Python 프로세스에서 `time.monotonic()`으로 5초와 2초의 deadline을 적용한다.
그룹의 살아 있는 구성원을 반복 확인하고 KILL 직전에 다시 확인하며, 소유권을 확인할 수 없으면 그룹 신호를 보내지 않는다.

## 이유

기존 Bash loop는 0.1초마다 새 Python을 시작하여 `/proc` 전체를 읽었다.
이번 격리 환경의 `/proc`에는 2개 프로세스만 보이며, 20회씩 3회 측정한 scan은 회당 0.202·0.220·0.216ms, Python 시작과 scan은 회당 18.958·19.008·19.220ms였다.
새 종료 helper에서 TERM을 무시하는 소유 세션의 cleanup은 3회 모두 5.001초였다.
이 측정은 실제 1,800개 프로세스 host의 지연을 검증하지 않는다.

## 대안

첫 scan의 PID 목록만 보존하면 종료 중 생성된 child와 재사용된 PGID를 놓칠 수 있다.
`kill -0`만으로는 현재 그룹 구성원의 소유권을 증명할 수 없어 사용하지 않는다.
Shell의 wall clock 기반 deadline은 시스템 시각 변경에 영향을 받아 사용하지 않는다.

## 검증

`tests/test_owned_shutdown.py`는 foreign marker와 읽을 수 없는 `/proc`, 종료 직전의 현재 소유권 재확인을 검사한다.
`tests/test_verl_wrapper.py`와 `tests/test_review_followup.py`는 정상 종료, SIGINT/SIGTERM, 종료 중 fork, 살아남은 child, workload exit code를 검사한다.
`tests/test_sidecar_shutdown.py`와 `tests/test_wrapper_job_ownership.py`는 별도 sidecar ownership을 검사한다.

## 전환

CLI·설정·artifact 형식 변경은 없다.
유예 만료 후 그룹 소유권 검사가 실패하면 강제 종료하지 않으며 wrapper는 원래 workload exit code를 유지한다.
