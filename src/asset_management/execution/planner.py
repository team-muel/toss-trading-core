"""Transforms approved target deltas into validated, idempotent order-intent plans."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from enum import StrEnum
from typing import Iterable, Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.domain.enums import OrderState
from asset_management.domain.errors import DataQualityError

from .intents import OrderIntent


class IntentSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


_ACTIVE_EXPOSURE_STATES = frozenset({
    OrderState.SUBMITTED, OrderState.ACKNOWLEDGED, OrderState.OPEN,
    OrderState.PARTIALLY_FILLED, OrderState.CANCEL_PENDING, OrderState.REPLACE_PENDING,
})


@dataclass(frozen=True, slots=True)
class OpenOrderExposure:
    """A reconciled active order, expressed as remaining rather than filled quantity."""

    broker_order_id: str
    instrument_id: str
    side: IntentSide
    ordered_quantity: Decimal
    cumulative_filled_quantity: Decimal
    state: OrderState

    def __post_init__(self) -> None:
        if (not isinstance(self.broker_order_id, str) or not self.broker_order_id.strip() or
                not isinstance(self.instrument_id, str) or not self.instrument_id.strip() or
                not isinstance(self.side, IntentSide) or self.state not in _ACTIVE_EXPOSURE_STATES or
                any(not isinstance(value, Decimal) or not value.is_finite() or value < 0
                    for value in (self.ordered_quantity, self.cumulative_filled_quantity)) or
                self.ordered_quantity <= 0 or self.cumulative_filled_quantity >= self.ordered_quantity):
            raise DataQualityError("OPEN_ORDER_EXPOSURE_INVALID")

    @property
    def remaining_quantity(self) -> Decimal:
        return self.ordered_quantity - self.cumulative_filled_quantity


def net_open_order_quantities(exposures: Iterable[OpenOrderExposure]) -> dict[str, Decimal]:
    """Return signed remaining exposure; unknown, duplicate, and crossed orders fail closed."""
    try:
        items = tuple(exposures)
    except TypeError as exc:
        raise DataQualityError("OPEN_ORDER_EXPOSURE_INVALID") from exc
    if (any(not isinstance(item, OpenOrderExposure) for item in items) or
            len({item.broker_order_id for item in items}) != len(items)):
        raise DataQualityError("OPEN_ORDER_EXPOSURE_INVALID")
    sides_by_instrument: dict[str, set[IntentSide]] = {}
    totals: dict[str, Decimal] = {}
    for item in items:
        sides_by_instrument.setdefault(item.instrument_id, set()).add(item.side)
        if len(sides_by_instrument[item.instrument_id]) != 1:
            raise DataQualityError("OPEN_ORDER_EXPOSURE_CONFLICT")
        signed = item.remaining_quantity if item.side is IntentSide.BUY else -item.remaining_quantity
        totals[item.instrument_id] = totals.get(item.instrument_id, Decimal(0)) + signed
    return totals


@dataclass(frozen=True, slots=True)
class ExecutableQuote:
    price: Decimal
    observed_at: datetime
    valid_until: datetime
    session_open: bool

    def __post_init__(self) -> None:
        if (not isinstance(self.price, Decimal) or not self.price.is_finite() or self.price <= 0 or
                not isinstance(self.observed_at, datetime) or not isinstance(self.valid_until, datetime) or
                self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None or
                self.valid_until.tzinfo is None or self.valid_until.utcoffset() is None or
                type(self.session_open) is not bool):
            raise DataQualityError("EXECUTION_QUOTE_INVALID")
        observed, valid = self.observed_at.astimezone(timezone.utc), self.valid_until.astimezone(timezone.utc)
        if valid <= observed:
            raise DataQualityError("EXECUTION_QUOTE_INVALID")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "valid_until", valid)


@dataclass(frozen=True, slots=True)
class InstrumentOrderRule:
    lot_size: Decimal
    price_tick: Decimal
    minimum_quantity: Decimal
    minimum_notional: Decimal
    estimated_fee_rate: Decimal
    fee_tick: Decimal

    def __post_init__(self) -> None:
        values = (self.lot_size, self.price_tick, self.minimum_quantity, self.minimum_notional,
                  self.estimated_fee_rate, self.fee_tick)
        if (any(not isinstance(value, Decimal) or not value.is_finite() or value < 0 for value in values) or
                self.lot_size <= 0 or self.price_tick <= 0 or self.fee_tick <= 0):
            raise DataQualityError("ORDER_RULE_INVALID")


@dataclass(frozen=True, slots=True)
class PlannedOrderIntent:
    order_intent_id: str
    client_order_id: str
    instrument_id: str
    side: IntentSide
    quantity: Decimal
    notional_amount: Decimal
    limit_price: Decimal
    expected_fee: Decimal
    quote_observed_at: datetime
    source_order_intent: OrderIntent
    content_hash: str

    def __post_init__(self) -> None:
        if (not self.order_intent_id.startswith("intent-") or not self.client_order_id.startswith("client-") or
                not isinstance(self.side, IntentSide) or not isinstance(self.source_order_intent, OrderIntent) or
                not isinstance(self.instrument_id, str) or not self.instrument_id.strip() or
                any(not isinstance(value, Decimal) or not value.is_finite() or value <= 0
                    for value in (self.quantity, self.notional_amount, self.limit_price)) or
                not isinstance(self.expected_fee, Decimal) or not self.expected_fee.is_finite() or self.expected_fee < 0 or
                not isinstance(self.quote_observed_at, datetime) or self.quote_observed_at.tzinfo is None or
                self.quote_observed_at.utcoffset() is None or len(self.content_hash) != 64):
            raise DataQualityError("PLANNED_ORDER_INTENT_INVALID")
        observed = self.quote_observed_at.astimezone(timezone.utc)
        object.__setattr__(self, "quote_observed_at", observed)
        if self.notional_amount != self.quantity * self.limit_price:
            raise DataQualityError("PLANNED_ORDER_INTENT_INVALID")
        expected_hash = digest(canonical(self.content_body()))
        if (self.content_hash != expected_hash or self.order_intent_id != f"intent-{expected_hash}" or
                self.client_order_id != f"client-{expected_hash}"):
            raise DataQualityError("PLANNED_ORDER_INTENT_INVALID")

    def content_body(self) -> dict[str, str]:
        """Fields bound to the deterministic order and client identifiers."""
        source = self.source_order_intent
        return {
            "source_risk_decision_id": source.risk_authorization.risk_decision_id,
            "target_hash": source.portfolio_target_hash,
            "instrument_id": self.instrument_id,
            "side": self.side.value,
            "quantity": str(self.quantity),
            "notional_amount": str(self.notional_amount),
            "limit_price": str(self.limit_price),
            "expected_fee": str(self.expected_fee),
            "quote_observed_at": self.quote_observed_at.isoformat(),
        }

    def payload(self) -> dict[str, str]:
        """Stable JSON representation; this is planning evidence, never a broker request."""
        return {
            "order_intent_id": self.order_intent_id,
            "client_order_id": self.client_order_id,
            **self.content_body(),
            "content_hash": self.content_hash,
        }


def plan_order_intents(*, source: OrderIntent, nav: Decimal, quotes: Mapping[str, ExecutableQuote],
                       current_quantities: Mapping[str, Decimal], open_order_quantities: Mapping[str, Decimal],
                       rules: Mapping[str, InstrumentOrderRule], evaluated_at: datetime) -> tuple[PlannedOrderIntent, ...]:
    """Net target deltas against OPEN/partial-fill exposure; never submits an order."""
    if (not isinstance(source, OrderIntent) or not isinstance(nav, Decimal) or not nav.is_finite() or nav <= 0 or
            not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None):
        raise DataQualityError("ORDER_PLANNER_INPUT_INVALID")
    instant = evaluated_at.astimezone(timezone.utc)
    result: list[PlannedOrderIntent] = []
    for target in source.target_weights:
        instrument = target.instrument_id
        quote, rule = quotes.get(instrument), rules.get(instrument)
        current, open_quantity = current_quantities.get(instrument), open_order_quantities.get(instrument, Decimal(0))
        if (not isinstance(quote, ExecutableQuote) or not isinstance(rule, InstrumentOrderRule) or
                not isinstance(current, Decimal) or not current.is_finite() or
                not isinstance(open_quantity, Decimal) or not open_quantity.is_finite()):
            raise DataQualityError("ORDER_PLANNER_INPUT_INVALID")
        if not quote.session_open:
            raise DataQualityError("ORDER_SESSION_CLOSED")
        if instant < quote.observed_at:
            raise DataQualityError("ORDER_QUOTE_FROM_FUTURE")
        if instant >= quote.valid_until:
            raise DataQualityError("ORDER_QUOTE_STALE")
        desired = (nav * target.target / quote.price / rule.lot_size).to_integral_value(rounding=ROUND_DOWN) * rule.lot_size
        delta = desired - current - open_quantity
        if delta == 0:
            continue
        quantity = (abs(delta) / rule.lot_size).to_integral_value(rounding=ROUND_DOWN) * rule.lot_size
        limit = (quote.price / rule.price_tick).to_integral_value(rounding=ROUND_DOWN) * rule.price_tick
        notional = quantity * limit
        if quantity < rule.minimum_quantity or notional < rule.minimum_notional:
            continue
        side = IntentSide.BUY if delta > 0 else IntentSide.SELL
        fee = ((notional * rule.estimated_fee_rate / rule.fee_tick).to_integral_value(rounding=ROUND_UP)
               * rule.fee_tick)
        body = {"source_risk_decision_id": source.risk_authorization.risk_decision_id,
                "target_hash": source.portfolio_target_hash, "instrument_id": instrument, "side": side.value,
                "quantity": str(quantity), "notional_amount": str(notional), "limit_price": str(limit),
                "expected_fee": str(fee), "quote_observed_at": quote.observed_at.isoformat()}
        content_hash = digest(canonical(body))
        result.append(PlannedOrderIntent(f"intent-{content_hash}", f"client-{content_hash}", instrument, side,
                                         quantity, notional, limit, fee, quote.observed_at, source, content_hash))
    return tuple(sorted(result, key=lambda item: item.instrument_id))
