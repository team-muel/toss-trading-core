from datetime import datetime, timezone
from decimal import Decimal

import pytest

from asset_management.decisions.governor import RiskGovernor
from asset_management.domain.enums import DataStatus, DecisionAction
from asset_management.domain.money import Money
from asset_management.domain.quantity import Quantity
from asset_management.time.asof import AsOfContext


def test_money_rejects_float_and_cross_currency_arithmetic():
    with pytest.raises(TypeError):
        Money.of(1.1, "USD")
    with pytest.raises(Exception):
        Money.of("1", "USD") + Money.of("1", "KRW")


def test_quantity_rounds_down_to_market_increment():
    assert Quantity.of("1.234").round_down("0.01").value == Decimal("1.23")


def test_asof_rejects_future_information():
    with pytest.raises(ValueError):
        AsOfContext("r", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc), "p", "params", "sha")


def test_governor_fails_closed_on_unknown_or_unreconciled_state():
    decision = RiskGovernor().evaluate(statuses=(DataStatus.UNKNOWN,), reconciled=False, limit_breached=False)
    assert decision.action is DecisionAction.BLOCK
    assert len(decision.reasons) == 2
