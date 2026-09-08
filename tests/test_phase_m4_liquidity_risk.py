from decimal import Decimal
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from asset_management.domain.enums import DataStatus
from asset_management.domain.errors import InvariantViolation
from asset_management.risk import LiquidityDecisionStatus, LiquidityRiskInput, assess_liquidity_risk
from asset_management.domain.economics import CurrencyBasis
from asset_management.risk import assess_historical_tail_risk, require_common_risk_context


def liquidity(**changes):
    values = dict(
        quality=DataStatus.KNOWN, order_notional=Decimal(100), position_notional=Decimal(800),
        average_daily_notional=Decimal(1_000), order_participation_rate=Decimal(".15"),
        liquidation_participation_rate=Decimal(".10"), allowed_liquidation_days=10,
        spread_bps=Decimal(12), volatility=Decimal(".25"), expected_liquidation_cost=Decimal(30),
        currency="USD", return_horizon_days=21, liquidity_horizon_days=10,
        confidence=Decimal(".95"), formula_version="liquidity@2", session="REGULAR",
    )
    values.update(changes)
    return LiquidityRiskInput(**values)


def test_order_and_position_capacity_are_separate_and_contextualized():
    result = assess_liquidity_risk(liquidity())
    assert result.status is LiquidityDecisionStatus.KNOWN
    assert result.order_capacity_notional == Decimal(150)
    assert result.position_capacity_notional == Decimal(1_000)
    assert result.currency == "USD" and result.return_horizon_days == 21
    assert result.liquidity_horizon_days == 10 and result.formula_version == "liquidity@2"


def test_capacity_breaches_block_without_zeroing_or_fabricating_cost():
    result = assess_liquidity_risk(liquidity(order_notional=Decimal(151), position_notional=Decimal(1001)))
    assert result.status is LiquidityDecisionStatus.BLOCKED
    assert result.reason_codes == ("ORDER_CAPACITY_EXCEEDED", "POSITION_LIQUIDATION_CAPACITY_EXCEEDED")
    assert result.expected_liquidation_cost == Decimal(30)


@pytest.mark.parametrize("quality", (DataStatus.UNKNOWN, DataStatus.MISSING, DataStatus.STALE, DataStatus.CONFLICT))
def test_unknown_liquidity_is_blocked_with_no_fabricated_numeric_capacity(quality):
    result = assess_liquidity_risk(liquidity(quality=quality, order_notional=None, position_notional=None,
                                             average_daily_notional=None, order_participation_rate=None,
                                             liquidation_participation_rate=None, allowed_liquidation_days=None,
                                             spread_bps=None, volatility=None, expected_liquidation_cost=None))
    assert result.status is LiquidityDecisionStatus.BLOCKED
    assert result.order_capacity_notional is None and result.position_capacity_notional is None
    assert result.expected_liquidation_cost is None


def test_known_missing_or_unknown_fabricated_values_fail_closed():
    with pytest.raises(InvariantViolation, match="LIQUIDITY_KNOWN_INPUT_MISSING"):
        liquidity(average_daily_notional=None)
    with pytest.raises(InvariantViolation, match="LIQUIDITY_UNKNOWN_INPUT_MUST_NOT_FABRICATE_VALUES"):
        liquidity(quality=DataStatus.UNKNOWN)
    with pytest.raises(InvariantViolation, match="LIQUIDITY_CONTEXT_INVALID"):
        liquidity(currency="")


def test_tail_risk_and_fx_related_risk_inputs_require_one_currency_and_horizon():
    tail = assess_historical_tail_risk((Decimal("-.1"), Decimal("-.05")), confidence=Decimal(".5"),
                                       currency_basis=CurrencyBasis.BASE, return_horizon_days=21,
                                       formula_version="historical-cvar@2")
    assert tail.unit == "RETURN" and tail.tail_risk.historical_cvar == Decimal(".1")
    require_common_risk_context(expected_return_currency=CurrencyBasis.BASE, risk_free_currency=CurrencyBasis.BASE,
                                covariance_currency=CurrencyBasis.BASE, factor_return_currency=CurrencyBasis.BASE,
                                active_return_currency=CurrencyBasis.BASE, return_horizon_days=21,
                                risk_free_horizon_days=21, covariance_horizon_days=21,
                                factor_horizon_days=21, active_horizon_days=21)
    with pytest.raises(Exception, match="RISK_CURRENCY_BASIS_MISMATCH"):
        require_common_risk_context(expected_return_currency=CurrencyBasis.BASE, risk_free_currency=CurrencyBasis.LOCAL,
                                    covariance_currency=CurrencyBasis.BASE, factor_return_currency=CurrencyBasis.BASE,
                                    active_return_currency=CurrencyBasis.BASE, return_horizon_days=21,
                                    risk_free_horizon_days=21, covariance_horizon_days=21,
                                    factor_horizon_days=21, active_horizon_days=21)


def test_liquidity_schema_keeps_blocked_values_explicitly_unavailable():
    root = Path(__file__).parents[1]
    schema = json.loads((root / "schemas/liquidity_risk_assessment.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    result = assess_liquidity_risk(liquidity(quality=DataStatus.UNKNOWN, order_notional=None,
                                             position_notional=None, average_daily_notional=None,
                                             order_participation_rate=None, liquidation_participation_rate=None,
                                             allowed_liquidation_days=None, spread_bps=None, volatility=None,
                                             expected_liquidation_cost=None))
    Draft202012Validator(schema).validate({
        "status": result.status.value, "reason_codes": list(result.reason_codes),
        "order_capacity_notional": None, "position_capacity_notional": None,
        "expected_liquidation_cost": None, "currency": result.currency,
        "return_horizon_days": result.return_horizon_days,
        "liquidity_horizon_days": result.liquidity_horizon_days,
        "confidence": str(result.confidence), "formula_version": result.formula_version,
        "session": result.session,
    })
