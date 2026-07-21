# Foundation v1: Funded Read-Only Validation

## Purpose

Foundation v1 verifies that the system can read, store, explain, and audit a Toss account that has real account activity:

- cash or broker buying power
- holdings
- a user-submitted manual order
- a CLOSED order after execution
- filled quantity, filled amount, average filled price
- commission, tax, and settlement date
- sellable quantity for held symbols

This phase is still read-only. The system does not submit automatic orders. The user may place a very small manual order in the Toss app, and the system only reads the resulting account state.

## Entry Conditions

- Foundation v0 has passed with `foundation_snapshot=ok`.
- `python -m toss_trading.cli.foundation_audit --profile v0-empty-safe` returns `foundation_audit=ok`.
- Toss API credentials and allowed IP are configured.
- The account has enough cash for a tiny manual order.

## Manual Account Event

Use the Toss app, not this system, to create a small real account event. The
current approved OpenAPI contract does not rely on a CLOSED order list. Capture
the order ID while the manual order is still OPEN, then validate its detail.

Required event shape:

- Place one small supported US-listed stock or ETF limit order from
  `data/universe.csv` far enough from market to remain OPEN briefly.
- Run `foundation_snapshot` while it is OPEN and record its `orderId`.
- Fill the order only through the Toss app, then use that exact ID for the
  read-only detail validation. A cancel-only event cannot satisfy the execution,
  commission, and settlement evidence required by v1.
- Prefer an order size where fees, tax, and settlement fields are visible in the Toss API response.
- Do not run an automatic order planner in this phase.

## Validation Commands

After the manual order is visible in Toss:

```powershell
$env:PYTHONPATH='src'
python -m toss_trading.cli.foundation_snapshot --target-order-id "<captured-order-id>"
python -m toss_trading.cli.foundation_audit --profile v1-funded-read-only
```

The v1 audit must return:

```text
foundation_audit=ok
profile=v1-funded-read-only
```

## Required Evidence

The v1 audit must prove:

- at least one holding is normalized
- the captured target order is normalized from its detail response
- the captured target order has a filled quantity greater than zero
- the same `/api/v1/orders/{orderId}` detail raw response exists in the
  latest completed snapshot run
- at least one execution summary has cumulative filled quantity, cumulative filled amount, and average filled price
- at least one execution delta is generated from cumulative execution snapshots
- at least one commission snapshot exists
- at least one settlement date exists
- sellable quantity is stored for held symbols
- buying power is stored as broker buying-power constraint, not internal cash
- `blockers=['none']`

## Go/No-Go Rule

Paper order planner and strategy signals remain blocked until v1 passes.

If any v1 requirement fails:

- keep the system read-only
- inspect `raw_api_response` for the Toss response shape
- update normalization only after confirming the raw field mapping
- confirm the exact `/api/v1/orders/{target_order_id}` detail call was saved in the same completed snapshot run
- confirm repeated snapshots do not create duplicate execution deltas
- rerun `foundation_snapshot`
- rerun `foundation_audit --profile v1-funded-read-only`

## Non-Goals

Do not add these in v1:

- automatic order submission
- strategy signals
- NAV, option, news, or external market-data engines
- tax-lot automation beyond reading commission/tax/settlement fields from Toss order responses
