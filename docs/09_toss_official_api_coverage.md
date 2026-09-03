# Toss Official API Coverage

이 문서는 공식 Toss Invest Open API 기준 문서입니다. 전략 판단, 외부 데이터 피드, 운영 정책은 다른 문서에서 다룹니다.

## Official Base

- Approved schema version: `1.2.14`
- Approved SHA-256: `a7b32ba754401d13fa649ba91eebd212420eb1afab28e9c2c0d6ea8d43055fed`
- Base server: `https://openapi.tossinvest.com`
- Auth: OAuth2 Client Credentials Grant
- Token endpoint: `POST /oauth2/token`
- Account context header: `X-Tossinvest-Account: {accountSeq}`
- Runtime style: REST API (read-only Foundation)

2026-09-03 `latest/openapi.json`을 다시 다운로드해 위 SHA-256과
OpenAPI version `1.2.14`를 확인했습니다. 현재 공식 paths에는 별도 현금
잔고 또는 balance endpoint가 없고, 현금 관련 주문 전 constraint는
`GET /api/v1/buying-power`의 `cashBuyingPower`입니다. 따라서 내부 초기
현금잔고를 이 값에서 역산하지 않습니다.

## Confirmed API Groups

| Group | Endpoints | Trading Meaning |
| --- | --- | --- |
| Auth | `POST /oauth2/token` | access token 발급 |
| Account | `GET /api/v1/accounts` | 계좌 목록과 `accountSeq` 확인 |
| Asset | `GET /api/v1/holdings` | 국내/미국 주식 보유 현황 |
| Market Data | `GET /api/v1/orderbook`, `prices`, `trades`, `price-limits`, `candles` | 호가, 현재가, 체결, 상하한가, 1분/일봉 |
| Stock Info | `GET /api/v1/stocks`, `stocks/all`, `stocks/{symbol}/warnings` | 종목 마스터, 거래소별 전체 거래 가능 유니버스, 매수 유의사항 |
| KR Stock Trading Trend | `GET /api/v1/stocks/{symbol}/investor-trading`, `program-trades`, `short-selling`, `credit-trades`, `securities-lending` | 국내 종목 투자자·프로그램·공매도·신용·대차 일별 동향 |
| Market Info | `GET /api/v1/exchange-rate`, `market-calendar/KR`, `market-calendar/US` | 환율, 국내/미국 장 운영 |
| Order | `POST /api/v1/orders`, `orders/{orderId}/modify`, `orders/{orderId}/cancel` | 주문 생성, 정정, 취소 |
| Order History | `GET /api/v1/orders`, `orders/{orderId}` | 주문 목록, 주문 상세 |
| Order Info | `GET /api/v1/buying-power`, `sellable-quantity`, `commissions` | 매수 가능 금액, 매도 가능 수량, 수수료 |

`GET /api/v1/stocks/all`은 `KOSPI`, `KOSDAQ`, `NYSE`, `NASDAQ`,
`AMEX`, `KR_ETC`, `US_ETC`별 활성 종목을 페이지네이션 없이 반환하며,
상장 상태·증권 유형·보통주 여부 필터를 지원합니다. `/prices`와 `/stocks`는
최대 200개 심볼을 한 요청으로 조회하므로 수집기는 200개 단위로 분할합니다.

국내 종목 동향 5개 API는 최대 100개 일별 레코드와 `nextUntil` cursor를
제공합니다. 이 데이터는 미국 종목에는 제공되지 않습니다. 공매도 동향은
일별 거래 활동이지 short interest가 아니며, 대차잔고는 borrow availability나
borrow fee가 아닙니다.

## Connected Read-only Adapter

`TossReadOnlyAdapter`는 다음 read-only endpoint를 모두 호출할 수 있습니다.

- 거래소별 전체 거래 가능 종목
- 최대 200개 단위 종목 기본정보와 현재가
- 종목별 경고, 호가, 최근 체결, 상·하한가
- 국내 종목별 투자자·프로그램·공매도·신용·대차 동향
- 환율, 장 운영 정보, 랭킹, 국내 지수·국채 지표

시장 데이터 경로는 OAuth token만 사용하며 계좌 헤더나 주문 endpoint를
사용하지 않습니다. 국내 종목 동향은 국내 심볼에만 요청해야 합니다.

## Important Order Rules

OpenAPI 1.2.14에는 `/api/v1/conditional-orders`의 `SINGLE`, `OCO`, `OTO`
조건주문도 포함됩니다. 이 경로들은 계약에는 기록했지만
`config/default_policy.yaml`에서 계속 비활성화하며, 현재
`TossReadOnlyAdapter`는 생성·정정·취소할 수 없습니다. 승인된 경로와 method는
`config/toss_openapi_contract.json`에 고정합니다. 계약 검사는 공식 문서의
33개 path에 속한 모든 HTTP method가 승인 또는 명시적 비활성 중 하나로
분류됐는지도 확인합니다. 새 endpoint가 문서에 추가되면 해시를 갱신하는
것만으로는 통과하지 않습니다.

- `clientOrderId`는 멱등성 키입니다.
- `clientOrderId` 미전달 시 멱등성이 적용되지 않습니다.
- 동일 `clientOrderId` 재요청은 이전 주문 결과를 재반환합니다.
- 수량 주문은 정수 주 단위입니다.
- 미국 주식은 `orderAmount` 기반 시장가 주문을 지원하지만 정규장 시간에만 가능합니다.
- 1억원 이상 주문은 `confirmHighValueOrder=true`가 필요합니다.
- 클라이언트는 unknown enum 값을 허용해야 합니다.

## CLOSED Order Continuity

OpenAPI 1.2.14는 `/api/v1/orders`의 `OPEN`, `CLOSED`와 CLOSED용 cursor
pagination을 명시합니다. 2026-07-21 실제 GCP 계정에서도 종료 주문 반환을
확인했습니다. 기본 6시간 runner는 KST 기준 최근 7일을 중첩 조회해 실행 사이에
OPEN에서 사라진 주문을 놓치지 않습니다. v1은 OPEN 또는 CLOSED 목록에서 얻은
정확한 `orderId`의 `/api/v1/orders/{orderId}` 상세를 같은 snapshot run에서
검증합니다.

## Order Status

Known statuses:

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

주문 상세의 `execution`은 다음 누적 필드를 제공합니다.

- `filledQuantity`
- `averageFilledPrice`
- `filledAmount`
- `commission`
- `tax`
- `filledAt`
- `settlementDate`

이는 개별 fill stream이나 webhook이 있다는 뜻이 아닙니다. 내부 장부는 누적 snapshot과 delta 계산으로 반영합니다.

## Rate Limits

Rate limit은 client x API group 기준입니다. 현재 공식 overview 기준:

| Group | Limit |
| --- | --- |
| `AUTH` | 5 TPS |
| `ACCOUNT` | 1 TPS |
| `ASSET` | 5 TPS |
| `STOCK` | 5 TPS |
| `STOCK_ALL` | 1 TPS |
| `STOCK_TRADING_TREND` | 10 TPS |
| `MARKET_INFO` | 3 TPS |
| `MARKET_DATA` | 15 TPS |
| `MARKET_DATA_CHART` | 20 TPS |
| `ORDER` | 10 TPS |
| `ORDER_HISTORY` | 5 TPS |
| `ORDER_INFO` | 6 TPS, 09:00-09:10 KST에는 3 TPS |

응답 헤더:

- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-RateLimit-Reset`
- `Retry-After` for 429

코드는 이 숫자를 고정값으로 신뢰하지 않고 응답 헤더를 읽어 token bucket을 갱신합니다.

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

## Not Covered By Toss Open API

- 해외 옵션 주문
- 옵션 chain, IV, Greeks
- 옵션 assignment/expiration 이벤트
- T-bill 또는 채권 직접 보유/담보 API
- margin requirement breakdown
- borrow rate, short availability
- webhook
- WebSocket 런타임 연동(공식 AsyncAPI 3.0 계약은 별도로 제공되지만 현재 비활성)
- tax lot/cost basis API
- 배당/원천징수/corporate action cashflow 이벤트 API
- ETF NAV, premium/discount, ROC 분배 구성

## Revised Live Scope

Toss API만으로 가능한 실거래 MVP:

- 기술 API 범위는 국내/미국 주식·ETF이나, 이 저장소의 live 후보는 전용 계좌의
  미국 상장 USD long-only ETF 현물 주문으로 한정
- REST polling 기반 주문 상태 대사
- `cashBuyingPower` constraint 기반 주문 가능 금액 통제
- sellable quantity 기반 매도 가능 수량 통제
- holdings/orders/execution summary 기반 내부 장부 replay

Toss API 단독으로 live 자동화하지 않는 영역:

- 옵션 캐리
- T-bill 직접 ladder
- 숏/대차 기반 pair
- 마진/레버리지 기반 전략
- 세무 lot 자동 확정
