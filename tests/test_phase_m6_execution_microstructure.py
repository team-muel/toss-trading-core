from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from asset_management.domain.enums import DecisionAction
from asset_management.domain.errors import DataQualityError
from asset_management.execution import (ArrivalQuote, AuctionState, DecisionPrice, ExchangeSessionWindow,
                                        IntentSide, MarketSession, MicrostructurePolicy,
                                        assess_microstructure, to_executable_quote)


NOW = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
D = Decimal


def calendar(**changes):
    values = dict(exchange="XNYS", local_date=date(2026, 9, 8), timezone_name="America/New_York", is_open=True,
                  regular_open_at=datetime(2026, 9, 8, 13, 30, tzinfo=timezone.utc),
                  regular_close_at=datetime(2026, 9, 8, 20, tzinfo=timezone.utc),
                  premarket_open_at=datetime(2026, 9, 8, 8, tzinfo=timezone.utc),
                  afterhours_close_at=datetime(2026, 9, 8, 22, tzinfo=timezone.utc),
                  source="calendar-provider", available_at=NOW - timedelta(hours=1))
    values.update(changes)
    return ExchangeSessionWindow(**values)


def decision(**changes):
    values = dict(instrument_id="SPY", price=D(99), observed_at=NOW - timedelta(days=1),
                  available_at=NOW - timedelta(days=1), currency="USD", source="daily-provider")
    values.update(changes)
    return DecisionPrice(**values)


def quote(**changes):
    values = dict(instrument_id="SPY", exchange="XNYS", bid=D("100.00"), ask=D("100.10"),
                  observed_at=NOW - timedelta(seconds=5), available_at=NOW - timedelta(seconds=2),
                  currency="USD", source="quote-provider", session=MarketSession.REGULAR)
    values.update(changes)
    return ArrivalQuote(**values)


def policy(**changes):
    values = dict(version="micro@1", max_quote_age=timedelta(seconds=30), maximum_spread_bps=D(25))
    values.update(changes)
    return MicrostructurePolicy(**values)


def assess(**changes):
    values = dict(policy=policy(), side=IntentSide.BUY, decision_price=decision(), arrival_quote=quote(),
                  calendar=calendar(), evaluated_at=NOW)
    values.update(changes)
    return assess_microstructure(**values)


def test_regular_quote_keeps_decision_arrival_and_executable_prices_separate():
    result = assess()
    assert result.action is DecisionAction.ALLOW
    assert result.decision_price.price == D(99)
    assert result.arrival_quote.mid == D("100.05")
    assert result.executable_price_reference == D("100.10")
    assert result.arrival_quote.spread_absolute == D(".10")
    assert result.arrival_quote.spread_bps == D("9.995002498750624687656171914")
    assert result.payload()["quote_source"] == "quote-provider"
    assert result.payload()["currency"] == "USD" and result.payload()["session"] == "REGULAR"
    executable_quote = to_executable_quote(assessment=result, policy=policy())
    assert executable_quote.price == D("100.10") and executable_quote.observed_at == result.arrival_quote.observed_at


@pytest.mark.parametrize("changes, action, reason", [
    ({"arrival_quote": quote(trading_halted=True)}, DecisionAction.BLOCK, "TRADING_HALTED_OR_SUSPENDED"),
    ({"arrival_quote": quote(bid=D("100.11"), ask=D("100.10"))}, DecisionAction.BLOCK, "CROSSED_QUOTE"),
    ({"arrival_quote": quote(observed_at=NOW - timedelta(minutes=2))}, DecisionAction.DEFER, "QUOTE_STALE_OR_UNAVAILABLE"),
    ({"arrival_quote": quote(abnormal_spread=True)}, DecisionAction.DEFER, "ABNORMAL_SPREAD"),
    ({"arrival_quote": quote(auction_state=AuctionState.OPENING)}, DecisionAction.DEFER, "AUCTION_NOT_PERMITTED"),
])
def test_halted_invalid_or_low_quality_quotes_fail_closed(changes, action, reason):
    result = assess(**changes)
    assert result.action is action and reason in result.reason_codes
    assert result.executable_price_reference is None
    with pytest.raises(DataQualityError, match="MICROSTRUCTURE_QUOTE_NOT_EXECUTABLE"):
        to_executable_quote(assessment=result, policy=policy())


def test_calendar_and_session_mismatch_block_instead_of_guessing_market_state():
    result = assess(arrival_quote=quote(session=MarketSession.AFTERHOURS))
    assert result.action is DecisionAction.BLOCK and "QUOTE_SESSION_CONFLICT" in result.reason_codes
    result = assess(arrival_quote=quote(exchange="XNAS"))
    assert result.action is DecisionAction.BLOCK and "EXCHANGE_CALENDAR_CONFLICT" in result.reason_codes


def test_premarket_requires_an_explicit_policy_exception():
    at = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    early_quote = quote(observed_at=at - timedelta(seconds=5), available_at=at - timedelta(seconds=2),
                        session=MarketSession.PREMARKET)
    early_calendar = calendar(available_at=at - timedelta(hours=1))
    assert assess(arrival_quote=early_quote, calendar=early_calendar, evaluated_at=at).action is DecisionAction.DEFER
    assert assess(policy=policy(allow_premarket=True), arrival_quote=early_quote,
                  calendar=early_calendar, evaluated_at=at).action is DecisionAction.ALLOW


def test_schema_covers_the_serialized_assessment():
    schema = json.loads((Path(__file__).parents[1] / "schemas/execution_microstructure.schema.json").read_text())
    assert set(schema["required"]) == set(assess().payload())
    assert schema["properties"]["action"]["enum"] == ["ALLOW", "DEFER", "BLOCK"]
