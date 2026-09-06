from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from asset_management.domain.errors import DataQualityError
from asset_management.domain.horizon import DecayProfile, SignalValidity
from asset_management.governance import ModelDefinition, ModelRegistry, ModelScope, ModelStatus
from asset_management.expectations import *
from asset_management.expectations.engine import COMPONENTS
from asset_management.pricing.models import BetaEstimate
from asset_management.pricing.capm import capm_required_return
from asset_management.quality.models import QualityStatus

D=Decimal; NOW=datetime(2026,1,2,tzinfo=timezone.utc)
VALIDITY=SignalValidity(252,63,NOW+timedelta(days=30),DecayProfile.LINEAR)
CAPM_REGISTRY=ModelRegistry()
CAPM_MODEL=ModelDefinition("CAPM","1","required return",("input",),("required_return",),
                           (ModelScope.REQUIRED_RETURN,),("unstable",),date(2026,1,1),
                           date(2026,12,31),"owner")
CAPM_REGISTRY.register(CAPM_MODEL)
for _status in (ModelStatus.VALIDATED,ModelStatus.APPROVED,ModelStatus.ACTIVE):
    CAPM_REGISTRY.transition(CAPM_MODEL.key,_status,effective_at=NOW,
                             reason="test promotion",evidence_ids=("test:evidence",))
CAPM_AUTH=CAPM_REGISTRY.authorize(CAPM_MODEL.key,ModelScope.REQUIRED_RETURN,at=NOW)
def components(kind, point=D(".01"), uncertainty=D(".001"), confidence=D(".8")):
    return {name: ExpectedReturnComponent(name,point,uncertainty,confidence,(f"feature:{name}",),252,VALIDITY) for name in COMPONENTS[kind]}

@pytest.mark.parametrize("kind,count", [(AssetClass.EQUITY,5),(AssetClass.EQUITY_ETF,5),(AssetClass.BOND_ETF,5),(AssetClass.CASH,3),(AssetClass.COMMODITY_ETF,4)])
def test_distinct_asset_contracts(kind,count):
    result=expected_return(instrument_id="X",asset_class=kind,components=components(kind),horizon=252,as_of=NOW)
    assert len(result.components)==count and result.gross_expected_return==D(".01")*count

def test_wrong_asset_model_fails_closed():
    with pytest.raises(DataQualityError): expected_return(instrument_id="X",asset_class=AssetClass.CASH,components=components(AssetClass.EQUITY_ETF),horizon=252,as_of=NOW)

def test_gross_net_and_components():
    result=expected_return(instrument_id="X",asset_class=AssetClass.CASH,components=components(AssetClass.CASH),horizon=252,as_of=NOW,transaction_cost=D(".002"),tax_drag=D(".003"),fx_cost=D(".004"))
    assert result.gross_expected_return==D(".03") and result.net_expected_return==D(".021")
    assert sum(x.point_estimate for x in result.components)==result.gross_expected_return
    assert result.payload()["components"][0]["input_features"]

def test_shrinkage():
    assert shrink_estimate(D(".12"),D(".04"),D(".25"))==D(".06")
    assert shrink_estimate(D(".12"),D(".04"),D(0))==D(".04")
    item=shrink_component(raw_estimate=D(".12"),prior=D(".04"),confidence=D(".25"),
                          uncertainty=D(".01"),component_name="current_yield",
                          input_features=("yield",),horizon=252,validity=VALIDITY)
    assert item.point_estimate==D(".06")

def test_uncertainty_is_conservative_without_correlation_matrix():
    result=expected_return(instrument_id="X",asset_class=AssetClass.CASH,components=components(AssetClass.CASH,uncertainty=D(".01")),horizon=252,as_of=NOW,uncertainty_z=D(1))
    assert result.upper_bound-result.net_expected_return==D(".03")

def required(rate=D(".02")):
    beta=BetaEstimate(D(0),D(0),D(".001"),252,252,D(1),NOW,QualityStatus.VALID,D(1))
    return capm_required_return(instrument_id="X",risk_free_rate=rate,beta=beta,market_risk_premium=D(".05"),horizon=252,as_of=NOW,validity=VALIDITY,model_registry=CAPM_REGISTRY,authorization=CAPM_AUTH)

def test_alpha_keeps_inputs_separate():
    exp=expected_return(instrument_id="X",asset_class=AssetClass.CASH,components=components(AssetClass.CASH),horizon=252,as_of=NOW)
    alpha=calculate_alpha(exp,required(),as_of=NOW)
    assert (alpha.net_expected_return,alpha.required_return,alpha.alpha)==(D(".03"),D(".02"),D(".01"))

def test_interval_crossing_zero_abstains():
    exp=expected_return(instrument_id="X",asset_class=AssetClass.CASH,components=components(AssetClass.CASH,uncertainty=D(".02")),horizon=252,as_of=NOW)
    result=calculate_alpha(exp,required(),as_of=NOW)
    assert result.abstain and "ALPHA_INTERVAL_CROSSES_ZERO" in result.reason_codes

def test_expired_signal_abstains_even_when_values_are_otherwise_valid():
    exp=expected_return(instrument_id="X",asset_class=AssetClass.CASH,
                        components=components(AssetClass.CASH),horizon=252,as_of=NOW)
    result=calculate_alpha(exp,required(),as_of=VALIDITY.valid_until+timedelta(seconds=1))
    assert result.abstain and "SIGNAL_EXPIRED" in result.reason_codes

@pytest.mark.parametrize("flag,reason", [("model_conflict","MODEL_CONFLICT"),("event_risk","EVENT_RISK"),("feature_conflict","FEATURE_CONFLICT")])
def test_risk_reasons_abstain(flag,reason):
    exp=expected_return(instrument_id="X",asset_class=AssetClass.CASH,components=components(AssetClass.CASH),horizon=252,as_of=NOW)
    result=calculate_alpha(exp,required(),as_of=NOW,**{flag:True})
    assert result.abstain and reason in result.reason_codes

def test_cost_and_buffer_abstain():
    exp=expected_return(instrument_id="X",asset_class=AssetClass.CASH,components=components(AssetClass.CASH,point=D(".001")),horizon=252,as_of=NOW,transaction_cost=D(".002"))
    result=calculate_alpha(exp,required(D("-.01")),as_of=NOW,uncertainty_buffer=D(".002"))
    assert result.abstain and "INSUFFICIENT_NET_BENEFIT" in result.reason_codes

def test_no_order_direction():
    assert not ({"BUY","SELL","order","side"} & set(AlphaEstimate.__dataclass_fields__))
