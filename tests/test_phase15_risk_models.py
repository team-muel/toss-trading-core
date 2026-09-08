from datetime import date
from decimal import Decimal
import pytest
from asset_management.domain.errors import DataQualityError
from asset_management.risk import *
from asset_management.risk.covariance import is_psd
from asset_management.risk.cvar import historical_tail_risk
from asset_management.risk.drawdown import assess_drawdown
from asset_management.risk.factor_risk import EXPOSURES
from asset_management.risk.models import CovarianceEstimate

D=Decimal
def panel():
    obs={date(2026,1,1):{"A":D(".01"),"B":D(".02")},date(2026,1,2):{"A":D("-.01"),"B":D(".00")},date(2026,1,3):{"A":D(".02"),"B":D("-.01")}}
    return build_return_panel(instruments=("A","B"),observations=obs,total_return=True,currency_basis=CurrencyBasis.BASE)

def test_return_panel_basis_missing_prelisting_and_outliers():
    with pytest.raises(DataQualityError): build_return_panel(instruments=("A",),observations={date(2026,1,1):{"A":D(0)},date(2026,1,2):{"A":D(0)}},total_return=False,currency_basis=CurrencyBasis.LOCAL)
    obs={date(2025,1,1):{"A":D(".9")},date(2026,1,1):{"A":D(".9")},date(2026,1,2):{"A":D("-.9")}}
    result=build_return_panel(instruments=("A",),observations=obs,total_return=True,currency_basis=CurrencyBasis.LOCAL,listing_dates={"A":date(2026,1,1)},winsor_limits=(D("-.2"),D(".2")))
    assert result.prelisting_rows_excluded==1 and result.returns==((D(".2"),),(D("-.2"),))

def test_missing_is_never_imputed():
    obs={date(2026,1,1):{"A":D(0)},date(2026,1,2):{"A":None},date(2026,1,3):{"A":D(0)}}
    with pytest.raises(DataQualityError): build_return_panel(instruments=("A",),observations=obs,total_return=True,currency_basis=CurrencyBasis.BASE)

def test_fx_return_is_exact_and_separate():
    local,fx,base=decompose_base_currency_return(D(".10"),D(".05"))
    assert (local,fx,base)==(D(".10"),D(".05"),D(".1550"))

def test_sample_ewma_shrinkage_psd():
    sample=sample_covariance(panel()); ewma=ewma_covariance(panel(),D(".8"))
    target=((sample.matrix[0][0],D(0)),(D(0),sample.matrix[1][1]))
    shrunk=shrink_covariance(sample,target,D(".25"))
    assert sample.method=="SAMPLE" and ewma.method=="EWMA" and shrunk.psd and is_psd(shrunk.matrix)
    assert shrunk.matrix[0][1]==D(".75")*sample.matrix[0][1]
    assert sample.annualization_factor==252
    with pytest.raises(DataQualityError): shrink_covariance(sample,((D(1),D(2)),(D(2),D(1))),D(1))

def test_factor_and_stress_covariance_are_separate():
    factor=factor_covariance(((D(1),),(D(".5"),)),((D(".04"),),),(D(".01"),D(".02")))
    stressed=stress_covariance(factor,D(2),D(".3"))
    assert factor.method=="FACTOR" and not factor.stressed and stressed.stressed
    assert stressed.matrix[0][0]==D(4)*factor.matrix[0][0]

def test_safe_inverse_fallback_blocks_optimizer():
    singular=CovarianceEstimate(((D(1),D(1)),(D(1),D(1))),"TEST",2,True)
    inverse,fallback=safe_inverse(singular)
    gate=optimizer_risk_gate(covariance_valid=True,inverse_fallback=fallback,return_panel_valid=True,tail_risk_valid=True)
    assert fallback and not gate.optimizer_allowed

def test_portfolio_volatility_and_euler_contributions_reconcile():
    cov=CovarianceEstimate(((D(".04"),D(0)),(D(0),D(".09"))),"TEST",10,True)
    risk=portfolio_risk((D(".6"),D(".4")),cov)
    assert abs(risk.portfolio_volatility-D(".0288").sqrt())<D("1e-27")
    assert abs(sum(risk.component)-risk.portfolio_volatility)<D("1e-18")

def test_historical_var_cvar_expected_shortfall_positive_loss():
    tail=historical_tail_risk((D("-.10"),D("-.05"),D(0),D(".02")),D(".5"))
    assert tail.historical_var==D(".05") and tail.historical_cvar==tail.expected_shortfall==D(".075")

def test_exposure_stress_gap_and_event_controls():
    rows=[{name:D(1) for name in EXPOSURES},{name:D(0) for name in EXPOSURES}]
    assert aggregate_exposure((D(".6"),D(".4")),rows)["market_beta"]==D(".6")
    scenario=StressScenario("US -10%",{"A":D("-.10")},{"USD/KRW":D(".05")},D(3))
    loss,fx=stress_loss((D(1),),("A",),scenario)
    assert loss==D(".10") and fx["USD/KRW"]==D(".05") and gap_stress(D(".5"),D("-.2"))==D(".10")
    assert event_risk_action(event_imminent=True,action=EventAction.BLOCK)==EventAction.BLOCK
    assert len(REQUIRED_STRESS_SCENARIOS)==9
    liquid=liquidity_risk(position_quantity=D(1000),average_daily_volume=D(1000),max_participation=D(".1"),volume_multiplier=D(3))
    assert liquid.liquidation_days==30 and liquid.stressed_daily_volume==D(1000)/D(3)

def test_drawdown_keeps_diagnostics():
    result=assess_drawdown((D(100),D(120),D(90)),risk_estimation_error=D(".1"),regime_mismatch=D(".2"))
    assert result.drawdown==D("-.25") and result.risk_estimation_error==D(".1") and result.regime_mismatch==D(".2")
