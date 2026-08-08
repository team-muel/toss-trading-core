# Toss API Key And Adapter Plan

## Purpose

이 문서는 Toss 연결과 broker adapter 계약만 다룹니다. Toss가 제공하는 전체 기능 목록은 `docs/09_toss_official_api_coverage.md`를 기준으로 합니다.

## Secret Handling

실제 키 값은 아래 위치에 두지 않습니다.

- Git 저장소
- 문서
- 코드 상수
- 로그
- 스크린샷

로컬 단일 사용자 개발에서는 Git에 올라가지 않는 `.env`를 임시로 사용할 수 있습니다. 운영 환경은 OS credential manager, KMS, Vault, Secrets Manager 같은 secret store를 사용합니다.

표준 환경 변수:

```text
TOSS_BROKER_BASE_URL
TOSS_CLIENT_ID
TOSS_CLIENT_SECRET
TOSS_ACCOUNT_SEQ
TOSS_API_ENV
```

`TOSS_BROKER_BASE_URL` 기본값은 `https://openapi.tossinvest.com`입니다.

## Adapter Contract

| Internal Interface | Official Endpoint | Required Behavior |
| --- | --- | --- |
| `auth.refresh()` | `POST /oauth2/token` | token 발급, 만료 전 갱신 |
| `accounts.list()` | `GET /api/v1/accounts` | `accountSeq` 확인 |
| `positions.list()` | `GET /api/v1/holdings` | 보유수량, 평균단가, 평가금액 수집 |
| `orders.submit()` | `POST /api/v1/orders` | `clientOrderId` 필수, raw request/response 저장 |
| `orders.modify()` | `POST /api/v1/orders/{orderId}/modify` | review 상태 처리 |
| `orders.cancel()` | `POST /api/v1/orders/{orderId}/cancel` | review 상태 처리 |
| `orders.list_open()` | `GET /api/v1/orders` | OPEN 주문 대사 |
| `orders.list_closed()` | `GET /api/v1/orders` | OpenAPI 1.2.13 계약에 따라 7일 중첩 window로 정기 대사 |
| `orders.get()` | `GET /api/v1/orders/{orderId}` | execution 누적 snapshot 생성 |
| `buying_power.get()` | `GET /api/v1/buying-power` | `cashBuyingPower`를 broker constraint로 저장 |
| `sellable_quantity.get()` | `GET /api/v1/sellable-quantity` | 매도 가능 수량 gate |
| `commissions.get()` | `GET /api/v1/commissions` | 비용 모델과 실비 비교 |

## Idempotency Rule

모든 주문 생성은 `clientOrderId`가 필수입니다.

공식 문서상 `clientOrderId`는 멱등성 키이며 동일 값 재요청은 일정 시간 동안 이전 주문 결과를 재반환합니다. 내부 정책은 더 엄격합니다. 한번 사용한 `clientOrderId`는 유효기간이 지나도 재사용하지 않습니다.

타임아웃 또는 네트워크 오류가 발생했을 때 같은 주문을 즉시 새 ID로 재전송하지 않습니다.

허용 순서:

1. 같은 `clientOrderId`로 결과 재조회 또는 재요청
2. OPEN 주문 조회와 같은 `clientOrderId` 결과 확인
3. 주문 상세 조회
4. 상태 확정 후 새 의사결정과 새 `clientOrderId` 발급

상태가 확인되기 전 새 ID로 같은 주문을 제출하는 것은 중복 주문 위험으로 간주합니다.

## Order Status Policy

Terminal status:

- `FILLED`
- `REJECTED`
- `CANCELED`
- `REPLACED`

Review status:

- `CANCEL_REJECTED`
- `REPLACE_REJECTED`

Review status는 terminal로 처리하지 않습니다. 원주문을 다시 조회해 effective state를 확정해야 합니다.

Unknown enum:

- 저장
- 신규 주문 중단
- 수동 검토 또는 adapter update

## Polling Policy

Toss 주문 상태는 REST polling으로 관리합니다.

- create 직후 detail/list polling
- OPEN 주문 주기 대사
- CLOSED 목록은 OpenAPI 1.2.13이 cursor pagination을 명시합니다. 기본 runner가 7일 중첩 window로 수집하며, 범위를 넓히는 복구 실행만 명시적으로 수행합니다.
- 종료 주문은 OPEN일 때 확보한 정확한 `orderId`로 상세 조회
- rate-limit header 기반 cadence 조정
- 429 발생 시 `Retry-After` 우선

폴링 숫자는 코드에 고정하지 않고 `docs/10_calibration_policy.md`와 실제 관측치로 조정합니다.

## Live Adapter Gate

아래 조건을 모두 충족해야 live adapter를 켤 수 있습니다.

- OAuth2 토큰 발급 성공
- Open API 허용 IP 등록 완료
- `accountSeq` 확인
- holdings 반복 조회와 내부 position 대사
- 주문 제출, 상세 조회, 취소 흐름 확인
- 부분체결과 종료 주문 replay 확인
- `cashBuyingPower` constraint와 내부 available cash 정책 확인
- raw broker snapshot 저장 확인
- rate-limit header와 429 retry 처리 확인
- error envelope의 `requestId`, `code`, `message`, `data` 기록
- broker reconciliation이 반복적으로 허용 오차 이내

하나라도 빠지면 `paper` 또는 `semi_auto` 모드를 유지합니다.
