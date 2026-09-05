from datetime import datetime, timedelta, timezone
from decimal import Decimal
from dataclasses import replace
import pytest

from asset_management.domain.errors import DataQualityError
from asset_management.pricing import *
from asset_management.pricing.factors import require_separate_timing_overlay
from asset_management.quality.models import QualityStatus

D = Decimal
NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)

def test_horizons_and_compounding():
    assert HORIZONS == (21, 63, 126, 252)
    assert annual_to_horizon(D("0.21"), 252) == D("0.21")
    assert abs(annual_to_horizon(D("0.21"), 63) - ((D("1.21").sqrt().sqrt()) - 1)) < D("1e-26")
    with pytest.raises(DataQualityError): annual_to_horizon(D("0.1"), 30)

def test_risk_free_curve_is_complete_and_point_in_time():
    points = [RiskFreePoint(NOW, h, D("0.03"), "treasury", QualityStatus.VALID) for h in HORIZONS]
    curve = RiskFreeCurve(points)
    assert curve.rate(horizon=63, information_cutoff=NOW) == D("0.03")
    with pytest.raises(DataQualityError): curve.rate(horizon=63, information_cutoff=NOW-timedelta(seconds=1))

def test_manual_capm_and_output_is_not_an_order():
    beta = BetaEstimate(D("1.2"), D("1.2"), D("0.1"), 252, 252, D("0.8"), NOW, QualityStatus.VALID, D(1))
    result = capm_required_return(instrument_id="ETF", risk_free_rate=D("0.03"), beta=beta,
                                  market_risk_premium=D("0.05"), horizon=252, as_of=NOW)
    assert result.required_return == D("0.09")
    assert not ({"order", "side", "BUY", "SELL"} & set(result.payload()))
    assert result.lower_bound <= result.required_return <= result.upper_bound

def test_beta_missing_fails_and_unstable_estimate_shrinks():
    with pytest.raises(DataQualityError): estimate_beta([D(".01")]*3, [D(".01")]*3, as_of=NOW)
    market = [D(i % 11 - 5)/100 for i in range(80)]
    asset = [D("2")*x + D((i*7)%13-6)/20 for i,x in enumerate(market)]
    beta = estimate_beta(asset, market, as_of=NOW)
    assert beta.standard_error > 0 and beta.observation_count == 80
    assert abs(beta.beta-D(1)) < abs(beta.raw_beta-D(1))

def _premiums():
    return {name: FactorPremium(name, D(".01"), D(".002"), NOW, NOW, "pit", QualityStatus.VALID)
            for name in FACTORS}

def test_multifactor_is_pit_and_preserves_uncertainty():
    loadings = {name: D(1) for name in FACTORS}
    result = multifactor_required_return(instrument_id="ETF", risk_free_rate=D(".03"), loadings=loadings,
                                         premiums=_premiums(), horizon=252, as_of=NOW, information_cutoff=NOW)
    assert result.required_return == D(".10") and result.estimation_uncertainty > 0
    future = _premiums(); future["VALUE"] = replace(future["VALUE"], available_at=NOW+timedelta(seconds=1))
    with pytest.raises(DataQualityError): multifactor_required_return(instrument_id="ETF", risk_free_rate=D(".03"), loadings=loadings, premiums=future, horizon=252, as_of=NOW, information_cutoff=NOW)

def test_overlays_cannot_double_count_and_timing_stays_separate():
    with pytest.raises(DataQualityError): require_distinct_factor_roles(required_return_factors={"MKT"}, expected_return_overlay_factors={"MKT"})
    assert require_separate_timing_overlay(long_horizon_premium=D(".08"), short_horizon_signal=D(".01")) == (D(".08"), D(".01"))

def test_black_litterman_equilibrium_views_and_gate():
    cov = ((D(".04"), D(".01")), (D(".01"), D(".09")))
    prior = equilibrium_returns(cov, (D(".6"), D(".4")), D("2"))
    assert prior == (D(".056"), D(".084"))
    with pytest.raises(DataQualityError): posterior_returns(cov, (D(".6"),D(".4")), D(2), (), capm_stable=False, supply_stable=True, market_caps_stable=True)
    view = BlackLittermanView((D(1),D(-1)), D(".03"), D(".5"))
    post = posterior_returns(cov,(D(".6"),D(".4")),D(2),(view,),capm_stable=True,supply_stable=True,market_caps_stable=True)
    assert post != prior

@pytest.mark.parametrize("field,bounds", [("revenue_growth",(D("-.2"),D(".4"))), ("operating_margin",(D(".01"),D(".5"))), ("fcf_margin",(D(".01"),D(".4"))), ("discount_rate",(D(".05"),D(".3"))), ("terminal_growth",(D("-.02"),D(".07")))])
def test_reverse_dcf_recovers_each_assumption(field,bounds):
    original = DcfAssumptions(D(1000), D(100))
    price = dcf_price(original)
    baseline = replace(original, **{field: (bounds[0]+bounds[1])/2})
    result = solve_implied(market_price=price, assumptions=baseline, field=field, lower=bounds[0], upper=bounds[1])
    assert abs(result.implied_value-getattr(original,field)) < D(".00002")

def test_reverse_dcf_fails_closed_without_bracket():
    with pytest.raises(DataQualityError): solve_implied(market_price=D(10000), assumptions=DcfAssumptions(D(100),D(10)), field="revenue_growth", lower=D(0), upper=D(".01"))
