"""Phase 14 expected-return and alpha contracts."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from asset_management.pricing.models import HORIZONS
from asset_management.quality.models import QualityStatus
from asset_management.domain.horizon import SignalValidity, require_horizon_alignment
from asset_management.domain.economics import EconomicValue
from .equity import EquityGrowthBasis, EQUITY_COMPONENTS, AGGREGATE_EQUITY_COMPONENTS

class AssetClass(StrEnum):
    EQUITY = "EQUITY"
    EQUITY_ETF = "EQUITY_ETF"
    BOND_ETF = "BOND_ETF"
    CASH = "CASH"
    COMMODITY_ETF = "COMMODITY_ETF"

@dataclass(frozen=True)
class ExpectedReturnComponent:
    component_name: str
    point_estimate: Decimal
    uncertainty: Decimal
    confidence: Decimal
    input_features: tuple[str, ...]
    horizon: int
    validity: SignalValidity
    def __post_init__(self) -> None:
        values = (self.point_estimate, self.uncertainty, self.confidence)
        if (not isinstance(self.validity, SignalValidity) or not self.component_name.strip() or
                any(not x.is_finite() for x in values) or
                self.uncertainty < 0 or not Decimal(0) <= self.confidence <= Decimal(1) or
                not self.input_features or self.horizon not in HORIZONS):
            raise ValueError("EXPECTED_RETURN_COMPONENT_INVALID")
        if self.validity.forecast_horizon != self.horizon:
            raise ValueError("EXPECTED_RETURN_COMPONENT_HORIZON_CONFLICT")

@dataclass(frozen=True)
class ExpectedReturnEstimate:
    instrument_id: str
    asset_class: AssetClass
    horizon: int
    components: tuple[ExpectedReturnComponent, ...]
    gross_expected_return: Decimal
    expected_transaction_cost: Decimal
    expected_tax_drag: Decimal
    expected_fx_cost: Decimal
    net_expected_return: Decimal
    lower_bound: Decimal
    upper_bound: Decimal
    confidence: Decimal
    quality_status: QualityStatus
    as_of: datetime
    validity: SignalValidity
    growth_basis: EquityGrowthBasis | None = None
    def __post_init__(self) -> None:
        if self.asset_class is AssetClass.EQUITY:
            if not isinstance(self.growth_basis, EquityGrowthBasis):
                raise ValueError("EQUITY_GROWTH_BASIS_REQUIRED")
            names = EQUITY_COMPONENTS if self.growth_basis is EquityGrowthBasis.PER_SHARE else AGGREGATE_EQUITY_COMPONENTS
            if len(self.components) != len(names) or {x.component_name for x in self.components} != set(names):
                raise ValueError("EQUITY_COMPONENT_IDENTITY_INVALID")
        elif self.growth_basis is not None:
            raise ValueError("EQUITY_GROWTH_BASIS_UNEXPECTED")
        values = (self.gross_expected_return, self.expected_transaction_cost,
                  self.expected_tax_drag, self.expected_fx_cost, self.net_expected_return,
                  self.lower_bound, self.upper_bound, self.confidence)
        if (not isinstance(self.validity, SignalValidity) or not self.instrument_id.strip() or
                not self.components or self.horizon not in HORIZONS or
                any(x.horizon != self.horizon for x in self.components) or self.as_of.tzinfo is None or
                self.as_of.utcoffset() is None or any(not x.is_finite() for x in values) or
                any(x < 0 for x in (self.expected_transaction_cost, self.expected_tax_drag, self.expected_fx_cost)) or
                not Decimal(0) <= self.confidence <= Decimal(1) or
                not self.lower_bound <= self.net_expected_return <= self.upper_bound or
                self.validity.valid_until <= self.as_of.astimezone(self.validity.valid_until.tzinfo)):
            raise ValueError("EXPECTED_RETURN_ESTIMATE_INVALID")
        aligned = require_horizon_alignment(x.validity for x in self.components)
        if self.validity != aligned or self.validity.forecast_horizon != self.horizon:
            raise ValueError("EXPECTED_RETURN_VALIDITY_CONFLICT")
        if self.gross_expected_return != sum(x.point_estimate for x in self.components):
            raise ValueError("EXPECTED_RETURN_COMPONENT_SUM_CONFLICT")
        if self.net_expected_return != self.gross_expected_return-self.expected_transaction_cost-self.expected_tax_drag-self.expected_fx_cost:
            raise ValueError("EXPECTED_RETURN_NET_CONFLICT")

    def payload(self) -> dict:
        return {"schema_version": "expected-return@2", "instrument_id": self.instrument_id, "asset_class": self.asset_class.value,
                "growth_basis": None if self.growth_basis is None else self.growth_basis.value,
                "horizon": self.horizon,
                "components": [{"component_name": x.component_name, "point_estimate": str(x.point_estimate),
                                "uncertainty": str(x.uncertainty), "confidence": str(x.confidence),
                                "input_features": list(x.input_features), "horizon": x.horizon,
                                "validity": x.validity.payload()}
                               for x in self.components],
                "gross_expected_return": str(self.gross_expected_return),
                "expected_transaction_cost": str(self.expected_transaction_cost),
                "expected_tax_drag": str(self.expected_tax_drag), "expected_fx_cost": str(self.expected_fx_cost),
                "net_expected_return": str(self.net_expected_return), "lower_bound": str(self.lower_bound),
                "upper_bound": str(self.upper_bound), "confidence": str(self.confidence),
                "quality_status": self.quality_status.value, "as_of": self.as_of.isoformat(),
                "validity": self.validity.payload()}

@dataclass(frozen=True)
class AlphaEstimate:
    """Legacy ``alpha-estimate@1`` shape retained for deterministic replay.

    It is not a generic alpha, benchmark-active return, or current decision
    input; use ``ModelRelativeAlphaAssessment`` for new canonical work.
    """
    instrument_id: str
    horizon: int
    net_expected_return: Decimal
    required_return: Decimal
    alpha: Decimal
    lower_bound: Decimal
    upper_bound: Decimal
    decision: str
    reason_codes: tuple[str, ...]
    as_of: datetime
    validity: SignalValidity

    def __post_init__(self) -> None:
        values = (self.net_expected_return, self.required_return, self.alpha,
                  self.lower_bound, self.upper_bound)
        if (not isinstance(self.validity, SignalValidity) or not self.instrument_id.strip() or
                self.horizon not in HORIZONS or
                self.validity.forecast_horizon != self.horizon or
                any(not x.is_finite() for x in values) or self.alpha != self.net_expected_return-self.required_return or
                self.lower_bound > self.upper_bound or self.decision not in {"ELIGIBLE", "ABSTAIN"} or
                self.as_of.tzinfo is None or self.as_of.utcoffset() is None or
                (self.decision == "ABSTAIN") != bool(self.reason_codes)):
            raise ValueError("ALPHA_ESTIMATE_INVALID")

    @property
    def abstain(self) -> bool: return self.decision == "ABSTAIN"


@dataclass(frozen=True)
class ModelRelativeAlphaAssessment:
    """Canonical ex-ante model residual with shrinkage and ABSTAIN evidence."""

    value: EconomicValue
    raw_alpha: Decimal
    lower_bound: Decimal
    upper_bound: Decimal
    confidence: Decimal
    prior: Decimal
    shrinkage_amount: Decimal
    combined_signal_forecast_id: str
    decision: str
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        from asset_management.domain.economics import ReturnSemanticType, ReturnMetricStatus
        values = (self.raw_alpha, self.lower_bound, self.upper_bound, self.confidence,
                  self.prior, self.shrinkage_amount)
        if (self.value.semantic_type is not ReturnSemanticType.MODEL_RELATIVE_ALPHA or
                self.value.status is not ReturnMetricStatus.AVAILABLE or
                any(not item.is_finite() for item in values) or
                not Decimal(0) <= self.confidence <= Decimal(1) or
                self.lower_bound > self.upper_bound or len(self.combined_signal_forecast_id) != 64 or
                any(char not in "0123456789abcdef" for char in self.combined_signal_forecast_id) or
                self.decision not in {"ELIGIBLE", "ABSTAIN"} or
                (self.decision == "ABSTAIN") != bool(self.reason_codes)):
            raise ValueError("MODEL_RELATIVE_ALPHA_ASSESSMENT_INVALID")

    @property
    def abstain(self) -> bool:
        return self.decision == "ABSTAIN"

    def payload(self) -> dict[str, object]:
        return {"raw_alpha": str(self.raw_alpha), "lower_bound": str(self.lower_bound),
                "upper_bound": str(self.upper_bound), "confidence": str(self.confidence),
                "prior": str(self.prior), "shrinkage_amount": str(self.shrinkage_amount),
                "combined_signal_forecast_id": self.combined_signal_forecast_id,
                "decision": self.decision, "reason_codes": list(self.reason_codes)}
