# AMA-54 Economic Trade Evidence

The canonical economic gate uses one explicit basis: `RETURN_UTILITY` or
`MONEY`. Benefit, cost, and uncertainty buffer must share that basis. Monetary
evidence additionally requires a positive NAV. A return-utility record cannot
silently carry NAV, preventing accidental comparison of a return to a cash cost.
The legacy numeric helper remains for existing replay records; new decision
paths use `EconomicTradeEvidence` and retain its formula version.
