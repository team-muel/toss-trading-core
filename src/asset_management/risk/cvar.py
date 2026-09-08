"""Historical CVaR / expected shortfall with positive-loss convention."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from asset_management.domain.decimal import exact_decimal
from asset_management.domain.economics import CurrencyBasis
from asset_management.domain.errors import DataQualityError
from .models import TailRisk


@dataclass(frozen=True, slots=True)
class TailRiskAssessment:
    tail_risk: TailRisk
    currency_basis: CurrencyBasis
    return_horizon_days: int
    unit: str
    formula_version: str
    confidence: Decimal

    def __post_init__(self):
        confidence = exact_decimal(self.confidence)
        if (not isinstance(self.tail_risk, TailRisk) or not isinstance(self.currency_basis, CurrencyBasis) or
                type(self.return_horizon_days) is not int or self.return_horizon_days < 1 or
                self.unit != "RETURN" or not self.formula_version.strip() or not Decimal(0) < confidence < Decimal(1)):
            raise DataQualityError("TAIL_RISK_CONTEXT_INVALID")
        if confidence != self.tail_risk.confidence:
            raise DataQualityError("TAIL_RISK_CONFIDENCE_MISMATCH")
        object.__setattr__(self, "confidence", confidence)

def historical_tail_risk(returns,confidence=Decimal("0.95")):
    values=sorted(tuple(returns))
    if not values or not Decimal(0)<confidence<Decimal(1): raise DataQualityError("TAIL_RISK_INPUT_INVALID")
    count=max(1,int(((Decimal(1)-confidence)*len(values)).to_integral_value(rounding=ROUND_CEILING)))
    tail=values[:count]; var=max(Decimal(0),-tail[-1]); expected=max(Decimal(0),-sum(tail)/Decimal(len(tail)))
    return TailRisk(confidence,var,expected,expected)


def assess_historical_tail_risk(returns, *, confidence: Decimal, currency_basis: CurrencyBasis,
                                return_horizon_days: int, formula_version: str) -> TailRiskAssessment:
    return TailRiskAssessment(historical_tail_risk(returns, confidence), currency_basis,
                              return_horizon_days, "RETURN", formula_version, confidence)
