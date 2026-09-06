from decimal import Decimal
from datetime import datetime, timedelta, timezone
import pytest
from asset_management.domain.errors import DataQualityError
from asset_management.governance import (BenchmarkDefinition, InvestorMandate, InvestorMandateRegistry,
    MandateObjective, RiskPreference, WealthConvention)
from asset_management.portfolio import *

D=Decimal
NOW=datetime(2026,9,6,tzinfo=timezone.utc)
def optimizer_authority(risk_aversion=D(1),active_risk_aversion=D(0)):
    registry=InvestorMandateRegistry()
    for identifier in ("SPY","CASH"):
        registry.register_benchmark(BenchmarkDefinition(identifier,"1",identifier,True,"USD",21,"etf-us-v1",NOW,NOW+timedelta(days=1)))
    preference=RiskPreference(D(".2"),D(".25"),D(".1"),D(".2"),D(".1"),D(".1"),"concentration@1","turnover@1","tax@1","liquidity@1",D(0),D(5),D(0),D(5),"investor approval",("evidence:1",))
    mandate=InvestorMandate("household","1",MandateObjective.ABSOLUTE_WEALTH,"USD","USD",WealthConvention.NOMINAL,63,21,21,"SPY@1",None,"CASH@1",preference,NOW,NOW+timedelta(days=1))
    registry.register_mandate(mandate)
    return dict(mandate_registry=registry,mandate_authorization=registry.authorize_optimizer(mandate.key,risk_aversion=risk_aversion,active_risk_aversion=active_risk_aversion,at=NOW),authorized_at=NOW)
def target(weights=(D(".5"),D(".5")),stage="TEST"):
    return PortfolioTarget(("RISK","CASH"),weights,stage)
def policy(**changes):
    values=dict(cash_instrument="CASH",max_single_weight=D(".8"),min_cash_weight=D(".1"),
        max_volatility=D(".2"),max_cvar=D(".15"),max_stress_loss=D(".25"),max_turnover=D(".5"),
        max_trade_amount=D(1000),group_caps={"equity":D(".7")},factor_caps={"beta":D(".8")},
        currency_caps={"USD":D(1)},liquidity_caps={"illiquid":D(".5")},version="v1")
    values.update(changes); return ConstraintPolicy(**values)

def test_six_layer_building_blocks():
    strategic=strategic_allocation({"EQUITY":D(".5"),"BOND":D(".3"),"CASH":D(".1"),"GOLD_COMMODITY":D(".1")})
    tactical=tactical_overlay(strategic,{"EQUITY":D("-.05"),"BOND":D(0),"CASH":D(".05"),"GOLD_COMMODITY":D(0)},
                              {name:(D(0),D(1)) for name in strategic.instruments})
    selected=select_securities(D(".45"),{"A":D(2),"B":D(1)})
    assert tactical.weights[0]==D(".45") and selected.weights==(D(".30"),D(".15"))

def test_objective_has_half_variance_and_no_double_counting():
    value=objective((D(1),),(D(".1"),),((D(".04"),),),(D(1),),risk_aversion=D(2),linear_cost=(D(0),),impact_cost=(D(0),))
    assert value==D(".06")
    with pytest.raises(DataQualityError): objective((D(1),),(D(".1"),),((D(".04"),),),(D(1),),risk_aversion=D(2),linear_cost=(D(0),),impact_cost=(D(0),),turnover_penalty=D(".1"))

def test_optimizer_moves_toward_alpha_but_returns_weights_only():
    result=optimize_weights(instruments=("RISK","CASH"),alpha=(D(".10"),D(".01")),covariance=((D(".04"),D(0)),(D(0),D(".001"))),current=(D(".2"),D(".8")),cash_instrument="CASH",max_single_weight=D(".9"),min_cash_weight=D(".1"),risk_aversion=D(1),linear_cost=(D(".001"),D(".001")),impact_cost=(D(".01"),D(".01")),iterations=200,**optimizer_authority())
    assert result.weights[0]>D(".2") and sum(result.weights)==D(1)
    assert not ({"order","side"}&set(result.__dataclass_fields__))
    nearby=optimize_weights(instruments=("RISK","CASH"),alpha=(D(".1001"),D(".01")),covariance=((D(".04"),D(0)),(D(0),D(".001"))),current=(D(".2"),D(".8")),cash_instrument="CASH",max_single_weight=D(".9"),min_cash_weight=D(".1"),risk_aversion=D(1),linear_cost=(D(".001"),D(".001")),impact_cost=(D(".01"),D(".01")),iterations=200,**optimizer_authority())
    assert abs(nearby.weights[0]-result.weights[0])<D(".01")

def test_risk_scaling_changes_total_exposure_not_all_to_cash():
    result=risk_scale(target(),cash_instrument="CASH",current_volatility=D(".20"),target_volatility=D(".10"),drawdown_multiplier=D(".8"),confidence_multiplier=D(".9"))
    assert result.weights==(D(".25"),D(".75"))

def test_all_constraints_and_infeasible_are_explicit():
    exposures={"equity":(D(1),D(0))}; factors={"beta":(D(1),D(0))}; currencies={"USD":(D(1),D(1))}; liquidity={"illiquid":(D(1),D(0))}
    reasons=constraint_violations(target((D(".9"),D(".1"))),policy(),current_weights=(D(".5"),D(".5")),nav=D(10000),volatility=D(".3"),cvar=D(".2"),stress_loss=D(".3"),groups=exposures,factors=factors,currencies=currencies,liquidity=liquidity)
    assert {"SINGLE_WEIGHT_LIMIT","GROUP_LIMIT","FACTOR_LIMIT","LIQUIDITY_LIMIT","VOLATILITY_LIMIT","CVAR_LIMIT","STRESS_LOSS_LIMIT","TURNOVER_LIMIT","TRADE_AMOUNT_LIMIT"}<=set(reasons)
    with pytest.raises(DataQualityError): require_feasible(target((D(".9"),D(".1"))),policy(),current_weights=(D(".5"),D(".5")),nav=D(10000),volatility=D(".3"),cvar=D(".2"),stress_loss=D(".3"),groups=exposures,factors=factors,currencies=currencies,liquidity=liquidity)

def test_no_trade_band_and_economic_gate_preserve_three_targets():
    raw=target((D(".6"),D(".4")),"RAW_TARGET"); risk=target((D(".55"),D(".45")),"RISK_CONSTRAINED_TARGET")
    result=finalize_targets(raw=raw,risk_constrained=risk,current_weights=(D(".5"),D(".5")),no_trade_bands=(D(".02"),D(".1")),expected_benefit=D(".01"),expected_cost=D(".002"),uncertainty_buffer=D(".003"))
    assert result.raw_target is raw and result.risk_constrained_target is risk
    assert result.executable_target.weights==(D(".55"),D(".45")) and sum(result.executable_target.weights)==1
    blocked=finalize_targets(raw=raw,risk_constrained=risk,current_weights=(D(".5"),D(".5")),no_trade_bands=(D(0),D(0)),expected_benefit=D(".004"),expected_cost=D(".002"),uncertainty_buffer=D(".002"))
    assert blocked.no_trade and blocked.executable_target.weights==(D(".5"),D(".5"))

def test_solver_failure_waterfall_never_invents_equal_weight():
    current=target((D(".7"),D(".3")))
    chosen,reason=choose_fail_safe(current=current,current_valid=False,risk_minimum=None,approved_fallback=None,cash_target=None)
    assert reason=="NO_TRADE" and chosen.weights==current.weights
    cash=target((D(0),D(1)),"CASH")
    chosen,reason=choose_fail_safe(current=current,current_valid=False,risk_minimum=None,approved_fallback=None,cash_target=cash)
    assert (chosen,reason)==(cash,"CASH")

def test_target_quantity_is_not_an_order():
    assert target_quantities((D(".5"),D(".5")),(D(20),D(10)),D(1000),(D(1),D(5)))==(D(25),D(50))

def test_constraint_projection_uses_known_feasible_current_portfolio():
    current=target((D(".4"),D(".6"))); candidate=target((D(".9"),D(".1")))
    projected=constraint_projection(candidate,current,lambda item:item.weights[0]<=D(".7"))
    assert abs(projected.weights[0]-D(".7"))<D("1e-20") and sum(projected.weights)==1
