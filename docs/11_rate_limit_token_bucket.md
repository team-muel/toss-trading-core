# Rate Limit Token Bucket

## Goal

Toss Open API는 client x API group 기준 rate limit을 적용합니다. 주문 시스템은 응답 헤더를 읽어 그룹별 token bucket을 갱신하고, limit에 가까워지면 주문보다 조회를 먼저 감속해야 합니다.

## Bucket Key

```text
bucket_key = client_id_hash + ":" + api_group
```

API group 예시:

- `AUTH`
- `ACCOUNT`
- `ASSET`
- `STOCK`
- `MARKET_INFO`
- `MARKET_DATA`
- `MARKET_DATA_CHART`
- `ORDER`
- `ORDER_HISTORY`
- `ORDER_INFO`

## Header Inputs

정상 응답과 429 응답에서 다음 헤더를 읽습니다.

- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-RateLimit-Reset`
- `Retry-After`

## Runtime Behavior

- 요청 전 bucket에 token이 없으면 큐에 넣고 대기합니다.
- `ORDER`와 `ORDER_INFO`는 장 초반 피크 한도를 별도로 적용합니다.
- 429 수신 시 `Retry-After`를 우선하고, 그 다음 지수 백오프와 jitter를 적용합니다.
- 429가 반복되면 해당 API group을 degraded 상태로 표시합니다.
- `ORDER` group degraded 상태에서는 신규 주문 생성보다 주문 상태 확인을 우선합니다.

## Priority

높은 우선순위:

- `orders.get`
- `orders.list_open`
- `orders.list_closed`
- `buying_power.get`
- `orders.cancel`

낮은 우선순위:

- 캔들 백필
- 대량 현재가 갱신
- 리서치용 데이터 조회

## Persistence

rate limit 이벤트는 운영 분석을 위해 저장합니다.

필드:

- `ts`
- `api_group`
- `endpoint`
- `limit`
- `remaining`
- `reset_seconds`
- `retry_after_seconds`
- `request_id`
- `action`
