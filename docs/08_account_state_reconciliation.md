# Account State And Reconciliation

## Why This Comes Before Strategy

Toss API 기반 자동매매에서 가장 중요한 모듈은 전략 엔진이 아니라 계좌 상태 엔진입니다. 공식 API는 buying power, holdings, orders, 주문별 execution summary를 제공하지만 별도 cashflow/tax lot API는 제공하지 않습니다.

따라서 브로커 응답과 내부 이벤트 장부를 결합해 주문 가능 금액, 포지션, 주문 상태, 체결 합계, 수수료, 세금, 결제 예정일을 재현해야 합니다.

## Account State Formula

```text
available_cash =
  broker_cash_buying_power_constraint
  - reserved_cash_open_orders
  - estimated_fees
  - estimated_tax_reserve
  - liquidity_buffer
```

`broker_cash_buying_power_constraint`는 현금 잔고가 아닙니다. Toss가 현재 주문 가능하다고 판단한 제약값이며, 내부 cash ledger와 별도로 비교합니다.

```text
risk_nav = min(current_nav, rolling_20d_avg_nav)
```

`risk_nav`는 수익 직후 자동으로 포지션이 커지는 문제를 막기 위한 sizing 기준입니다.

## Required State Variables

| Variable | Meaning |
| --- | --- |
| `broker_cash_buying_power_constraint` | Toss `cashBuyingPower`; broker constraint |
| `estimated_cash_balance` | 내부 cash ledger로 재구성한 현금 추정값 |
| `pending_settlement_cash` | execution `settlementDate` 기준 미정산 추정 |
| `reserved_cash_open_orders` | open order 예약금 |
| `available_cash` | 내부 주문 허용 현금 |
| `net_liquidation_value` | holdings market value + internal cash estimate |
| `risk_nav` | conservative sizing NAV |
| `estimated_tax_reserve` | tax and fee reserve |

## Reconciliation Rules

| Item | Tolerance | Action When Exceeded |
| --- | --- | --- |
| Buying power constraint drift | starter/calibrated tolerance | block new orders, reload broker snapshot |
| Position quantity difference | 0 | block new orders, replay orders |
| Average price difference | rounding only | recompute P&L and lots |
| Open order mismatch | 0 | manual review before new orders |
| Missing target order detail | 0 | OPEN 상태에서 확보한 `orderId` 상세를 다시 조회 |
| Commission/tax mismatch | 0 until calibrated | hold P&L as provisional |

## Replay Principle

내부 장부는 이벤트 소싱 방식으로 재현 가능해야 합니다. 같은 입력 이벤트를 다시 재생하면 같은 계좌 상태가 나와야 합니다.

2026-07-23 실제 GCS v0·v1 백업을 새 임시 SQLite로 replay했고 두 profile
모두 감사를 통과했습니다. Replay는 기존 DB를 덮어쓰지 않으며 저장된
response hash가 달라지면 실패합니다.

```powershell
python -m toss_trading.cli.foundation_replay `
  --source-db "<restored.sqlite>" `
  --destination-db "<new-replay.sqlite>"
python -m toss_trading.cli.foundation_audit `
  --db "<new-replay.sqlite>" `
  --profile v1-funded-read-only
```

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

주문 상세의 execution summary는 누적 snapshot으로 저장하고, 장부 반영은 snapshot 간 delta로 계산합니다.

## Raw API Response Requirement

계좌, 보유, 주문, buying power, sellable quantity, 수수료 응답은 정규화하기 전에 `raw_api_response`에 `source_type='broker'`로 저장합니다. 내부 장부가 틀렸을 때 원본 응답을 재생할 수 있어야 하며, request/response hash와 Toss `requestId`를 함께 남깁니다.

## Live Trading Blockers

다음 중 하나라도 발생하면 live 신규 주문을 차단합니다.

- 브로커 스냅샷 조회 실패
- OPEN 주문 상태 불명
- 대상 주문 ID의 상세 응답 누락
- holdings와 내부 position ledger 불일치
- buying power 조회 실패
- buying power가 내부 available cash와 정책 허용 범위를 벗어남
- 주문 timeout 뒤 기존 주문 접수 여부 확인 불가
- execution delta가 음수 또는 비정상

## Foundation Completion Evidence

이번 foundation 단계는 다음 증거가 있어야 완료로 봅니다.

- `python -m toss_trading.cli.foundation_snapshot` 성공
- `python -m toss_trading.cli.foundation_audit`가 `foundation_audit=ok` 반환
- `runtime/foundation_account_state.sqlite`에 `raw_api_response`와 정규화 snapshot 저장
- broker 접근 실패 시 `source_health_snapshot`에 `blocked/error`와 운영 action 저장
- `runtime/foundation_account_state_report.txt`에 holdings count, open orders count, buying power, blockers 출력
- `blockers=['none']` 또는 명확히 설명 가능한 비거래 blocker만 존재

## Foundation v1 Funded Read-Only Evidence

v1은 빈 계좌 검증이 아닙니다. 실제 현금, 보유종목, 수동 주문, 체결, 수수료, 결제일이 있는 상태에서 다음 명령이 통과해야 합니다.

```powershell
$env:PYTHONPATH='src'
python -m toss_trading.cli.foundation_snapshot --target-order-id "<OPEN 상태에서 확보한 orderId>"
python -m toss_trading.cli.foundation_audit --profile v1-funded-read-only
```

v1 audit은 다음을 요구합니다.

- holdings count가 0보다 큼
- snapshot run에 `target_order_id`가 기록됨
- 동일 run에 정확한 `/api/v1/orders/{target_order_id}` 상세 응답이 raw로 저장됨
- 대상 주문에 0보다 큰 실제 체결수량이 존재
- cumulative filled quantity, cumulative filled amount, average filled price가 존재
- cumulative execution snapshot에서 execution delta가 생성됨
- commission snapshot이 존재
- settlement date가 존재
- 보유종목별 sellable quantity snapshot이 존재
- buying power가 broker constraint로 저장됨
- `blockers=['none']`

이 조건이 통과하기 전에는 paper order planner와 전략 신호를 연결하지 않습니다.
