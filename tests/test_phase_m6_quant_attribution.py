from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import pytest

from asset_management.domain.errors import InvariantViolation
from asset_management.governance import BenchmarkDefinition, InvestorMandate, InvestorMandateRegistry, MandateObjective, RiskPreference, WealthConvention
from asset_management.reporting.quant_attribution import QuantAttributionInput, quantify_attribution

NOW=datetime(2026,9,6,tzinfo=timezone.utc)
def registry():
    value=InvestorMandateRegistry()
    for key in ("SPY","CASH"): value.register_benchmark(BenchmarkDefinition(key,"1",key,True,"USD",21,"etf-us-v1",NOW,NOW+timedelta(days=1)))
    risk=RiskPreference(Decimal(".2"),Decimal(".2"),Decimal(".1"),Decimal(".2"),Decimal(".1"),Decimal(".1"),"c@1","t@1","tax@1","l@1",Decimal(0),Decimal(2),Decimal(0),Decimal(2),"approved",("e:1",))
    mandate=InvestorMandate("m","1",MandateObjective.BENCHMARK_RELATIVE,"USD","USD",WealthConvention.NOMINAL,21,21,21,"SPY@1",None,"CASH@1",risk,NOW,NOW+timedelta(days=1)); value.register_mandate(mandate)
    return value, mandate
def inputs(benchmark="SPY@1"):
    return QuantAttributionInput("s@1","d1","m@1",benchmark,NOW,"f@1","o@1","e@1",("forecast:1","target:1"),{"A":Decimal(".1"),"CASH":Decimal(".01")},{"A":Decimal(".8"),"CASH":Decimal(".2")},{"A":Decimal(".6"),"CASH":Decimal(".4")},{"A":Decimal(".5"),"CASH":Decimal(".5")},{"A":Decimal(".5"),"CASH":Decimal(".5")},Decimal(".005"),Decimal(".03"),Decimal(".01"),{"asset_allocation":Decimal(0),"factor":Decimal(0),"security_selection":Decimal(0),"timing":Decimal(0),"execution":Decimal(0),"fx":Decimal(0),"tax":Decimal(0),"fees":Decimal(0),"residual":Decimal(0)})
def test_attribution_preserves_distinct_active_return_stages_and_drag():
    value,_=registry(); result=quantify_attribution(value,inputs())
    assert result.payload["constraint_drag"] == "0.018" and result.payload["execution_drag"] == "0.014"
    assert result.payload["realized_active_return"] == "0.02" and len(result.content_hash)==64
def test_non_mandated_benchmark_fails_closed():
    value,_=registry()
    with pytest.raises(InvariantViolation,match="NOT_MANDATED"): quantify_attribution(value,inputs("CASH@1"))
def test_schema_matches_attribution_contract():
    value,_=registry(); result=quantify_attribution(value,inputs())
    schema=json.loads((Path(__file__).parents[1] / "schemas/quant_attribution.schema.json").read_text())
    assert set(schema["required"]) == set(result.payload)
