from dataclasses import dataclass


@dataclass(frozen=True)
class ReconciliationResult:
    item_type: str
    broker_value: float
    internal_value: float
    difference: float
    status: str
    action_required: str | None = None


def reconcile_value(
    item_type: str,
    broker_value: float,
    internal_value: float,
    tolerance: float,
) -> ReconciliationResult:
    difference = broker_value - internal_value
    if abs(difference) <= tolerance:
        return ReconciliationResult(
            item_type=item_type,
            broker_value=broker_value,
            internal_value=internal_value,
            difference=difference,
            status="ok",
        )
    return ReconciliationResult(
        item_type=item_type,
        broker_value=broker_value,
        internal_value=internal_value,
        difference=difference,
        status="failed",
        action_required="block_new_orders_and_reload_broker_snapshot",
    )
