"""Historical CVaR / expected shortfall with positive-loss convention."""
from decimal import Decimal, ROUND_CEILING
from asset_management.domain.errors import DataQualityError
from .models import TailRisk

def historical_tail_risk(returns,confidence=Decimal("0.95")):
    values=sorted(tuple(returns))
    if not values or not Decimal(0)<confidence<Decimal(1): raise DataQualityError("TAIL_RISK_INPUT_INVALID")
    count=max(1,int(((Decimal(1)-confidence)*len(values)).to_integral_value(rounding=ROUND_CEILING)))
    tail=values[:count]; var=max(Decimal(0),-tail[-1]); expected=max(Decimal(0),-sum(tail)/Decimal(len(tail)))
    return TailRisk(confidence,var,expected,expected)
