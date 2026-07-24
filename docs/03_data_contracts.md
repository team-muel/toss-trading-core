# Data Contracts And Ledger Schema

이 문서는 정규화된 내부 데이터 계약을 정의합니다. 실제 SQL 초안은 `schemas/trading_ledger.sql`을 기준으로 합니다.

## Common Fields

모든 수집 데이터는 가능한 한 아래 필드를 가집니다.

| Field | Meaning |
| --- | --- |
| `event_time_utc` | 데이터가 의미하는 UTC 시각 |
| `available_at` | 해당 값이 전략에 실제 사용 가능해진 시각 |
| `source_ts` | provider가 준 원본 timestamp |
| `received_at` | 시스템 수신 시각 |
| `exchange_local_date` | 해당 거래소 기준 날짜 |
| `session_label` | regular, premarket, afterhours, close 등 |
| `source_timezone` | 원본 timestamp의 timezone |
| `source` | Toss, Massive, FRED, SEC, issuer 등 |
| `quality_flag` | `ok`, `stale`, `missing`, `estimated`, `manual`, `blocked` |

시간 비교와 event window 계산은 UTC를 기준으로 하고, 주문 가능 여부는 거래소 local session을 함께 봅니다.

## Reference Data

### instrument_master

심볼 매핑은 별도 원장으로 관리합니다. 미국 주식/ETF는 ticker가 단순해 보여도 옵션 OCC symbol, CIK, vendor ticker, Toss stock master가 다를 수 있습니다.

필수 필드:

- `symbol_id`
- `toss_symbol`
- `ticker`
- `vendor_symbol`
- `occ_symbol`
- `cik`
- `asset_class`
- `currency`
- `timezone`
- `mic`
- `effective_from`
- `effective_to`

### source_health_snapshot

외부 데이터 피드가 정상인지 판단하는 테이블입니다.

필수 필드:

- `source`
- `channel`
- `last_success_at`
- `max_age_ms`
- `heartbeat_timeout_ms`
- `lag_ms`
- `source_status`
- `action`

`source_status`가 `stale`, `degraded`, `blocked`이면 해당 피드에 의존하는 신규 신호를 중단합니다.

### raw_api_response

모든 API 원본 응답의 공통 저장소입니다. Toss broker 응답과 외부 vendor 응답을 같은 형식으로 저장합니다.

필수 필드:

- `source`
- `source_type`: `broker` 또는 `vendor`
- `account_seq`: broker/account-bound 응답일 때만 값 존재
- `endpoint`
- `http_method`
- `request_hash`
- `response_hash`
- `status_code`
- `body_json`

민감한 API key, token, raw account number, raw account identifier는 저장하지 않습니다. `raw_api_response.body_json` 저장 전 OAuth token, `accountNo`/`accountNumber`/`account_no`, `accountSeq`/`account_seq`류 필드는 redaction합니다. 정규화 테이블의 `account_seq`는 대사 키로만 사용합니다.

## Market And External Data

### market_bars

OHLCV bar 저장소입니다. Toss와 외부 provider 모두 들어올 수 있으므로 `source`와 timestamp metadata가 필수입니다.

연구용 대량 시계열의 canonical 저장소는 Foundation SQLite가 아니라
`research_data/silver`의 Parquet입니다. `interval`, `adjustment`,
`available_at`, `source_revision`, `raw_manifest_id`, `schema_version`을
반드시 보존합니다. raw와 total-return 값을 같은 key로 덮어쓰지 않습니다.

### options_chain_snapshot

Toss 공식 API는 옵션 chain과 Greeks를 제공하지 않습니다. 이 테이블은 research 또는 risk input 전용입니다.

필수 필드:

- `underlying`
- `expiry`
- `strike`
- `cp`
- `bid`, `ask`, `mid`
- `iv`, `delta`, `gamma`, `vega`
- `oi`, `volume`
- `source`
- `available_at`
- `dataset_manifest_ids`
- `transformation_version`
- `parameters_hash`
- `code_revision`
- `quality_flag`

### external_event_log

SEC filing, issuer notice, split/dividend event, trading halt 같은 이벤트를 표준화합니다.

필수 필드:

- `source`
- `source_event_id`
- `symbol`
- `cik`
- `event_type`
- `event_status`
- `event_time_utc`
- `filing_url` 또는 `source_url`
- `raw_snapshot_ref`

### rate_series_observation

FRED 또는 Treasury 계열 금리 시계열입니다.

필수 필드:

- `series_id`
- `observation_date`
- `value`
- `realtime_start`
- `realtime_end`
- `source`

ALFRED 또는 vintage data를 쓰는 백테스트에서는 revision-aware 조회를 사용합니다.

### etf_nav_snapshot

ETF NAV, premium/discount, indicative value를 저장합니다. issuer parser 또는 외부 vendor의 품질을 함께 기록합니다.

### etf_distribution_event

배당, 분배, ROC, 원천징수 보조 정보를 저장합니다.

필수 필드:

- `symbol`
- `declaration_date`
- `ex_date`
- `record_date`
- `pay_date`
- `cash_amount`
- `currency`
- `distribution_type`
- `roc_flag`
- `tax_character_source`
- `quality_flag`

generic dividend API는 ROC tax character를 완전히 보장하지 않으므로 issuer notice와 SEC filing으로 보강합니다.

### feature_snapshot

feature 계산 결과입니다. feature는 주문 신호가 아닙니다.

필수 필드:

- `symbol`
- `feature_namespace`
- `feature_name`
- `feature_value`
- `lookback_window`
- `source`
- `quality_flag`

## Account And Order Data

### account_snapshot

Toss 계좌 목록 응답을 정규화한 snapshot입니다.

### holding_snapshot

Toss 보유주식 응답을 정규화한 snapshot입니다. 이 테이블은 broker가 보고한 보유 상태이고, 전략별 position은 `position_log`와 분리합니다.

### broker_order_snapshot

Toss 주문 목록/상세 응답을 정규화한 snapshot입니다. 주문 상세의 execution summary는 이 테이블에 누적값으로 저장하고, 장부 반영은 `execution_snapshot_log`와 `execution_delta_log`가 담당합니다.

### buying_power_snapshot

Toss buying power 응답을 정규화한 snapshot입니다. `cash_buying_power`는 현금 잔고가 아니라 broker constraint입니다.

### sellable_quantity_snapshot

Toss sellable quantity 응답을 정규화한 snapshot입니다.

### commission_snapshot

Toss commissions 응답을 정규화한 snapshot입니다. 실제 체결 수수료는 주문 execution snapshot을 우선합니다.

### signal_log

전략 엔진의 후보 신호입니다. 주문 의사결정이 아니라 risk hub 입력입니다.

필수 필드:

- `engine`
- `symbol_or_pair`
- `raw_score`
- `adjusted_score`
- `signal_side`
- `target_weight`
- `reason_code`

### signal_decision_log

ALLOW/REDUCE/BLOCK 계층입니다. feature와 raw signal을 받아 최종적으로 주문 planner에 넘길 수 있는 의사결정만 남깁니다.

필수 필드:

- `engine`
- `symbol`
- `decision`: `ALLOW`, `REDUCE`, `BLOCK`
- `target_weight`
- `source_signal_id`
- `source_feature_ids`
- `gate_reason`

### order_log

주문 계획과 broker 주문 ID를 연결합니다.

필수 필드:

- `account_seq`
- `client_order_id`
- `broker_order_id`
- `symbol`
- `side`
- `order_basis`: `quantity` 또는 `amount`
- `qty`: quantity 기반 주문일 때만 값 존재
- `order_amount`: amount 기반 주문일 때만 값 존재
- `order_type`
- `time_in_force`
- `status`
- `raw_request_ref`
- `raw_response_ref`

`qty`와 `order_amount`는 동시에 존재할 수 없습니다. 미국 `orderAmount` 시장가 주문은 별도 제약과 정규장 조건을 둡니다.

### execution_snapshot_log

Toss 주문 상세의 누적 execution snapshot입니다.

- `cumulative_filled_qty`
- `cumulative_filled_amount`
- `average_filled_price`
- `cumulative_commission`
- `cumulative_tax`
- `settlement_date`

### execution_delta_log

이전 snapshot과 현재 snapshot의 차이입니다. cash ledger와 position replay는 delta를 기준으로 반영합니다.

### cash_ledger

내부 현금 보조장부입니다. Toss `cashBuyingPower`를 현금처럼 저장하지 않습니다.

체결 기반 `TRADE_COST`, `TRADE_PROCEEDS`, `COMMISSION_FEE`,
`REGULATORY_FEE` 이벤트는 `execution_delta_log`에서 파생하며
`amount_decimal`로 정확한 금액 문자열을 보존합니다. 동일 execution
delta와 event type 조합은 결정적 ID를 사용해 한 번만 반영합니다. 초기
현금잔고는 추정하거나 `cashBuyingPower`에서 역산하지 않습니다.

현재 OPEN 매수 주문의 예약현금은 같은 run의 주문을 `broker_order_id`별로
중복 제거한 뒤 미체결 수량×지정가 또는 남은 주문금액으로 계산합니다.
가격·통화·주문금액을 확정할 수 없으면 신규 주문 허용값을 만들지 않고
reconciliation blocker로 처리합니다.

필수 event type:

- `TRADE_PROCEEDS`
- `TRADE_COST`
- `COMMISSION_FEE`
- `REGULATORY_FEE`
- `DIVIDEND_GROSS`
- `WITHHOLDING_TAX`
- `RETURN_OF_CAPITAL`
- `INTEREST_CASH`
- `OPTION_PREMIUM_IN`
- `OPTION_PREMIUM_OUT`
- `MARGIN_RELEASE`
- `MARGIN_RESERVE`

### tax_lot_log

세무 신고 확정값이 아니라 보조장부입니다. 취득, 처분, 수수료, 세금, FX, dividend, withholding, ROC/basis adjustment를 분리합니다.

### risk_snapshot

필수 계좌 리스크 변수:

- `portfolio_nav`
- `risk_nav`
- `estimated_cash_balance`
- `broker_cash_buying_power_constraint`
- `pending_settlement_cash`
- `reserved_cash_open_orders`
- `kill_switch_state`

### broker_reconciliation_log

브로커 스냅샷과 내부 ledger의 차이를 저장합니다. 차이가 있으면 신규 주문을 막습니다.

### client_order_id_registry

Toss의 10분 멱등성 유효기간과 무관하게 한번 사용한 `clientOrderId`는 내부적으로 재사용하지 않습니다.

## Data Quality Gates

Global gate:

- Toss broker snapshot 저장 실패
- Toss holdings/orders와 내부 ledger 불일치
- 주문 상태 불명
- `clientOrderId` 없는 live 주문
- `cashBuyingPower` constraint 조회 실패
- rate limit degraded 상태에서 신규 주문 시도

Engine-scoped gate:

- Distribution filter: NAV/ROC/issuer notice stale이면 신규 매수 차단
- ETF relative value: whitelist 밖 pair 또는 borrow 필요한 pair 차단
- Option research: option quote crossed, stale, missing OI이면 신호 비활성화
- Gap fade: orderbook/trades timestamp mismatch 또는 latency 초과 시 비활성화

Fallback rule:

- Massive WebSocket 실패 -> Massive REST snapshot -> 해당 feature 비활성화
- SEC poller 실패 -> nightly archive 보조 -> event gate 보수화
- issuer parser 실패 -> distribution filter 신규 매수 차단
