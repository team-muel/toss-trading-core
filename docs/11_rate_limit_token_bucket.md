# Rate Limit Token Bucket

## Implementation Status

Foundation runner now has a conservative in-memory token bucket:

- implementation: `src/toss_trading/runtime/rate_limit.py`
- adapter integration: `TossReadOnlyAdapter`
- defaults: `TOSS_RATE_LIMIT_CAPACITY=20`, `TOSS_RATE_LIMIT_REFILL_PER_SECOND=5`
- response header sync: `X-RateLimit-Limit`, `X-RateLimit-Remaining`

This is enough for read-only foundation snapshot/audit on one VM process. It is not yet a distributed or persisted bucket. Before multi-process runners, cron overlap, or live order submission, rate-limit state must be persisted or process concurrency must be prevented.

The GCP foundation runner uses `flock` through `scripts/run_foundation_gcp.sh` to prevent overlapping read-only runs on one VM. This is still not a distributed rate-limit bucket and does not cover multiple VMs.

## Goal

Toss Open API는 client x API group 기준 rate limit을 적용합니다. 주문 시스템은 응답 헤더를 읽어 그룹별 token bucket을 갱신하고, limit에 가까워지면 신규 주문보다 상태 조회와 취소를 우선합니다.

## Bucket Key

```text
bucket_key = client_id_hash + ":" + api_group
```

API group:

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
- 429 수신 시 `Retry-After`를 우선하고, 지수 백오프와 jitter를 적용합니다.
- 429가 반복되면 해당 API group을 degraded 상태로 표시합니다.
- degraded 상태에서는 신규 주문 생성을 중단하고 상태 조회, 취소, reconciliation을 우선합니다.

## Priority

High:

- `orders.get`
- `orders.list_open`
- `orders.list_closed`
- `buying_power.get`
- `orders.cancel`

Normal:

- `orders.submit`
- `orders.modify`
- `sellable_quantity.get`
- `commissions.get`

Low:

- candle backfill
- large quote refresh
- research data query

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
- `degraded_state`

## External Vendors

외부 vendor도 각자 rate limit이 있지만 Toss token bucket과 섞지 않습니다. vendor rate limit은 `source_health_snapshot`과 adapter별 scheduler에서 관리하고, source가 degraded이면 해당 feature를 끕니다. Toss 주문 상태 조회를 외부 vendor 요청보다 우선합니다.
