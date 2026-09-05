"""Event risk can only reduce, defer, or block."""
from enum import StrEnum
from asset_management.domain.errors import DataQualityError
class EventAction(StrEnum): REDUCE="REDUCE"; DEFER="DEFER"; BLOCK="BLOCK"
def event_risk_action(*, event_imminent: bool, action: EventAction|None):
    if not event_imminent: return None
    if not isinstance(action,EventAction): raise DataQualityError("EVENT_RISK_ACTION_REQUIRED")
    return action
