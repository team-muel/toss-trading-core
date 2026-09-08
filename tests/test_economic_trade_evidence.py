from decimal import Decimal
import pytest
from asset_management.domain.errors import DataQualityError
from asset_management.portfolio import EconomicBenefitUnit, EconomicTradeEvidence

D=Decimal
def test_economic_gate_requires_one_explicit_unit_basis():
    utility=EconomicTradeEvidence(D(".01"),D(".002"),D(".003"),EconomicBenefitUnit.RETURN_UTILITY,"utility@1")
    money=EconomicTradeEvidence(D(10),D(2),D(3),EconomicBenefitUnit.MONEY,"money@1",D(1000))
    assert utility.permits_trade() and money.permits_trade()

def test_monetary_and_return_bases_cannot_be_ambiguous():
    with pytest.raises(DataQualityError,match="EVIDENCE_INVALID"):
        EconomicTradeEvidence(D(".01"),D(".002"),D(".003"),EconomicBenefitUnit.MONEY,"money@1")
    with pytest.raises(DataQualityError,match="EVIDENCE_INVALID"):
        EconomicTradeEvidence(D(".01"),D(".002"),D(".003"),EconomicBenefitUnit.RETURN_UTILITY,"utility@1",D(1000))
