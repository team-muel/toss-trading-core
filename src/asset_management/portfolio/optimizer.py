"""Convex target-weight optimization; never creates orders."""
from decimal import Decimal
from asset_management.domain.errors import DataQualityError
from .costs import transaction_cost
from .models import ConstructionResult,PortfolioTarget
from .rebalance import apply_no_trade_bands,economic_trade_gate

def objective(weights,alpha,covariance,current,*,risk_aversion,linear_cost,impact_cost,
              turnover_penalty=Decimal(0),concentration_penalty=Decimal(0),
              transaction_cost_includes_turnover=True):
    if transaction_cost_includes_turnover and turnover_penalty>0:
        raise DataQualityError("TURNOVER_COST_DOUBLE_COUNTING")
    n=len(weights); delta=tuple(weights[i]-current[i] for i in range(n))
    variance=sum(weights[i]*covariance[i][j]*weights[j] for i in range(n) for j in range(n))
    return (sum(weights[i]*alpha[i] for i in range(n))-risk_aversion*variance/2
            -transaction_cost(delta,linear_cost,impact_cost)
            -turnover_penalty*sum(abs(x) for x in delta)
            -concentration_penalty*sum(x*x for x in weights))

def _project_simplex_cap(values,cap,cash_index,min_cash):
    result=[max(Decimal(0),min(cap,x)) for x in values]
    result[cash_index]=max(result[cash_index],min_cash)
    for _ in range(100):
        difference=Decimal(1)-sum(result)
        if abs(difference)<=Decimal("1e-18"): return tuple(result)
        eligible=[i for i,x in enumerate(result) if (difference>0 and x<cap) or (difference<0 and x>(min_cash if i==cash_index else 0))]
        if not eligible: raise DataQualityError("PORTFOLIO_PROJECTION_INFEASIBLE")
        share=difference/Decimal(len(eligible))
        for i in eligible:
            floor=min_cash if i==cash_index else Decimal(0)
            result[i]=max(floor,min(cap,result[i]+share))
    raise DataQualityError("PORTFOLIO_PROJECTION_DID_NOT_CONVERGE")

def optimize_weights(*,instruments,alpha,covariance,current,cash_instrument,max_single_weight,
                     min_cash_weight,risk_aversion,linear_cost,impact_cost,
                     turnover_penalty=Decimal(0),concentration_penalty=Decimal(0),
                     transaction_cost_includes_turnover=True,iterations=500,step=Decimal("0.05")):
    n=len(instruments)
    if (not all(len(x)==n for x in (alpha,current,linear_cost,impact_cost)) or len(covariance)!=n or
            any(len(row)!=n for row in covariance) or cash_instrument not in instruments or
            any(not x.is_finite() for x in (*alpha,*current,*linear_cost,*impact_cost,
                                            *(x for row in covariance for x in row))) or
            risk_aversion<0 or concentration_penalty<0 or step<=0 or iterations<1):
        raise DataQualityError("OPTIMIZER_INPUT_INVALID")
    # Validate economic specification before iterating.
    objective(current,alpha,covariance,current,risk_aversion=risk_aversion,linear_cost=linear_cost,
              impact_cost=impact_cost,turnover_penalty=turnover_penalty,
              concentration_penalty=concentration_penalty,
              transaction_cost_includes_turnover=transaction_cost_includes_turnover)
    weights=tuple(current); cash_index=instruments.index(cash_instrument)
    for iteration in range(1,iterations+1):
        sigma_w=tuple(sum(covariance[i][j]*weights[j] for j in range(n)) for i in range(n))
        gradient=[]
        for i in range(n):
            delta=weights[i]-current[i]; sign=Decimal(1) if delta>0 else Decimal(-1) if delta<0 else Decimal(0)
            cost_gradient=linear_cost[i]*sign+2*impact_cost[i]*delta
            if not transaction_cost_includes_turnover: cost_gradient+=turnover_penalty*sign
            gradient.append(alpha[i]-risk_aversion*sigma_w[i]-cost_gradient-2*concentration_penalty*weights[i])
        rate=step/Decimal(iteration).sqrt()
        weights=_project_simplex_cap(tuple(weights[i]+rate*gradient[i] for i in range(n)),
                                     max_single_weight,cash_index,min_cash_weight)
    return PortfolioTarget(tuple(instruments),weights,"RAW_TARGET")

def risk_scale(target: PortfolioTarget,*,cash_instrument,current_volatility,target_volatility,
               drawdown_multiplier,confidence_multiplier):
    values=(current_volatility,target_volatility,drawdown_multiplier,confidence_multiplier)
    if (cash_instrument not in target.instruments or any(not x.is_finite() or x<0 for x in values)
            or drawdown_multiplier>1 or confidence_multiplier>1):
        raise DataQualityError("RISK_SCALING_INPUT_INVALID")
    volatility_scale=min(Decimal(1),target_volatility/current_volatility) if current_volatility>0 else Decimal(1)
    scale=min(volatility_scale,drawdown_multiplier,confidence_multiplier)
    cash_index=target.instruments.index(cash_instrument)
    weights=[x*scale if i!=cash_index else Decimal(0) for i,x in enumerate(target.weights)]
    weights[cash_index]=Decimal(1)-sum(weights)
    return PortfolioTarget(target.instruments,tuple(weights),"RISK_CONSTRAINED_TARGET")

def choose_fail_safe(*,current: PortfolioTarget,current_valid: bool,risk_minimum: PortfolioTarget|None,
                     approved_fallback: PortfolioTarget|None,cash_target: PortfolioTarget|None):
    if current_valid: return current,"KEEP_CURRENT"
    if risk_minimum is not None: return risk_minimum,"RISK_MINIMUM"
    if approved_fallback is not None: return approved_fallback,"APPROVED_FALLBACK"
    if cash_target is not None: return cash_target,"CASH"
    return PortfolioTarget(current.instruments,current.weights,"NO_TRADE",("SOLVER_FAILURE",)),"NO_TRADE"

def finalize_targets(*,raw: PortfolioTarget,risk_constrained: PortfolioTarget,current_weights,
                     no_trade_bands,expected_benefit,expected_cost,uncertainty_buffer):
    if raw.instruments!=risk_constrained.instruments or not len(current_weights)==len(no_trade_bands)==len(raw.weights):
        raise DataQualityError("TARGET_STAGE_MISMATCH")
    economic=economic_trade_gate(expected_benefit,expected_cost,uncertainty_buffer)
    cash_index=raw.instruments.index("CASH") if "CASH" in raw.instruments else len(raw.weights)-1
    executable_weights=(apply_no_trade_bands(risk_constrained.weights,current_weights,no_trade_bands,cash_index)
                        if economic else tuple(current_weights))
    executable=PortfolioTarget(raw.instruments,executable_weights,"EXECUTABLE_TARGET",
                               () if economic else ("ECONOMIC_GATE_FAILED",))
    return ConstructionResult(raw,risk_constrained,executable,"SOLVED",expected_benefit,
                              expected_cost,uncertainty_buffer,not economic)

def constraint_projection(candidate: PortfolioTarget,current: PortfolioTarget,is_feasible,iterations=80):
    """Find the furthest feasible point on the candidate-to-current segment."""
    if candidate.instruments!=current.instruments or not is_feasible(current):
        raise DataQualityError("CONSTRAINT_PROJECTION_BASE_INVALID")
    if is_feasible(candidate): return PortfolioTarget(candidate.instruments,candidate.weights,"CONSTRAINT_PROJECTED")
    low=Decimal(0); high=Decimal(1); best=current.weights
    for _ in range(iterations):
        fraction=(low+high)/2
        weights=tuple(current.weights[i]+fraction*(candidate.weights[i]-current.weights[i]) for i in range(len(current.weights)))
        probe=PortfolioTarget(current.instruments,weights,"CONSTRAINT_PROBE")
        if is_feasible(probe): low=fraction; best=weights
        else: high=fraction
    return PortfolioTarget(current.instruments,best,"CONSTRAINT_PROJECTED",("CONSTRAINT_BOUNDARY",))
