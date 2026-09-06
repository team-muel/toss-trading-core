from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import pytest

from asset_management.domain.errors import InvariantViolation
from asset_management.governance import (BenchmarkDefinition, InvestorMandate, InvestorMandateRegistry,
    MandateObjective, RiskPreference, WealthConvention)

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)

def preference():
    return RiskPreference(Decimal(".2"),Decimal(".25"),Decimal(".1"),Decimal(".2"),Decimal(".1"),Decimal(".1"),"concentration@1","turnover@1","tax@1","liquidity@1",Decimal(".5"),Decimal("3"),Decimal(".2"),Decimal("2"),"mandate approval",("evidence:approved",))

def registry():
    value=InvestorMandateRegistry()
    for identifier in ("SPY","CASH"):
        value.register_benchmark(BenchmarkDefinition(identifier,"1",identifier,True,"USD",21,"etf-us-v1",NOW,NOW+timedelta(days=30)))
    mandate=InvestorMandate("household","1",MandateObjective.BENCHMARK_RELATIVE,"USD","KRW",WealthConvention.NOMINAL,63,21,21,"SPY@1",None,"CASH@1",preference(),NOW,NOW+timedelta(days=30))
    value.register_mandate(mandate)
    return value, mandate

def test_mandate_freezes_benchmark_risk_budget_and_optimizer_ranges(tmp_path):
    value, mandate = registry()
    authorization=value.authorize_optimizer(mandate.key,risk_aversion=Decimal("1"),active_risk_aversion=Decimal(".5"),at=NOW)
    value.require_optimizer_authorization(authorization,risk_aversion=Decimal("1"),active_risk_aversion=Decimal(".5"),at=NOW)
    assert len(value.publish(__import__("asset_management.data.immutable",fromlist=["ImmutableDatasetStore"]).ImmutableDatasetStore(tmp_path,credentials_classified=True))) == 64
    with pytest.raises(InvariantViolation,match="NOT_AUTHORIZED"):
        value.authorize_optimizer(mandate.key,risk_aversion=Decimal("4"),active_risk_aversion=Decimal(".5"),at=NOW)
    with pytest.raises(InvariantViolation,match="CONFLICT"):
        value.register_mandate(replace(mandate, primary_benchmark_key="CASH@1"))

def test_unknown_benchmark_future_effective_and_stale_authorization_fail_closed():
    value, mandate = registry()
    with pytest.raises(InvariantViolation,match="NOT_REGISTERED"):
        value.register_mandate(replace(mandate, mandate_id="other", primary_benchmark_key="unknown@1"))
    authorization=value.authorize_optimizer(mandate.key,risk_aversion=Decimal("1"),active_risk_aversion=Decimal(".5"),at=NOW)
    value.register_benchmark(BenchmarkDefinition("BOND","1","BOND",True,"USD",21,"etf-us-v1",NOW,NOW+timedelta(days=30)))
    with pytest.raises(InvariantViolation,match="AUTHORIZATION_INVALID"):
        value.require_optimizer_authorization(authorization,risk_aversion=Decimal("1"),active_risk_aversion=Decimal(".5"),at=NOW)

def test_registry_schema_matches_published_contract():
    value, _ = registry()
    schema=json.loads((Path(__file__).parents[1] / "schemas/investor_mandate.schema.json").read_text())
    assert set(schema["required"]) == set(value.payload())
