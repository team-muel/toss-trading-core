"""Phase 15 risk contracts."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Mapping

class CurrencyBasis(StrEnum): LOCAL="LOCAL"; BASE="BASE"; HEDGED="HEDGED"
class MissingPolicy(StrEnum): FAIL="FAIL"; DROP_ROW="DROP_ROW"

@dataclass(frozen=True)
class ReturnPanel:
    instruments: tuple[str,...]
    dates: tuple[date,...]
    returns: tuple[tuple[Decimal,...],...]
    total_return: bool
    currency_basis: CurrencyBasis
    missing_policy: MissingPolicy
    dropped_rows: int=0
    outlier_policy: str="NONE"
    prelisting_rows_excluded: int=0
    periods_per_year: int=252
    def __post_init__(self):
        if (not self.total_return or not self.instruments or len(set(self.instruments))!=len(self.instruments)
                or len(self.dates)!=len(self.returns) or len(set(self.dates))!=len(self.dates)
                or any(len(row)!=len(self.instruments) for row in self.returns)
                or any(not x.is_finite() for row in self.returns for x in row)):
            raise ValueError("RETURN_PANEL_INVALID")
        if self.periods_per_year<1: raise ValueError("RETURN_PANEL_PERIOD_INVALID")

@dataclass(frozen=True)
class CovarianceEstimate:
    matrix: tuple[tuple[Decimal,...],...]
    method: str
    observation_count: int
    psd: bool
    stressed: bool=False
    annualization_factor: int=1

@dataclass(frozen=True)
class RiskContribution:
    marginal: tuple[Decimal,...]
    component: tuple[Decimal,...]
    portfolio_volatility: Decimal

@dataclass(frozen=True)
class TailRisk:
    confidence: Decimal
    historical_var: Decimal
    historical_cvar: Decimal
    expected_shortfall: Decimal

@dataclass(frozen=True)
class RiskGate:
    action: str
    reason_codes: tuple[str,...]
    @property
    def optimizer_allowed(self): return self.action=="ALLOW"

def optimizer_risk_gate(*, covariance_valid: bool, inverse_fallback: bool,
                        return_panel_valid: bool, tail_risk_valid: bool) -> RiskGate:
    reasons=[]
    if not covariance_valid: reasons.append("COVARIANCE_INVALID")
    if inverse_fallback: reasons.append("COVARIANCE_INVERSE_FALLBACK")
    if not return_panel_valid: reasons.append("RETURN_PANEL_INVALID")
    if not tail_risk_valid: reasons.append("TAIL_RISK_INVALID")
    return RiskGate("ALLOW" if not reasons else "BLOCK_OPTIMIZER",tuple(reasons))
