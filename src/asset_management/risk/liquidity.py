"""AMA-48 liquidity capacity with separated order and position limits."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from enum import StrEnum

from asset_management.domain.decimal import exact_decimal
from asset_management.domain.enums import DataStatus
from asset_management.domain.errors import DataQualityError, InvariantViolation

@dataclass(frozen=True)
class LiquidityRisk:
    liquidation_days: int
    participation_rate: Decimal
    stressed_daily_volume: Decimal


class LiquidityDecisionStatus(StrEnum):
    KNOWN = "KNOWN"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class LiquidityRiskInput:
    """All amounts are notional amounts in one explicit currency."""
    quality: DataStatus
    order_notional: Decimal | None
    position_notional: Decimal | None
    average_daily_notional: Decimal | None
    order_participation_rate: Decimal | None
    liquidation_participation_rate: Decimal | None
    allowed_liquidation_days: int | None
    spread_bps: Decimal | None
    volatility: Decimal | None
    expected_liquidation_cost: Decimal | None
    currency: str
    return_horizon_days: int
    liquidity_horizon_days: int
    confidence: Decimal
    formula_version: str
    session: str

    def __post_init__(self) -> None:
        if (not isinstance(self.quality, DataStatus) or not self.currency.strip() or
                not self.formula_version.strip() or not self.session.strip() or
                type(self.return_horizon_days) is not int or self.return_horizon_days < 1 or
                type(self.liquidity_horizon_days) is not int or self.liquidity_horizon_days < 1):
            raise InvariantViolation("LIQUIDITY_CONTEXT_INVALID")
        confidence = exact_decimal(self.confidence)
        if not Decimal(0) < confidence <= Decimal(1):
            raise InvariantViolation("LIQUIDITY_CONFIDENCE_INVALID")
        object.__setattr__(self, "confidence", confidence)
        values = (self.order_notional, self.position_notional, self.average_daily_notional,
                  self.order_participation_rate, self.liquidation_participation_rate,
                  self.spread_bps, self.volatility, self.expected_liquidation_cost)
        if self.quality is not DataStatus.KNOWN:
            if any(value is not None for value in values) or self.allowed_liquidation_days is not None:
                raise InvariantViolation("LIQUIDITY_UNKNOWN_INPUT_MUST_NOT_FABRICATE_VALUES")
            return
        if self.allowed_liquidation_days is None or type(self.allowed_liquidation_days) is not int or self.allowed_liquidation_days < 1:
            raise InvariantViolation("LIQUIDITY_DAYS_INVALID")
        if any(value is None for value in values):
            raise InvariantViolation("LIQUIDITY_KNOWN_INPUT_MISSING")
        normalized = tuple(exact_decimal(value) for value in values)
        order, position, adv, order_rate, position_rate, spread, volatility, cost = normalized
        if (order < 0 or position < 0 or adv <= 0 or not Decimal(0) < order_rate <= 1 or
                not Decimal(0) < position_rate <= 1 or spread < 0 or volatility < 0 or cost < 0):
            raise InvariantViolation("LIQUIDITY_VALUE_INVALID")
        for name, value in zip(("order_notional", "position_notional", "average_daily_notional",
                                "order_participation_rate", "liquidation_participation_rate",
                                "spread_bps", "volatility", "expected_liquidation_cost"), normalized):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class LiquidityAssessment:
    status: LiquidityDecisionStatus
    reason_codes: tuple[str, ...]
    order_capacity_notional: Decimal | None
    position_capacity_notional: Decimal | None
    expected_liquidation_cost: Decimal | None
    currency: str
    return_horizon_days: int
    liquidity_horizon_days: int
    confidence: Decimal
    formula_version: str
    session: str


def assess_liquidity_risk(value: LiquidityRiskInput) -> LiquidityAssessment:
    if not isinstance(value, LiquidityRiskInput):
        raise DataQualityError("LIQUIDITY_INPUT_REQUIRED")
    if value.quality is not DataStatus.KNOWN:
        return LiquidityAssessment(LiquidityDecisionStatus.BLOCKED, (f"LIQUIDITY_{value.quality.value}",),
                                   None, None, None, value.currency, value.return_horizon_days,
                                   value.liquidity_horizon_days, value.confidence,
                                   value.formula_version, value.session)
    order_capacity = value.average_daily_notional * value.order_participation_rate
    position_capacity = (value.average_daily_notional * value.liquidation_participation_rate *
                         Decimal(value.allowed_liquidation_days or 0))
    reasons: list[str] = []
    if value.order_notional > order_capacity:
        reasons.append("ORDER_CAPACITY_EXCEEDED")
    if value.position_notional > position_capacity:
        reasons.append("POSITION_LIQUIDATION_CAPACITY_EXCEEDED")
    return LiquidityAssessment(LiquidityDecisionStatus.BLOCKED if reasons else LiquidityDecisionStatus.KNOWN,
                               tuple(reasons), order_capacity, position_capacity,
                               value.expected_liquidation_cost, value.currency,
                               value.return_horizon_days, value.liquidity_horizon_days,
                               value.confidence, value.formula_version, value.session)

def liquidity_risk(*, position_quantity: Decimal, average_daily_volume: Decimal,
                   max_participation: Decimal, volume_multiplier: Decimal=Decimal(1)) -> LiquidityRisk:
    if (any(not x.is_finite() for x in (position_quantity,average_daily_volume,max_participation,volume_multiplier))
            or average_daily_volume<=0 or not Decimal(0)<max_participation<=Decimal(1) or volume_multiplier<=0):
        raise DataQualityError("LIQUIDITY_RISK_INPUT_INVALID")
    stressed=average_daily_volume/volume_multiplier
    capacity=stressed*max_participation
    days=int((abs(position_quantity)/capacity).to_integral_value(rounding=ROUND_CEILING))
    participation=min(Decimal(1),abs(position_quantity)/stressed)
    return LiquidityRisk(days,participation,stressed)
