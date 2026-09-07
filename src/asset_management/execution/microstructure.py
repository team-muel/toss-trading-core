"""Fail-closed, low-frequency execution microstructure assessment."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from asset_management.domain.enums import DecisionAction
from asset_management.domain.errors import DataQualityError

from .planner import ExecutableQuote, IntentSide


class MarketSession(StrEnum):
    PREMARKET = "PREMARKET"
    REGULAR = "REGULAR"
    AFTERHOURS = "AFTERHOURS"
    CLOSED = "CLOSED"


class AuctionState(StrEnum):
    NONE = "NONE"
    OPENING = "OPENING"
    CLOSING = "CLOSING"


@dataclass(frozen=True, slots=True)
class ExchangeSessionWindow:
    """One source-backed exchange calendar record; unknown session data is never inferred."""

    exchange: str
    local_date: date
    timezone_name: str
    is_open: bool
    regular_open_at: datetime | None
    regular_close_at: datetime | None
    premarket_open_at: datetime | None
    afterhours_close_at: datetime | None
    source: str
    available_at: datetime

    def __post_init__(self) -> None:
        if (not isinstance(self.exchange, str) or not self.exchange.strip() or
                not isinstance(self.local_date, date) or not _text(self.timezone_name) or not isinstance(self.is_open, bool) or
                not isinstance(self.source, str) or not self.source.strip() or
                not _aware(self.available_at)):
            raise DataQualityError("MICROSTRUCTURE_SESSION_INVALID")
        try:
            zone = ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise DataQualityError("MICROSTRUCTURE_SESSION_INVALID") from exc
        fields = (self.regular_open_at, self.regular_close_at,
                  self.premarket_open_at, self.afterhours_close_at)
        if any(value is not None and not _aware(value) for value in fields):
            raise DataQualityError("MICROSTRUCTURE_SESSION_INVALID")
        object.__setattr__(self, "available_at", self.available_at.astimezone(timezone.utc))
        if not self.is_open:
            if any(value is not None for value in fields):
                raise DataQualityError("MICROSTRUCTURE_SESSION_INVALID")
            return
        if self.regular_open_at is None or self.regular_close_at is None:
            raise DataQualityError("MICROSTRUCTURE_SESSION_INVALID")
        if (not self.regular_open_at < self.regular_close_at or
                (self.premarket_open_at is not None and self.premarket_open_at > self.regular_open_at) or
                (self.afterhours_close_at is not None and self.regular_close_at > self.afterhours_close_at)):
            raise DataQualityError("MICROSTRUCTURE_SESSION_INVALID")
        if any(value.astimezone(zone).date() != self.local_date for value in fields if value is not None):
            raise DataQualityError("MICROSTRUCTURE_SESSION_INVALID")
        for name in ("regular_open_at", "regular_close_at", "premarket_open_at", "afterhours_close_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, value.astimezone(timezone.utc))

    def session_at(self, instant: datetime) -> MarketSession:
        if not _aware(instant):
            raise DataQualityError("MICROSTRUCTURE_TIME_INVALID")
        if not self.is_open:
            return MarketSession.CLOSED
        at = instant.astimezone(timezone.utc)
        if self.regular_open_at <= at < self.regular_close_at:
            return MarketSession.REGULAR
        if self.premarket_open_at is not None and self.premarket_open_at <= at < self.regular_open_at:
            return MarketSession.PREMARKET
        if self.afterhours_close_at is not None and self.regular_close_at <= at < self.afterhours_close_at:
            return MarketSession.AFTERHOURS
        return MarketSession.CLOSED


@dataclass(frozen=True, slots=True)
class DecisionPrice:
    """The model's price reference, retained separately from executable arrival pricing."""

    instrument_id: str
    price: Decimal
    observed_at: datetime
    available_at: datetime
    currency: str
    source: str

    def __post_init__(self) -> None:
        if (not _text(self.instrument_id) or not _positive(self.price) or not _aware(self.observed_at) or
                not _aware(self.available_at) or self.available_at < self.observed_at or
                not _text(self.currency) or not _text(self.source)):
            raise DataQualityError("DECISION_PRICE_INVALID")
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(timezone.utc))
        object.__setattr__(self, "available_at", self.available_at.astimezone(timezone.utc))


@dataclass(frozen=True, slots=True)
class ArrivalQuote:
    """Provider quote evidence, including conditions that may block execution."""

    instrument_id: str
    exchange: str
    bid: Decimal
    ask: Decimal
    observed_at: datetime
    available_at: datetime
    currency: str
    source: str
    session: MarketSession
    auction_state: AuctionState = AuctionState.NONE
    trading_halted: bool = False
    suspended: bool = False
    volatility_spike: bool = False
    abnormal_spread: bool = False

    def __post_init__(self) -> None:
        if (not _text(self.instrument_id) or not _text(self.exchange) or not _positive(self.bid) or
                not _positive(self.ask) or not _aware(self.observed_at) or not _aware(self.available_at) or
                self.available_at < self.observed_at or not _text(self.currency) or not _text(self.source) or
                not isinstance(self.session, MarketSession) or not isinstance(self.auction_state, AuctionState) or
                any(type(value) is not bool for value in (self.trading_halted, self.suspended,
                                                           self.volatility_spike, self.abnormal_spread))):
            raise DataQualityError("ARRIVAL_QUOTE_INVALID")
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(timezone.utc))
        object.__setattr__(self, "available_at", self.available_at.astimezone(timezone.utc))

    @property
    def crossed(self) -> bool:
        return self.bid > self.ask

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal(2)

    @property
    def spread_absolute(self) -> Decimal:
        return self.ask - self.bid

    @property
    def spread_bps(self) -> Decimal:
        return self.spread_absolute / self.mid * Decimal(10_000)


@dataclass(frozen=True, slots=True)
class MicrostructurePolicy:
    version: str
    max_quote_age: timedelta
    maximum_spread_bps: Decimal
    allow_premarket: bool = False
    allow_afterhours: bool = False
    allow_auctions: bool = False

    def __post_init__(self) -> None:
        if (not _text(self.version) or not isinstance(self.max_quote_age, timedelta) or
                self.max_quote_age <= timedelta(0) or not _nonnegative(self.maximum_spread_bps) or
                any(type(value) is not bool for value in (self.allow_premarket, self.allow_afterhours,
                                                           self.allow_auctions))):
            raise DataQualityError("MICROSTRUCTURE_POLICY_INVALID")


@dataclass(frozen=True, slots=True)
class MicrostructureAssessment:
    action: DecisionAction
    reason_codes: tuple[str, ...]
    side: IntentSide
    policy_version: str
    decision_price: DecisionPrice
    arrival_quote: ArrivalQuote
    calendar: ExchangeSessionWindow
    executable_price_reference: Decimal | None
    evaluated_at: datetime

    def __post_init__(self) -> None:
        if (self.action not in (DecisionAction.ALLOW, DecisionAction.DEFER, DecisionAction.BLOCK) or
                not self.reason_codes or len(set(self.reason_codes)) != len(self.reason_codes) or
                any(not _text(reason) for reason in self.reason_codes) or not isinstance(self.side, IntentSide) or
                not _text(self.policy_version) or
                not isinstance(self.decision_price, DecisionPrice) or not isinstance(self.arrival_quote, ArrivalQuote) or
                not isinstance(self.calendar, ExchangeSessionWindow) or
                not _aware(self.evaluated_at) or
                (self.action is DecisionAction.ALLOW) != (self.executable_price_reference is not None) or
                (self.executable_price_reference is not None and not _positive(self.executable_price_reference))):
            raise DataQualityError("MICROSTRUCTURE_ASSESSMENT_INVALID")
        object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(timezone.utc))

    def payload(self) -> dict[str, object]:
        quote = self.arrival_quote
        return {
            "action": self.action.value, "reason_codes": list(self.reason_codes), "side": self.side.value,
            "policy_version": self.policy_version,
            "decision_price": str(self.decision_price.price),
            "decision_observed_at": self.decision_price.observed_at.isoformat(),
            "decision_available_at": self.decision_price.available_at.isoformat(),
            "decision_currency": self.decision_price.currency, "decision_source": self.decision_price.source,
            "arrival_bid": str(quote.bid), "arrival_ask": str(quote.ask), "arrival_mid": str(quote.mid),
            "spread_absolute": str(quote.spread_absolute), "spread_bps": str(quote.spread_bps),
            "executable_price_reference": (str(self.executable_price_reference)
                                             if self.executable_price_reference is not None else None),
            "instrument_id": quote.instrument_id, "exchange": quote.exchange, "currency": quote.currency,
            "session": quote.session.value, "auction_state": quote.auction_state.value,
            "trading_halted": quote.trading_halted, "suspended": quote.suspended,
            "volatility_spike": quote.volatility_spike, "abnormal_spread": quote.abnormal_spread,
            "quote_observed_at": quote.observed_at.isoformat(), "quote_available_at": quote.available_at.isoformat(),
            "quote_source": quote.source, "calendar_source": self.calendar.source,
            "calendar_available_at": self.calendar.available_at.isoformat(),
            "evaluated_at": self.evaluated_at.isoformat(),
        }


def assess_microstructure(*, policy: MicrostructurePolicy, side: IntentSide, decision_price: DecisionPrice,
                          arrival_quote: ArrivalQuote, calendar: ExchangeSessionWindow,
                          evaluated_at: datetime) -> MicrostructureAssessment:
    """Assess a tradable arrival quote; no result from this function submits an order."""
    if (not isinstance(policy, MicrostructurePolicy) or not isinstance(side, IntentSide) or
            not isinstance(decision_price, DecisionPrice) or not isinstance(arrival_quote, ArrivalQuote) or
            not isinstance(calendar, ExchangeSessionWindow) or not _aware(evaluated_at)):
        raise DataQualityError("MICROSTRUCTURE_INPUT_INVALID")
    at = evaluated_at.astimezone(timezone.utc)
    reasons: list[str] = []
    if (decision_price.instrument_id != arrival_quote.instrument_id or
            decision_price.currency != arrival_quote.currency):
        reasons.append("DECISION_PRICE_REFERENCE_CONFLICT")
    if decision_price.available_at > at:
        reasons.append("DECISION_PRICE_NOT_AVAILABLE")
    if arrival_quote.exchange != calendar.exchange:
        reasons.append("EXCHANGE_CALENDAR_CONFLICT")
    if calendar.available_at > at:
        reasons.append("SESSION_CALENDAR_NOT_AVAILABLE")
    expected_session = calendar.session_at(arrival_quote.observed_at)
    if arrival_quote.session is not expected_session:
        reasons.append("QUOTE_SESSION_CONFLICT")
    if arrival_quote.crossed:
        reasons.append("CROSSED_QUOTE")
    if arrival_quote.trading_halted or arrival_quote.suspended:
        reasons.append("TRADING_HALTED_OR_SUSPENDED")
    if reasons:
        return _assessment(DecisionAction.BLOCK, reasons, side, policy.version, decision_price, arrival_quote,
                           calendar, None, at)
    if arrival_quote.available_at > at or at - arrival_quote.observed_at > policy.max_quote_age:
        reasons.append("QUOTE_STALE_OR_UNAVAILABLE")
    if arrival_quote.session is MarketSession.CLOSED:
        reasons.append("SESSION_CLOSED")
    if arrival_quote.session is MarketSession.PREMARKET and not policy.allow_premarket:
        reasons.append("PREMARKET_NOT_PERMITTED")
    if arrival_quote.session is MarketSession.AFTERHOURS and not policy.allow_afterhours:
        reasons.append("AFTERHOURS_NOT_PERMITTED")
    if arrival_quote.auction_state is not AuctionState.NONE and not policy.allow_auctions:
        reasons.append("AUCTION_NOT_PERMITTED")
    if arrival_quote.volatility_spike:
        reasons.append("VOLATILITY_SPIKE")
    if arrival_quote.abnormal_spread or arrival_quote.spread_bps > policy.maximum_spread_bps:
        reasons.append("ABNORMAL_SPREAD")
    if reasons:
        return _assessment(DecisionAction.DEFER, reasons, side, policy.version, decision_price, arrival_quote,
                           calendar, None, at)
    executable = arrival_quote.ask if side is IntentSide.BUY else arrival_quote.bid
    return _assessment(DecisionAction.ALLOW, ("EXECUTABLE_QUOTE_VALID",), side, policy.version, decision_price,
                       arrival_quote, calendar, executable, at)


def to_executable_quote(*, assessment: MicrostructureAssessment,
                        policy: MicrostructurePolicy) -> ExecutableQuote:
    """Bridge only an allowed arrival quote into the order-intent planner."""
    if (not isinstance(assessment, MicrostructureAssessment) or not isinstance(policy, MicrostructurePolicy) or
            assessment.action is not DecisionAction.ALLOW or assessment.executable_price_reference is None):
        raise DataQualityError("MICROSTRUCTURE_QUOTE_NOT_EXECUTABLE")
    quote = assessment.arrival_quote
    return ExecutableQuote(price=assessment.executable_price_reference, observed_at=quote.observed_at,
                           valid_until=quote.observed_at + policy.max_quote_age,
                           session_open=quote.session is not MarketSession.CLOSED)


def _assessment(action: DecisionAction, reasons: list[str] | tuple[str, ...], side: IntentSide,
                policy_version: str, decision: DecisionPrice, quote: ArrivalQuote,
                calendar: ExchangeSessionWindow, executable: Decimal | None, at: datetime) -> MicrostructureAssessment:
    return MicrostructureAssessment(action, tuple(sorted(set(reasons))), side, policy_version, decision, quote,
                                    calendar, executable, at)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _positive(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _nonnegative(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0
