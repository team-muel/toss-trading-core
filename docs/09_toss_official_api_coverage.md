# Toss Official API Coverage

이 문서는 공식 Toss Invest Open API 문서를 기준으로 기존 자동매매 설계를 수정한 반영본입니다.

## Official Base

- Base server: `https://openapi.tossinvest.com`
- Auth: OAuth2 Client Credentials Grant
- Token endpoint: `POST /oauth2/token`
- Account context header: `X-Tossinvest-Account: {accountSeq}`
- Runtime style: REST API only

## Confirmed API Groups

| Group | Endpoints | Trading Meaning |
| --- | --- | --- |
| Auth | `POST /oauth2/token` | access token 발급 |
| Account | `GET /api/v1/accounts` | 계좌 목록, `accountSeq` 확인 |
| Asset | `GET /api/v1/holdings` | 국내/미국 주식 보유 현황 |
| Market Data | `GET /api/v1/orderbook`, `prices`, `trades`, `price-limits`, `candles` | 호가, 현재가, 체결, 상하한가, 1분/일봉 |
| Stock Info | `GET /api/v1/stocks`, `stocks/{symbol}/warnings` | 종목 마스터, 매수 유의사항 |
| Market Info | `GET /api/v1/exchange-rate`, `market-calendar/KR`, `market-calendar/US` | 환율, 국내/미국 장 운영 |
| Order | `POST /api/v1/orders`, `orders/{orderId}/modify`, `orders/{orderId}/cancel` | 주문 생성, 정정, 취소 |
| Order History | `GET /api/v1/orders`, `orders/{orderId}` | OPEN/CLOSED 주문 목록, 주문 상세 |
| Order Info | `GET /api/v1/buying-power`, `sellable-quantity`, `commissions` | 매수 가능 금액, 매도 가능 수량, 수수료 |

## Important Order Rules

- `clientOrderId`는 멱등성 키입니다.
- `clientOrderId` 미전달 시 멱등성이 적용되지 않습니다.
- 동일 `clientOrderId` 재요청은 이전 주문 결과를 재반환합니다.
- 멱등성 키 유효기간은 10분입니다.
- 수량 주문은 정수 주 단위입니다.
- 미국 주식은 `orderAmount` 기반 시장가 주문을 지원하지만 정규장 시간에만 가능합니다.
- 1억원 이상 주문은 `confirmHighValueOrder=true`가 필요합니다.
- 미국 주식 정정은 가격 변경만 지원하고 수량 변경은 지원하지 않습니다.
- 클라이언트는 unknown enum 값을 허용해야 합니다.

주문 상태:

- `PENDING`
- `PENDING_CANCEL`
- `PENDING_REPLACE`
- `PARTIAL_FILLED`
- `FILLED`
- `CANCELED`
- `REJECTED`
- `CANCEL_REJECTED`
- `REPLACE_REJECTED`
- `REPLACED`

운영 정책상 terminal로 바로 처리하는 상태는 `FILLED`, `CANCELED`, `REJECTED`, `REPLACED`입니다. `CANCEL_REJECTED`와 `REPLACE_REJECTED`는 원주문 상태를 다시 조회해야 하는 review 상태입니다.

## Execution Data Available

주문 상세의 `execution`은 다음 필드를 제공합니다.

- `filledQuantity`
- `averageFilledPrice`
- `filledAmount`
- `commission`
- `tax`
- `filledAt`
- `settlementDate`

따라서 내부 장부는 주문별 체결 합계, 수수료, 세금, 결제 예정일을 반영할 수 있습니다. 다만 개별 fill stream이나 webhook이 제공된다는 의미는 아닙니다.

## Rate Limits

Rate limit은 client x API group 기준입니다.

| Group | Limit |
| --- | --- |
| `AUTH` | 5 TPS |
| `ACCOUNT` | 1 TPS |
| `ASSET` | 5 TPS |
| `STOCK` | 5 TPS |
| `MARKET_INFO` | 3 TPS |
| `MARKET_DATA` | 10 TPS |
| `MARKET_DATA_CHART` | 5 TPS |
| `ORDER` | 6 TPS, 09:00-09:10 KST에는 3 TPS |
| `ORDER_HISTORY` | 5 TPS |
| `ORDER_INFO` | 6 TPS, 09:00-09:10 KST에는 3 TPS |

응답 헤더:

- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-RateLimit-Reset`
- `Retry-After` for 429

## Error Model

에러는 `error.requestId`, `error.code`, `error.message`, `error.data` envelope로 내려옵니다.

자동매매에서 즉시 처리해야 할 주요 코드:

- `invalid-token`, `expired-token`
- `account-header-required`
- `confirm-high-value-required`
- `request-in-progress`
- `already-filled`, `already-canceled`, `already-modified`, `already-rejected`
- `insufficient-buying-power`
- `order-hours-closed`
- `stock-restricted`
- `price-out-of-range`
- `opposite-pending-order-exists`
- `order-type-not-allowed`
- `prerequisite-required`
- `amount-order-outside-regular-hours`
- `modify-restricted`, `cancel-restricted`
- `rate-limit-exceeded`
- `maintenance`

## Still Not Covered

공식 문서 기준으로 아래는 자동매매 범위 밖이거나 별도 데이터 소스가 필요합니다.

- 해외 옵션 주문
- 옵션체인
- IV/Greeks
- 옵션 assignment/expiration 이벤트
- T-bill 또는 채권 직접 보유/담보 API
- margin requirement breakdown
- borrow rate, short availability
- webhook 또는 websocket stream
- tax lot/cost basis API
- 배당/원천징수/corporate action cashflow 이벤트 API
- ETF NAV, premium/discount, ROC 분배 구성

## Revised Strategy Scope

Toss API만으로 가능한 실거래 MVP:

- 국내/미국 주식·ETF 현물 주문
- ETF 상대가치 현물 페어 후보의 저회전 운용
- 분배형 ETF 필터를 적용한 현물 매수 차단/허용
- USD `cashBuyingPower`를 broker constraint로 사용한 주문 가능 금액 통제
- REST 폴링 기반 주문 상태 대사

Toss API 단독으로 live 자동화하지 않는 영역:

- 옵션 캐리 엔진
- T-bill 담보 엔진
- 숏/대차 기반 페어
- 마진/레버리지 기반 전략
- 세무 lot 자동 확정
