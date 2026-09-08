# AMA-124 — Investor Mandate Authority

Investor mandate fixes the objective, benchmark versions, wealth and currency conventions,
horizons, loss tolerance, liquidity reserve, risk budgets, policy keys, and allowed optimizer
`lambda` / `lambda_A` ranges before validation. The registry rejects same-version benchmark or
risk-policy substitutions and produces a content-addressed snapshot.

`optimize_weights` requires a current mandate authorization. The token binds the mandate,
primary benchmark, both risk-aversion parameters, registry hash, and UTC authorization time.
An optimizer request outside the preapproved range or after any registry change fails closed.
This contract does not enable live trading.
