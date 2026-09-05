"""Policy-defined historical and hypothetical stress scenarios."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping
from asset_management.domain.errors import DataQualityError

REQUIRED_STRESS_SCENARIOS=("US_EQUITY_MINUS_10","NASDAQ_MINUS_20","SEMICONDUCTOR_MINUS_30",
    "LONG_RATE_PLUS_100BP","CREDIT_SPREAD_WIDENING","USDKRW_SHOCK","CORRELATION_SPIKE",
    "LIQUIDITY_DETERIORATION","VOLUME_TRIPLE")

@dataclass(frozen=True)
class StressScenario:
    name: str
    asset_shocks: Mapping[str,Decimal]
    fx_shocks: Mapping[str,Decimal]
    liquidity_multiplier: Decimal=Decimal(1)

def stress_loss(weights, instruments, scenario: StressScenario):
    if (len(weights)!=len(instruments) or scenario.liquidity_multiplier<1 or
            any(not x.is_finite() for x in weights) or
            any(name not in scenario.asset_shocks for name in instruments) or
            any(not x.is_finite() for x in (*scenario.asset_shocks.values(),*scenario.fx_shocks.values()))):
        raise DataQualityError("STRESS_INPUT_INVALID")
    pnl=sum(weights[i]*scenario.asset_shocks[instruments[i]] for i in range(len(weights)))
    return max(Decimal(0),-pnl),dict(scenario.fx_shocks)

def gap_stress(weight: Decimal, gap_return: Decimal) -> Decimal:
    if not weight.is_finite() or not gap_return.is_finite(): raise DataQualityError("GAP_STRESS_INVALID")
    return max(Decimal(0),-weight*gap_return)
