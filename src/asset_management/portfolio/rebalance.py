"""No-trade bands and economic-benefit gates."""
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from asset_management.domain.errors import DataQualityError

def dynamic_no_trade_band(*,volatility: Decimal,spread: Decimal,tax_rate: Decimal,
                          illiquidity: Decimal,base_band: Decimal):
    values=(volatility,spread,tax_rate,illiquidity,base_band)
    if any(not x.is_finite() or x<0 for x in values): raise DataQualityError("NO_TRADE_BAND_INPUT_INVALID")
    return base_band+spread+tax_rate*base_band+illiquidity*volatility

def apply_no_trade_bands(target,current,bands,cash_index=-1):
    if not len(target)==len(current)==len(bands) or any(x<0 for x in bands):
        raise DataQualityError("NO_TRADE_BAND_INPUT_INVALID")
    result=[current[i] if abs(target[i]-current[i])<=bands[i] else target[i] for i in range(len(target))]
    cash_index%=len(result)
    result[cash_index]=Decimal(1)-sum(result[i] for i in range(len(result)) if i!=cash_index)
    if result[cash_index]<0: raise DataQualityError("NO_TRADE_BAND_BUDGET_INFEASIBLE")
    return tuple(result)

def economic_trade_gate(expected_benefit,expected_cost,uncertainty_buffer):
    if (not expected_benefit.is_finite() or
            any(not x.is_finite() or x<0 for x in (expected_cost,uncertainty_buffer))):
        raise DataQualityError("TRADE_GATE_INPUT_INVALID")
    return expected_benefit>expected_cost+uncertainty_buffer


class EconomicBenefitUnit(StrEnum):
    RETURN_UTILITY = "RETURN_UTILITY"
    MONEY = "MONEY"


@dataclass(frozen=True)
class EconomicTradeEvidence:
    expected_benefit: Decimal
    expected_cost: Decimal
    uncertainty_buffer: Decimal
    unit: EconomicBenefitUnit
    formula_version: str
    nav: Decimal | None = None

    def __post_init__(self) -> None:
        values = (self.expected_benefit, self.expected_cost, self.uncertainty_buffer)
        if (any(not isinstance(value, Decimal) or not value.is_finite() or value < 0 for value in values) or
                not isinstance(self.unit, EconomicBenefitUnit) or not self.formula_version.strip() or
                (self.unit is EconomicBenefitUnit.MONEY and (not isinstance(self.nav, Decimal) or not self.nav.is_finite() or self.nav <= 0)) or
                (self.unit is EconomicBenefitUnit.RETURN_UTILITY and self.nav is not None)):
            raise DataQualityError("ECONOMIC_TRADE_EVIDENCE_INVALID")

    def permits_trade(self) -> bool:
        return self.expected_benefit > self.expected_cost + self.uncertainty_buffer
