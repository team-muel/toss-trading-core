# Execution scheduling (AMA-110)

`execution.scheduling.plan_schedule` consumes an approved, netted parent intent
and re-evaluates microstructure at the planning instant. It produces paper
planning evidence only. The caller must supply reviewed broker order types and
their evidence ID; empty capabilities produce `BROKER_ORDER_TYPE_UNSUPPORTED`.
No broker write capability is enabled by this module.

Policies explicitly select Immediate, Passive Limit, TWAP-lite, POV-lite,
Open/Close-aware or Event-defer. All children are LIMIT plans. Immediate and
Passive Limit use one child. TWAP-lite divides lots evenly; POV-lite allocates
against each supplied window's volume cap. Open/Close-aware uses equal slices
while avoiding the calendar's opening and closing buffer. Event-defer produces
NO_TRADE. There is no silent conversion to another order type or strategy.

Higher urgency compresses the time window. Forecast expiry, transition start,
session close, event proximity, volatility, expected impact and order/ADV limits
constrain every schedule. Participation uses volume in instrument quantity units;
window volumes describe the clipped transition/session window before urgency
compression and are estimates, not guaranteed liquidity. Urgency scales volume
caps down proportionally with time. Child quantities sum to
the parent quantity, obey lot/minimum rules and preserve the parent price bound.
Insufficient capacity or any sub-minimum child rejects the entire schedule.

Each child needs fresh account, risk, quote, actual participation and capability
checks before execution. The cancel/replace contract is CANCEL_CONFIRM_REASSESS:
confirm cancellation before creating a replacement. Fallback is always DEFER.
Schedules are not a running scheduler, fill simulator or order authorization.
Deterministic child IDs bind the parent and planning inputs for replay evidence.
