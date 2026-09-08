from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import pytest

from asset_management.domain.errors import DataQualityError
from asset_management.domain.scalars import Currency
from asset_management.portfolio import CapitalFlow, CapitalFlowKind, RecognitionStatus, assess_capital_reserve

D=Decimal; NOW=datetime(2026,9,8,tzinfo=timezone.utc)
def flow(kind, amount, status, id):
    return CapitalFlow(id,kind,D(amount),Currency.USD,NOW,status,f"evidence:{id}",1,NOW)

def test_risk_capital_only_reserves_unrecognized_outflows_and_liquidity():
    result=assess_capital_reserve(nav=D("1000"),currency=Currency.USD,as_of=NOW,flows=(
        flow(CapitalFlowKind.PLANNED_WITHDRAWAL,"100",RecognitionStatus.NOT_RECOGNIZED,"withdraw"),
        flow(CapitalFlowKind.CONTRACTUAL_OUTFLOW,"50",RecognitionStatus.RECOGNIZED_IN_NAV,"payable"),
        flow(CapitalFlowKind.MINIMUM_LIQUIDITY,"200",RecognitionStatus.NOT_RECOGNIZED,"reserve"),
        flow(CapitalFlowKind.PLANNED_DEPOSIT,"80",RecognitionStatus.NOT_RECOGNIZED,"deposit")))
    assert result.risk_capital==D("700") and result.planned_deposits_not_yet_recognized==D("80")
    import jsonschema
    jsonschema.Draft202012Validator(json.loads(Path("schemas/capital_reserve_assessment.schema.json").read_text())).validate(result.payload())

def test_unknown_or_overcommitted_flow_fails_closed():
    with pytest.raises(DataQualityError,match="RECOGNITION_UNKNOWN"):
        assess_capital_reserve(nav=D("100"),currency=Currency.USD,as_of=NOW,flows=(flow(CapitalFlowKind.PLANNED_WITHDRAWAL,"1",RecognitionStatus.UNKNOWN,"unknown"),))
    with pytest.raises(DataQualityError,match="EXCEEDS_NAV"):
        assess_capital_reserve(nav=D("100"),currency=Currency.USD,as_of=NOW,flows=(flow(CapitalFlowKind.MINIMUM_LIQUIDITY,"101",RecognitionStatus.NOT_RECOGNIZED,"reserve"),))
