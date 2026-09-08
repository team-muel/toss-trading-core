"""Deterministic planning only; children require fresh authorization before execution."""
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from enum import StrEnum

from asset_management.data.immutable import canonical, digest
from asset_management.domain.enums import DecisionAction
from asset_management.domain.errors import DataQualityError
from .planner import PlannedOrderIntent, InstrumentOrderRule, IntentSide
from .microstructure import MicrostructureAssessment, MicrostructurePolicy, assess_microstructure


class ScheduleMode(StrEnum):
    IMMEDIATE = "IMMEDIATE"
    PASSIVE_LIMIT = "PASSIVE_LIMIT"
    TWAP_LITE = "TWAP_LITE"
    POV_LITE = "POV_LITE"
    OPEN_CLOSE_AWARE = "OPEN_CLOSE_AWARE"
    EVENT_DEFER = "EVENT_DEFER"


@dataclass(frozen=True)
class SchedulingPolicy:
    version: str
    mode: ScheduleMode
    max_participation: Decimal
    max_order_adv: Decimal
    max_impact_bps: Decimal
    max_volatility: Decimal
    child_count: int
    event_buffer: timedelta

    def __post_init__(self):
        if (not isinstance(self.version, str) or not self.version.strip() or
                not isinstance(self.mode, ScheduleMode) or
                any(not isinstance(v, Decimal) or not v.is_finite() or v <= 0 for v in
                    (self.max_participation, self.max_order_adv, self.max_impact_bps, self.max_volatility)) or
                self.max_participation > 1 or self.max_order_adv > 1 or
                type(self.child_count) is not int or not 1 <= self.child_count <= 1000 or
                not isinstance(self.event_buffer, timedelta) or self.event_buffer < timedelta(0)):
            raise DataQualityError("SCHEDULE_POLICY_INVALID")


@dataclass(frozen=True)
class ChildOrder:
    sequence: int
    quantity: Decimal
    limit_price: Decimal
    not_before: datetime
    expires_at: datetime
    volume_cap: Decimal


@dataclass(frozen=True)
class ExecutionSchedule:
    parent_id: str
    mode: ScheduleMode
    state: str
    reason: str
    input_hash: str
    children: tuple[ChildOrder, ...]
    participation: Decimal

    def payload(self):
        return dict(parent_id=self.parent_id, mode=self.mode.value, state=self.state, reason=self.reason,
                    input_hash=self.input_hash, participation=str(self.participation),
                    cancel_replace_policy="CANCEL_CONFIRM_REASSESS", fallback="DEFER",
                    requires_fresh_authorization=True,
                    children=[dict(child_id=f"{self.input_hash}:{c.sequence}", sequence=c.sequence,
                                   quantity=str(c.quantity), limit_price=str(c.limit_price),
                                   not_before=c.not_before.isoformat(), expires_at=c.expires_at.isoformat(),
                                   volume_cap=str(c.volume_cap)) for c in self.children])


def _stamp(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _evidence(value):
    if isinstance(value, dict):
        return {key: _evidence(item) for key, item in value.items()}
    if isinstance(value, (datetime, Decimal)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, timedelta):
        return value // timedelta(microseconds=1)
    if isinstance(value, (list, tuple)):
        return [_evidence(item) for item in value]
    return value.isoformat() if hasattr(value, 'isoformat') else value


def plan_schedule(*, parent: PlannedOrderIntent, market: MicrostructureAssessment,
                  micro_policy: MicrostructurePolicy, policy: SchedulingPolicy,
                  rule: InstrumentOrderRule, supported_order_types: frozenset[str],
                  capability_evidence_id: str, evaluated_at: datetime, transition_start: datetime,
                  valid_until: datetime, forecast_valid_until: datetime, urgency: Decimal,
                  adv_quantity: Decimal, volatility: Decimal, expected_impact_bps: Decimal,
                  window_volumes: tuple[Decimal, ...], event_times: tuple[datetime, ...]) -> ExecutionSchedule:
    """LIMIT-only paper plan. Forecast volume is a cap estimate, never a fill promise."""
    if (not isinstance(parent, PlannedOrderIntent) or not isinstance(market, MicrostructureAssessment) or
            not isinstance(micro_policy, MicrostructurePolicy) or not isinstance(policy, SchedulingPolicy) or
            not isinstance(rule, InstrumentOrderRule) or
            not isinstance(supported_order_types, frozenset) or
            any(not isinstance(t, str) for t in supported_order_types) or
            not isinstance(capability_evidence_id, str) or not capability_evidence_id.strip() or
            not isinstance(window_volumes, tuple) or not window_volumes or
            not isinstance(event_times, tuple) or
            any(not _stamp(t) for t in (evaluated_at, transition_start, valid_until, forecast_valid_until, *event_times)) or
            any(not isinstance(v, Decimal) or not v.is_finite() or v < 0
                for v in (urgency, adv_quantity, volatility, expected_impact_bps, *window_volumes)) or
            adv_quantity <= 0 or urgency > 1):
        raise DataQualityError("SCHEDULE_INPUT_INVALID")
    # Re-evaluate source evidence rather than trusting a previously allowed assessment.
    fresh = assess_microstructure(policy=micro_policy, side=parent.side, decision_price=market.decision_price,
                                  arrival_quote=market.arrival_quote, calendar=market.calendar,
                                  evaluated_at=evaluated_at)
    body = dict(parent=parent.payload(), market=fresh.payload(), policy=_evidence(asdict(policy)),
                calendar=_evidence(asdict(market.calendar)),
                micro_policy=_evidence(asdict(micro_policy)), rule=_evidence(asdict(rule)),
                capability_evidence_id=capability_evidence_id, supported=sorted(supported_order_types),
                evaluated_at=evaluated_at.isoformat(), transition_start=transition_start.isoformat(),
                valid_until=valid_until.isoformat(), forecast_valid_until=forecast_valid_until.isoformat(),
                urgency=str(urgency), adv=str(adv_quantity), volatility=str(volatility), impact=str(expected_impact_bps),
                volumes=list(map(str, window_volumes)), events=sorted(t.isoformat() for t in event_times))
    fingerprint = digest(canonical(body))

    def result(reason, children=()):
        return ExecutionSchedule(parent.order_intent_id, policy.mode, "PLANNED" if children else "NO_TRADE",
                                 reason, fingerprint, children, policy.max_participation)

    if market.arrival_quote.instrument_id != parent.instrument_id or market.side is not parent.side:
        return result("PARENT_MARKET_CONFLICT")
    if fresh.action is not DecisionAction.ALLOW:
        return result("MICROSTRUCTURE:" + ",".join(fresh.reason_codes))
    if "LIMIT" not in supported_order_types:
        return result("BROKER_ORDER_TYPE_UNSUPPORTED")
    calendar = market.calendar
    # This planner deliberately schedules only inside the verified regular session.
    if calendar.regular_open_at is None or calendar.regular_close_at is None:
        return result("SESSION_UNAVAILABLE")
    start = max(evaluated_at, transition_start, calendar.regular_open_at)
    end = min(valid_until, forecast_valid_until, calendar.regular_close_at)
    if policy.mode is ScheduleMode.OPEN_CLOSE_AWARE:
        start = max(start, calendar.regular_open_at + policy.event_buffer)
        end = min(end, calendar.regular_close_at - policy.event_buffer)
    if start >= end or calendar.session_at(evaluated_at) != market.arrival_quote.session:
        return result("EXECUTION_WINDOW_UNAVAILABLE")
    # Higher urgency compresses the available window, but never relaxes volume caps.
    time_fraction = Decimal(1) - urgency / Decimal(2)
    microseconds = (end - start) // timedelta(microseconds=1)
    end = start + timedelta(microseconds=int(Decimal(microseconds) * time_fraction))
    if policy.mode is ScheduleMode.EVENT_DEFER or any(
            start - policy.event_buffer <= t <= end + policy.event_buffer for t in event_times):
        return result("EVENT_DEFER")
    if parent.quantity / adv_quantity > policy.max_order_adv:
        return result("ORDER_ADV_LIMIT")
    if volatility > policy.max_volatility or expected_impact_bps > policy.max_impact_bps:
        return result("MARKET_COST_LIMIT")
    count = policy.child_count if policy.mode in (ScheduleMode.TWAP_LITE, ScheduleMode.POV_LITE,
                                                 ScheduleMode.OPEN_CLOSE_AWARE) else 1
    if len(window_volumes) != count:
        return result("WINDOW_VOLUME_MISSING")
    total_lots = parent.quantity / rule.lot_size
    if total_lots != total_lots.to_integral_value():
        return result("PARENT_LOT_CONFLICT")
    volumes = tuple(v * time_fraction for v in window_volumes)
    capacity = [int((v * policy.max_participation / rule.lot_size).to_integral_value(rounding=ROUND_DOWN))
                for v in volumes]
    lots = int(total_lots)
    if policy.mode is ScheduleMode.POV_LITE:
        allocation = []
        for cap in capacity:
            allocated = min(cap, lots)
            allocation.append(allocated)
            lots -= allocated
        if lots:
            return result("PARTICIPATION_CAPACITY_INSUFFICIENT")
    else:
        allocation = [int(total_lots) // count + (i < int(total_lots) % count) for i in range(count)]
    if any(a > c for a, c in zip(allocation, capacity)):
        return result("PARTICIPATION_CAPACITY_INSUFFICIENT")
    quote = market.arrival_quote
    passive = policy.mode is ScheduleMode.PASSIVE_LIMIT
    reference = (quote.bid if parent.side is IntentSide.BUY else quote.ask) if passive else fresh.executable_price_reference
    # Never exceed the parent buy ceiling or undercut its sell floor.
    price = min(reference, parent.limit_price) if parent.side is IntentSide.BUY else max(reference, parent.limit_price)
    rounding = ROUND_DOWN if parent.side is IntentSide.BUY else ROUND_UP
    price = (price / rule.price_tick).to_integral_value(rounding=rounding) * rule.price_tick
    if price <= 0:
        return result("CHILD_PRICE_INVALID")
    children = []
    for i, allocated in enumerate(allocation):
        if not allocated:
            continue
        qty = Decimal(allocated) * rule.lot_size
        if qty < rule.minimum_quantity or qty * price < rule.minimum_notional:
            return result("CHILD_BELOW_MINIMUM")
        left, right = start + (end - start) * i / count, start + (end - start) * (i + 1) / count
        if left >= right:
            return result("CHILD_WINDOW_INVALID")
        children.append(ChildOrder(i + 1, qty, price, left, right, volumes[i]))
    return result("SCHEDULE_READY", tuple(children))
