# Toss API Key And Adapter Plan

## 핵심 판단

공식 Toss Invest Open API는 OAuth2 Client Credentials Grant 방식입니다. 실거래 가능 여부는 `client_id/client_secret`으로 토큰을 받고, `accountSeq`를 계좌 헤더에 넣어 계좌/주문 API를 안정적으로 호출할 수 있는지로 판단합니다.

공식 문서 기준으로 현물 주식/ETF 자동매매에 필요한 핵심 API는 제공됩니다. 옵션, 채권/T-bill, margin breakdown, webhook/stream, 세무 cashflow 이벤트는 제공 범위가 아니므로 별도 모듈로 분리합니다.

## Secret Handling

실제 키 값은 아래 위치에 두지 않습니다.

- Git 저장소
- 문서
- 코드 상수
- 로그
- 스크린샷
- `.env` 파일

권장 방식:

- 운영 환경: KMS, Vault, Secrets Manager, 또는 브로커/클라우드 secret store
- 로컬 개발: OS credential manager 또는 세션 환경 변수
- 로컬 단일 사용자 개발에서는 Git에 올라가지 않는 `.env`를 임시로 사용할 수 있습니다.
- 테스트: 가능하다면 조회 전용 환경을 먼저 사용하고, trade 권한은 별도 승인 뒤 사용

필수 환경 변수 이름은 아래처럼 표준화합니다. 값은 secret store에서 주입합니다.

```text
TOSS_BROKER_BASE_URL
TOSS_CLIENT_ID
TOSS_CLIENT_SECRET
TOSS_ACCOUNT_SEQ
TOSS_API_ENV
```

`TOSS_BROKER_BASE_URL` 기본값은 `https://openapi.tossinvest.com`입니다.

로컬 워크스페이스에는 `.env.example`과 Git 무시 대상인 `.env`를 둘 수 있습니다. 코드에서는 프로세스 환경 변수를 우선하고, 비어 있으면 로컬 `.env`를 읽습니다.

## Official Adapter Contract

| Internal Interface | Official Endpoint | Required Return Fields |
| --- | --- | --- |
| `auth.refresh()` | `POST /oauth2/token` | `access_token`, expiry metadata if returned |
| `accounts.list()` | `GET /api/v1/accounts` | `accountSeq`, `accountNo`, `accountType` |
| `positions.list()` | `GET /api/v1/holdings` | `symbol`, `quantity`, `lastPrice`, `averagePurchasePrice`, `marketValue`, `profitLoss`, `cost` |
| `orders.submit()` | `POST /api/v1/orders` | `clientOrderId`, `orderId`, `status` |
| `orders.list_open()` | `GET /api/v1/orders?status=OPEN` | all open orders |
| `orders.list_closed()` | `GET /api/v1/orders?status=CLOSED` | paged closed orders |
| `orders.get()` | `GET /api/v1/orders/{orderId}` | `status`, `execution`, `orderedAt`, `canceledAt` |
| `orders.modify()` | `POST /api/v1/orders/{orderId}/modify` | replacement/cancel state |
| `orders.cancel()` | `POST /api/v1/orders/{orderId}/cancel` | cancellation state |
| `buying_power.get()` | `GET /api/v1/buying-power` | `currency`, `cashBuyingPower` |
| `sellable_quantity.get()` | `GET /api/v1/sellable-quantity` | sellable quantity |
| `commissions.get()` | `GET /api/v1/commissions` | KR/US commission rates |

## Idempotency Rule

모든 주문 생성은 `clientOrderId`가 필수입니다.

공식 문서상 `clientOrderId`는 멱등성 키입니다. 미전달 시 매 요청이 별개 주문으로 처리됩니다. 동일 값 재요청은 이전 주문 결과를 재반환하며, 유효기간은 10분입니다.

타임아웃 또는 네트워크 오류가 발생했을 때 같은 주문을 즉시 새 ID로 재전송하지 않습니다. 같은 `clientOrderId`로 재요청하거나 OPEN/CLOSED 주문 조회로 접수 여부를 확인합니다.

## Polling Fallback

공식 문서 기준 Open API는 REST only입니다. active order 구간에서는 폴링을 사용합니다.

- 폴링 간격: rate limit, 주문 상태 지연, 실제 체결 관측치로 calibration
- 최대 대기: 주문 상태 불명 리스크와 rate limit을 보고 calibration
- terminal status: `FILLED`, `REJECTED`, `CANCELED`, `REPLACED`, `CANCEL_REJECTED`, `REPLACE_REJECTED`
- unresolved status: 신규 주문 중단 후 수동 점검

## Live Adapter Gate

아래 조건을 모두 충족해야 `live` 어댑터를 켤 수 있습니다.

- OAuth2 토큰 발급 성공
- `GET /api/v1/accounts`로 `accountSeq` 확인
- `GET /api/v1/holdings` 반복 조회와 내부 포지션 장부 대사
- 주문 제출, 상태 조회, 취소가 모두 확인됨
- 부분체결과 종료 주문이 내부 장부에 정확히 반영됨
- `GET /api/v1/buying-power`의 `cashBuyingPower`와 내부 available cash 정책이 확인됨
- rate limit 헤더와 429 retry가 처리됨
- error envelope의 `requestId`, `code`, `message`, `data`가 로그에 남음
- broker reconciliation이 반복적으로 0 차이를 유지

하나라도 빠지면 `paper` 또는 `semi_auto` 모드를 유지합니다.
