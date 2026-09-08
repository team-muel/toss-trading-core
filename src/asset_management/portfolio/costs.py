"""Convex transaction-cost model."""
from decimal import Decimal
from asset_management.domain.errors import DataQualityError

def transaction_cost(delta,linear_cost,impact_cost):
    if (not (len(delta)==len(linear_cost)==len(impact_cost)) or
            any(not x.is_finite() for x in (*delta,*linear_cost,*impact_cost)) or
            any(x<0 for x in (*linear_cost,*impact_cost))):
        raise DataQualityError("TRANSACTION_COST_INPUT_INVALID")
    return sum(linear_cost[i]*abs(delta[i])+impact_cost[i]*delta[i]*delta[i] for i in range(len(delta)))
