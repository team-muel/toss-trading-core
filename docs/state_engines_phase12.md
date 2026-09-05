# Phase 12 — Four State Engines

Phase 12 keeps market, company, portfolio, and system state as separate contracts. A state
is a versioned snapshot of components with uncertainty and lineage. It is not an order or
investment signal.

## Common contract

Every snapshot records `as_of`, named components, conservative aggregate confidence,
quality status, maximum freshness lag, the union of input feature IDs, policy version, and
code revision. It also records the operational restriction and its risk multiplier. State
identity is a deterministic SHA-256 over all components, their individual confidence,
quality, freshness and lineage, the full policy, calculation time, optional regime, and code
revision. Repeating the same calculation produces the same immutable catalog object.

Each `StateComponent` retains its own value, confidence, quality status, freshness, and
feature IDs. `recompute_component` replaces only the named component and rebuilds the
aggregate state; unrelated components remain unchanged.

## Market state

Market state preserves growth, inflation, liquidity, rates, credit, volatility, trend,
breadth, and valuation as nine continuous components. It does not collapse them into an
initial RISK_ON/RISK_OFF score. A coarse EXPANSION, CONTRACTION, or TRANSITION regime is
derived only when the caller explicitly requests it; the continuous components remain.

## Company state

Company state separately preserves growth, profitability, cash quality, leverage, relative
growth, estimates, valuation, and shareholder yield. It remains inactive for company
selection in the ETF-first release, but its contract and deterministic engine are available
for later activation after the Phase 9 inputs pass quality gates.

## Portfolio state

Portfolio state preserves NAV, cash by currency, current weights, sector/factor/currency
exposures, volatility, CVaR, drawdown, risk contribution, open orders, reserved cash, and
unsettled cash. Map and list values remain structured; they are not hidden inside one score.

## System state

System state contains broker, data, clock, reconciliation, storage, model, and execution
health. Its only aggregate output is one of NORMAL, CAUTION, REDUCED_RISK, NO_NEW_TRADES,
or HALTED. It cannot emit an instrument direction, BUY, or SELL.

## Uncertainty and quality action

The policy defines stale age and two confidence boundaries. Low confidence applies the
versioned reduced-risk multiplier; intermediate confidence applies the caution multiplier.
Blocking quality or stale components set the multiplier to zero and produce NO_NEW_TRADES.
A BLOCKED or HALTED system component produces HALTED. Uncertainty therefore changes the
allowed risk rather than appearing only as descriptive text.

## Completion criteria

- the four state engines and component contracts remain separate
- System State produces operational restrictions only
- any component can be recomputed independently
- every component remains visible; there is no single opaque state score
- uncertainty and data quality reduce risk or block new trades under versioned policy
