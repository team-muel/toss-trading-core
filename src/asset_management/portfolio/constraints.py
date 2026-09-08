"""Policy-derived hard constraints with no hidden constants."""
from decimal import Decimal
from asset_management.domain.errors import DataQualityError
from .models import ConstraintPolicy,PortfolioTarget

def constraint_violations(target: PortfolioTarget,policy: ConstraintPolicy,*,current_weights,
                          nav: Decimal,volatility: Decimal,cvar: Decimal,stress_loss: Decimal,
                          groups=None,factors=None,currencies=None,liquidity=None):
    names=target.instruments; weights=target.weights; reasons=[]
    if len(current_weights)!=len(weights): raise DataQualityError("CONSTRAINT_DIMENSION_INVALID")
    if abs(sum(weights)-1)>Decimal("1e-12"): reasons.append("WEIGHTS_NOT_ONE")
    if any(x<0 for x in weights): reasons.append("LONG_ONLY_VIOLATION")
    if any(x>policy.max_single_weight for x in weights): reasons.append("SINGLE_WEIGHT_LIMIT")
    if policy.cash_instrument not in names or weights[names.index(policy.cash_instrument)]<policy.min_cash_weight: reasons.append("MIN_CASH_LIMIT")
    if volatility>policy.max_volatility: reasons.append("VOLATILITY_LIMIT")
    if cvar>policy.max_cvar: reasons.append("CVAR_LIMIT")
    if stress_loss>policy.max_stress_loss: reasons.append("STRESS_LOSS_LIMIT")
    turnover=sum(abs(weights[i]-current_weights[i]) for i in range(len(weights)))
    if turnover>policy.max_turnover: reasons.append("TURNOVER_LIMIT")
    if nav<=0 or any(abs(weights[i]-current_weights[i])*nav>policy.max_trade_amount for i in range(len(weights))): reasons.append("TRADE_AMOUNT_LIMIT")
    for exposures,caps,code in ((groups or {},policy.group_caps,"GROUP_LIMIT"),(factors or {},policy.factor_caps,"FACTOR_LIMIT"),(currencies or {},policy.currency_caps,"CURRENCY_LIMIT"),(liquidity or {},policy.liquidity_caps,"LIQUIDITY_LIMIT")):
        for key,values in exposures.items():
            if (len(values)!=len(weights) or any(not x.is_finite() for x in values) or key not in caps or
                    abs(sum(weights[i]*values[i] for i in range(len(weights))))>caps.get(key,Decimal(0))): reasons.append(code)
    return tuple(dict.fromkeys(reasons))

def require_feasible(*args,**kwargs):
    reasons=constraint_violations(*args,**kwargs)
    if reasons: raise DataQualityError("PORTFOLIO_INFEASIBLE:"+",".join(reasons))
