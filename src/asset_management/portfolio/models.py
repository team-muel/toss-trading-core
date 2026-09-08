"""Phase 16 portfolio construction contracts."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

@dataclass(frozen=True)
class PortfolioTarget:
    instruments: tuple[str,...]
    weights: tuple[Decimal,...]
    stage: str
    reason_codes: tuple[str,...]=()
    def __post_init__(self):
        if (not self.instruments or len(self.instruments)!=len(self.weights) or
                len(set(self.instruments))!=len(self.instruments) or
                any(not x.is_finite() for x in self.weights)):
            raise ValueError("PORTFOLIO_TARGET_INVALID")

@dataclass(frozen=True)
class ConstructionResult:
    raw_target: PortfolioTarget
    risk_constrained_target: PortfolioTarget
    executable_target: PortfolioTarget
    solver_status: str
    expected_benefit: Decimal
    expected_cost: Decimal
    uncertainty_buffer: Decimal
    no_trade: bool
    def __post_init__(self):
        targets=(self.raw_target,self.risk_constrained_target,self.executable_target)
        if (len({item.instruments for item in targets})!=1 or
                any(abs(sum(item.weights)-1)>Decimal("1e-12") for item in targets) or
                any(not x.is_finite() for x in (self.expected_benefit,self.expected_cost,self.uncertainty_buffer)) or
                self.expected_cost<0 or self.uncertainty_buffer<0):
            raise ValueError("CONSTRUCTION_RESULT_INVALID")

@dataclass(frozen=True)
class ConstraintPolicy:
    cash_instrument: str
    max_single_weight: Decimal
    min_cash_weight: Decimal
    max_volatility: Decimal
    max_cvar: Decimal
    max_stress_loss: Decimal
    max_turnover: Decimal
    max_trade_amount: Decimal
    group_caps: Mapping[str,Decimal]
    factor_caps: Mapping[str,Decimal]
    currency_caps: Mapping[str,Decimal]
    liquidity_caps: Mapping[str,Decimal]
    version: str
    def __post_init__(self):
        values=(self.max_single_weight,self.min_cash_weight,self.max_volatility,self.max_cvar,
                self.max_stress_loss,self.max_turnover,self.max_trade_amount,*self.group_caps.values(),
                *self.factor_caps.values(),*self.currency_caps.values(),*self.liquidity_caps.values())
        if (not self.cash_instrument or not self.version or any(not x.is_finite() or x<0 for x in values)
                or self.max_single_weight>1 or self.min_cash_weight>1):
            raise ValueError("CONSTRAINT_POLICY_INVALID")
