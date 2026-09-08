"""Liquidity capacity and liquidation-cost risk."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from asset_management.domain.errors import DataQualityError

@dataclass(frozen=True)
class LiquidityRisk:
    liquidation_days: int
    participation_rate: Decimal
    stressed_daily_volume: Decimal

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
