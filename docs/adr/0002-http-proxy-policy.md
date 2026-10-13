# ADR-0002: Backend HTTP proxy 선택

- 상태: 승인
- 적용 WP: WP-03

## 결정

모든 제품 HTTP caller는 요청마다 같은 proxy 정책을 적용한다.
`localhost`와 IPv4·IPv6 loopback, 명시적 `NO_PROXY` host·domain·port 및 literal IP CIDR 대상은 직접 연결한다.
그 밖의 대상은 사용자의 `HTTP_PROXY`·`HTTPS_PROXY`·`ALL_PROXY` 설정을 따른다.
Redirect의 새 목적지에도 이 정책을 다시 적용한다.

## 이유

기존 `urllib` 경로는 loopback을 proxy로 보낼 수 있고 `NO_PROXY`의 CIDR을 해석하지 않는다.
반대로 backend URL이 설정됐다는 이유만으로 proxy를 끄면 사용자가 설정한 원격 backend 연결이 깨진다.
Literal IP만 CIDR과 비교해 DNS 조회를 추가하지 않고, 기존 인증 redirect 차단과 transport 제한을 유지한다.

## 대안

모든 backend 요청에서 proxy를 비활성화하면 원격 proxy 사용자 계약을 잃는다.
DNS로 hostname을 CIDR에 대조하면 조회·지연·결과 변동이 생겨 요청 deadline과 선택 의미가 불명확해진다.

## 검증

`tests/test_http_proxy_policy.py`에서 실제 로컬 backend와 proxy, 주소·포트·CIDR 선택, redirect별 선택을 검사한다.
기존 `tests/test_authenticated_redirects.py`와 `tests/test_llm_transport_deadline.py`로 인증 정보, 응답 크기, partial body 및 deadline 경계를 검사한다.

## 전환

새 설정은 없다.
원격 backend에 proxy가 필요하면 기존 proxy 환경 변수를 유지하고, 우회가 필요하면 `NO_PROXY`에 host·domain·port 또는 literal IP CIDR을 지정한다.
