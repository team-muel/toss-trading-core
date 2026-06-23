# 데이터 계약과 로그 스키마

## 공통 원칙

모든 데이터는 다음 필드를 가져야 합니다.

- `ts`: 데이터가 의미하는 시장 시각
- `ingested_at`: 시스템 수집 시각
- `source`: 데이터 공급자
- `as_of`: 공시 또는 스냅샷 기준일
- `quality_flag`: `ok`, `stale`, `missing`, `estimated`, `manual`

## Core Tables

### market_bars

| Field | Description |
| --- | --- |
| `ts` | bar timestamp |
| `symbol` | ticker |
| `venue` | exchange or broker venue |
| `open`, `high`, `low`, `close` | price fields |
| `volume` | traded volume |
| `source` | data source |

### options_chain_snapshot

Toss 공식 API는 옵션체인과 Greeks를 제공하지 않습니다. 이 테이블은 외부 데이터 또는 연구용입니다.

| Field | Description |
| --- | --- |
| `ts` | snapshot time |
| `underlying` | underlying ticker |
| `expiry` | expiration date |
| `strike` | strike price |
| `cp` | call/put |
| `bid`, `ask`, `mid` | quote prices |
| `iv`, `delta`, `gamma`, `vega` | option metrics |
| `oi`, `volume` | liquidity fields |
| `source` | data source |

### signal_log

| Field | Description |
| --- | --- |
| `ts` | signal time |
| `engine` | engine name |
| `symbol_or_pair` | traded symbol or pair |
| `regime_tag` | market regime |
| `raw_score` | engine native score |
| `adjusted_score` | cost/risk adjusted score |
| `signal_side` | buy/sell/avoid |
| `target_weight` | proposed target |
| `expected_max_loss` | conservative loss estimate |
| `reason_code` | rule explanation |

### order_log

| Field | Description |
| --- | --- |
| `ts` | order creation time |
| `broker` | broker adapter |
| `mode` | paper/semi_auto/live |
| `client_order_id` | Toss `clientOrderId`, required for idempotency |
| `broker_order_id` | Toss `orderId` |
| `symbol` | symbol |
| `side` | buy/sell |
| `qty` | quantity |
| `order_amount` | US amount-based market order amount |
| `limit_px` | limit price |
| `status` | planned/submitted/filled/rejected/cancelled |
| `reject_code` | broker reject reason |
| `source_signal_id` | signal reference |

### fill_log

| Field | Description |
| --- | --- |
| `ts` | fill time |
| `order_id` | order reference |
| `fill_qty` | filled quantity |
| `fill_px` | fill price |
| `fees` | commissions and fees |
| `tax` | execution tax |
| `settlement_date` | Toss execution settlementDate |
| `slippage_vs_model` | realized slippage |

### position_log

| Field | Description |
| --- | --- |
| `ts` | snapshot time |
| `engine` | owning engine |
| `position_id` | position reference |
| `net_qty` | net quantity |
| `delta`, `vega` | risk metrics |
| `max_loss` | defined or estimated max loss |
| `collateral_reserved` | reserved collateral |
| `unrealized_pnl` | mark-to-market P&L |

### risk_snapshot

| Field | Description |
| --- | --- |
| `ts` | snapshot time |
| `portfolio_nav` | total NAV |
| `risk_nav` | conservative NAV for sizing |
| `estimated_cash_balance` | internal reconstructed cash |
| `broker_cash_buying_power` | Toss cashBuyingPower |
| `pending_settlement_cash` | settlementDate-based estimate |
| `reserved_cash_open_orders` | cash reserved by open orders |
| `kill_switch_state` | current kill-switch state |
| `engine_pnl_corr_hash` | correlation state fingerprint |
| `stress_2008`, `stress_2020`, `stress_2022`, `stress_2024` | stress estimates |

### tax_lot_log

| Field | Description |
| --- | --- |
| `ts` | event time |
| `symbol` | symbol |
| `lot_id` | tax lot id |
| `open_fx_rate`, `close_fx_rate` | KRW conversion references |
| `dividend_gross` | gross dividend/distribution |
| `withholding` | withheld tax |
| `roc_adjustment` | return-of-capital adjustment |

### cash_ledger

| Field | Description |
| --- | --- |
| `ts` | cash event time |
| `account_id` | broker account id |
| `currency` | cash currency |
| `event_type` | separated cash event type |
| `amount` | signed amount |
| `settlement_date` | settlement date when applicable |
| `source_ref` | order, fill, dividend, tax, or broker reference |
| `tax_relevant` | whether tax ledger needs this event |

필수 `event_type`:

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

### broker_reconciliation_log

| Field | Description |
| --- | --- |
| `ts` | reconciliation time |
| `account_id` | broker account id |
| `item_type` | cash, position, order, fill, dividend, tax, corporate_action |
| `broker_value` | broker-reported value |
| `internal_value` | internally reconstructed value |
| `difference` | broker minus internal |
| `status` | ok, warning, failed |
| `action_required` | manual action or automated block |

## Data Quality Gates

신규 신호 생성은 아래 조건에서 중단합니다.

- 시장 데이터 timestamp가 starter/calibrated 지연 한도를 초과
- 옵션 chain의 bid/ask가 역전 또는 비정상적으로 넓음
- NAV/premium 데이터가 전일 이전으로 stale
- borrow rate가 필요한 포지션인데 값이 없음
- tax/ROC 분류가 필요한 분배형 ETF인데 분배 구성 정보가 없음
- 브로커 현금/포지션/주문/체결과 내부 장부의 reconciliation이 실패
- `clientOrderId` 없는 live 주문 생성 시도
- `cashBuyingPower` 조회 실패
- OPEN 주문 목록 대사 실패
