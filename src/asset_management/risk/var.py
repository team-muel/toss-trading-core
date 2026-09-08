"""Historical portfolio VaR using positive-loss convention."""
from decimal import Decimal, ROUND_CEILING
from asset_management.domain.errors import DataQualityError

def portfolio_returns(panel,weights):
    weights=tuple(weights)
    if len(weights)!=len(panel.instruments): raise DataQualityError("RISK_WEIGHT_INVALID")
    return tuple(sum(row[i]*weights[i] for i in range(len(weights))) for row in panel.returns)

def historical_var(returns,confidence=Decimal("0.95")):
    values=sorted(returns)
    if not values or not Decimal(0)<confidence<Decimal(1): raise DataQualityError("TAIL_RISK_INPUT_INVALID")
    count=max(1,int(((Decimal(1)-confidence)*len(values)).to_integral_value(rounding=ROUND_CEILING)))
    index=count-1
    return max(Decimal(0),-values[index])
