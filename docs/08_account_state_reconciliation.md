# Account State And Reconciliation

## Why This Comes Before Strategy

Toss API 기반 자동매매에서 가장 중요한 모듈은 전략 엔진이 아니라 계좌 상태 엔진입니다. 공식 API는 `buying-power`, `holdings`, `orders`, 주문별 `execution.settlementDate`를 제공하지만 별도 cashflow/tax lot API는 문서상 제공하지 않습니다.

따라서 브로커 응답과 내부 이벤트 장부를 결합해 주문 가능 금액, 포지션, 주문 상태, 체결 합계, 수수료, 세금, 결제 예정일을 재현해야 합니다.

## Account State Formula

```text
available_cash =
  broker_cash_buying_power
  - reserved_cash_open_orders
  - estimated_fees
  - estimated_tax_reserve
  - liquidity_buffer
```

`risk_nav`는 현재 NAV를 그대로 쓰지 않습니다. 수익 직후 포지션이 자동 확대되는 것을 막기 위해 보수적으로 계산합니다.

```text
risk_nav = min(current_nav, rolling_20d_avg_nav)
```

수익 인식률, 손실 반영률 같은 숫자는 하드코딩하지 않고 calibration policy에서 별도로 승인합니다.

## Required State Variables

| Variable | Meaning |
| --- | --- |
| `broker_cash_buying_power` | `GET /api/v1/buying-power`의 `cashBuyingPower` |
| `estimated_cash_balance` | internal reconstructed cash |
| `pending_settlement_cash` | order execution settlementDate 기준 미정산 추정 |
| `reserved_cash_open_orders` | cash reserved by open orders |
| `available_cash` | internally allowed new order cash |
| `net_liquidation_value` | holdings market value + internal cash estimate |
| `risk_nav` | conservative NAV for sizing |
| `estimated_tax_reserve` | tax and fee reserve |

## Reconciliation Rules

| Item | Tolerance | Action When Exceeded |
| --- | --- | --- |
| Buying power difference | starter/calibrated tolerance | block new orders, reload broker snapshot |
| Position quantity difference | 0 | block new orders, replay orders |
| Average price difference | small rounding only | recompute realized P&L and lots |
| Open order mismatch | 0 | manual review before new orders |
| Missing closed order | 0 | reload CLOSED order pages and replay ledger |
| Commission/tax mismatch | 0 | hold P&L as provisional |

## Replay Principle

내부 장부는 이벤트 소싱 방식으로 재현 가능해야 합니다. 같은 입력 이벤트를 다시 재생하면 같은 계좌 상태가 나와야 합니다.

필수 이벤트:

- order submitted
- order accepted
- order rejected
- order cancelled
- partial fill
- full fill
- commission
- tax
- settlement scheduled
- settlement completed by policy
- reconciliation snapshot

공식 주문 `execution` 필드의 `commission`, `tax`, `settlementDate`는 `cash_ledger`와 `settlement` 이벤트 생성의 기준으로 사용합니다.

## Live Trading Blockers

다음 중 하나라도 발생하면 `live` 신규 주문을 차단합니다.

- 브로커 스냅샷 조회 실패
- OPEN 주문 상태 불명
- CLOSED 주문 페이징 누락
- holdings와 내부 position ledger 불일치
- `GET /api/v1/buying-power` 조회 실패
- buying power가 내부 available cash와 정책 허용 범위를 벗어남
- 주문 타임아웃 뒤 기존 주문 접수 여부 확인 불가
