# Canonical quant factor templates (AMA-163)

`quant_factor_spec` returns an existing immutable ResearchSpec. It does not
introduce a second evaluator, data source, simulation loop, allocation rule or
forecast authority. All six hypotheses compile through the same canonical Lark
AST and use the repository PIT runner introduced in AMA-162.

## Actual implementation gap

Return, standard-deviation and ranking primitives already exist. The remaining
families require elementwise composition. This finite change adds function-call
`add`, `subtract`, `multiply`, `divide` and `negate` rather than an infix grammar or
an unbounded code execution feature. At least one binary operand must be a panel;
only scalar broadcasting is permitted. Two panels require the same instrument
keys and observation lengths. Missing remains missing; division by zero is
unavailable; nonfinite operands/results fail closed. Period identity is supplied
by the common PIT resolver, not inferred by position across unrelated datasets.

## Hypotheses and units

Let R(N) = index[t]/index[t-N]-1 and V = sample_std(one_session_returns,W)*sqrt(A),
where A is the declared annual trading-session count. Index data must already
have a governed currency/adjustment/calendar contract. All windows refer to the
same supplied trading-session axis. `skip_recent` shifts the input observations;
it is not a replacement for the simulator's information/effective-time delay.

| Family | Raw primitive before canonical rank |
| --- | --- |
| cross_sectional_momentum | R(long) |
| risk_adjusted_momentum | R(long) / V |
| short_term_reversal | -R(short) |
| low_volatility | -V |
| trend_acceleration | R(short)/short - R(long)/long |
| multi_factor_composite | weighted average of 2-3 centered individual ranks |

Return-per-session acceleration preserves the legacy research hypothesis; it is
not a claim of a continuously compounded growth model. Individual templates
produce ranks in [0,1]. A composite first centers each rank into [-1,1].
Composite weights are positive, finite and explicitly represented in the
canonical expression. The combination is a distinct preregistered hypothesis,
not evidence of factor independence or permission to combine production scores.
Production forecast combination remains owned by AMA-106 after OOS calibration.

## Golden comparisons and intentional corrections

Migration tests run the actual legacy `run_quant_factor_backtest` with synthetic
prices, explicit zero execution costs, weekly rebalances and a skipped recent
session, then compare each recorded rebalance score against the canonical
expression at the same signal date. All six families agree where their meaning
is unchanged, after mapping a single-factor rank to the legacy centered scale.

The following differences are deliberate, not hidden parity exceptions:

* Canonical rank averages ties; the legacy rank broke ties by ticker ordering.
* Undefined risk-adjusted momentum at zero volatility is unavailable, not zero.
* A composite requires all active component values; it cannot impute an absent
  component as neutral or silently change the component weights.
* Strict PIT, complete historical membership and missing-window rules from the
  canonical research runner remain in force.

Legacy top-k, equal/inverse-volatility allocation, regime filters, rebalancing,
cash, execution costs and strategy performance are NOT replaced by these
expression templates. The legacy production callers have not switched.
Retirement remains AMA-167, coordinated with the unpublished AMA-156 cleanup.

## Acceptance evidence

The initial 54 tests cover all six templates through actual immutable/PIT stores,
per-instrument price-unit invariance, future-column isolation, stable/changed
identities, zero volatility, invalid weights, arithmetic axis/finite/missing
semantics, positive controls and the legacy golden comparisons. The first
integration fixture did not include enough observations for lag plus simulation
delay; it correctly returned NO_OBSERVATIONS. The fixture was extended, not the
unavailability guard weakened. Full local suite: 1,503 passed.

This is executable model mechanism evidence, not real-data predictive acceptance.
No broker authority, live flag, cost model, runtime entrypoint or deployment is
changed. Exact-head CI/review/merge evidence is recorded in Linear and the PR.
