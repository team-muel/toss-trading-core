"""AMA-49 event-risk controls that can only constrain existing decisions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum

from asset_management.domain.decimal import exact_decimal
from asset_management.domain.errors import DataQualityError, InvariantViolation


class EventAction(StrEnum):
    REDUCE = "REDUCE"
    DEFER = "DEFER"
    BLOCK = "BLOCK"


class EventType(StrEnum):
    EARNINGS = "EARNINGS"
    FOMC = "FOMC"
    MACRO_RELEASE = "MACRO_RELEASE"


def _aware(value: datetime, reason: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvariantViolation(reason)
    return value.astimezone(timezone.utc)


def _lineage(values: tuple[str, ...], reason: str) -> tuple[str, ...]:
    if any(not isinstance(value, str) or not value.strip() for value in values) or len(values) != len(set(values)):
        raise InvariantViolation(reason)
    return tuple(sorted(values))


@dataclass(frozen=True, slots=True)
class EventRiskInput:
    event_id: str
    event_type: EventType
    scheduled_at: datetime
    as_of_utc: datetime
    action: EventAction
    uncertainty_buffer: Decimal
    liquidity_buffer: Decimal
    expected_return_overlay_lineage_ids: tuple[str, ...] = ()
    event_penalty_lineage_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not isinstance(self.event_type, EventType) or not isinstance(self.action, EventAction):
            raise InvariantViolation("EVENT_RISK_IDENTITY_INVALID")
        scheduled = _aware(self.scheduled_at, "EVENT_RISK_SCHEDULE_TIME_INVALID")
        as_of = _aware(self.as_of_utc, "EVENT_RISK_AS_OF_TIME_INVALID")
        uncertainty = exact_decimal(self.uncertainty_buffer)
        liquidity = exact_decimal(self.liquidity_buffer)
        overlays = _lineage(self.expected_return_overlay_lineage_ids, "EVENT_RISK_OVERLAY_LINEAGE_INVALID")
        penalties = _lineage(self.event_penalty_lineage_ids, "EVENT_RISK_PENALTY_LINEAGE_INVALID")
        if scheduled <= as_of or uncertainty < 0 or liquidity < 0:
            raise InvariantViolation("EVENT_RISK_INPUT_INVALID")
        if set(overlays) & set(penalties):
            raise DataQualityError("EVENT_RISK_LINEAGE_OVERLAP")
        object.__setattr__(self, "scheduled_at", scheduled)
        object.__setattr__(self, "as_of_utc", as_of)
        object.__setattr__(self, "uncertainty_buffer", uncertainty)
        object.__setattr__(self, "liquidity_buffer", liquidity)
        object.__setattr__(self, "expected_return_overlay_lineage_ids", overlays)
        object.__setattr__(self, "event_penalty_lineage_ids", penalties)


@dataclass(frozen=True, slots=True)
class EventRiskControl:
    event_id: str
    action: EventAction
    uncertainty_buffer: Decimal
    liquidity_buffer: Decimal
    scheduled_at: datetime
    event_type: EventType

    def risk_input_flags(self) -> dict[str, bool]:
        """Map the event constraint to existing Risk Governor inputs only."""
        if self.action is EventAction.REDUCE:
            return {"event_risk_high": True}
        if self.action is EventAction.DEFER:
            return {"event_risk_high": True, "defer_execution": True}
        return {"event_risk_blocked": True}


def assess_event_risk(value: EventRiskInput, *, decision_horizon_end: datetime) -> EventRiskControl | None:
    """Return a constraint only when the event occurs in the decision horizon.

    The result deliberately contains no return, alpha, or portfolio-weight field.
    """
    end = _aware(decision_horizon_end, "EVENT_RISK_HORIZON_TIME_INVALID")
    if not isinstance(value, EventRiskInput):
        raise DataQualityError("EVENT_RISK_INPUT_REQUIRED")
    if end <= value.as_of_utc:
        raise DataQualityError("EVENT_RISK_HORIZON_INVALID")
    if value.scheduled_at > end:
        return None
    return EventRiskControl(value.event_id, value.action, value.uncertainty_buffer,
                            value.liquidity_buffer, value.scheduled_at, value.event_type)


def event_risk_action(*, event_imminent: bool, action: EventAction | None) -> EventAction | None:
    """Compatibility helper; rich calls should use EventRiskInput and assess_event_risk."""
    if not event_imminent:
        return None
    if not isinstance(action, EventAction):
        raise DataQualityError("EVENT_RISK_ACTION_REQUIRED")
    return action
