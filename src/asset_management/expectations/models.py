"""Phase 14 expected-return and alpha contracts."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from asset_management.pricing.models import HORIZONS
from asset_management.quality.models import QualityStatus

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
    def __post_init__(self) -> None:
        values = (self.point_estimate, self.uncertainty, self.confidence)
        if (not self.component_name.strip() or any(not x.is_finite() for x in values) or
                self.uncertainty < 0 or not Decimal(0) <= self.confidence <= Decimal(1) or
                not self.input_features or self.horizon not in HORIZONS):
            raise ValueError("EXPECTED_RETURN_COMPONENT_INVALID")

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
    def __post_init__(self) -> None:
        values = (self.gross_expected_return, self.expected_transaction_cost,
                  self.expected_tax_drag, self.expected_fx_cost, self.net_expected_return,
                  self.lower_bound, self.upper_bound, self.confidence)
        if (not self.instrument_id.strip() or not self.components or self.horizon not in HORIZONS or
                any(x.horizon != self.horizon for x in self.components) or self.as_of.tzinfo is None or
                self.as_of.utcoffset() is None or any(not x.is_finite() for x in values) or
                any(x < 0 for x in (self.expected_transaction_cost, self.expected_tax_drag, self.expected_fx_cost)) or
                not Decimal(0) <= self.confidence <= Decimal(1) or
                not self.lower_bound <= self.net_expected_return <= self.upper_bound):
            raise ValueError("EXPECTED_RETURN_ESTIMATE_INVALID")
        if self.gross_expected_return != sum(x.point_estimate for x in self.components):
            raise ValueError("EXPECTED_RETURN_COMPONENT_SUM_CONFLICT")
        if self.net_expected_return != self.gross_expected_return-self.expected_transaction_cost-self.expected_tax_drag-self.expected_fx_cost:
            raise ValueError("EXPECTED_RETURN_NET_CONFLICT")

    def payload(self) -> dict:
        return {"instrument_id": self.instrument_id, "asset_class": self.asset_class.value,
                "horizon": self.horizon,
                "components": [{"component_name": x.component_name, "point_estimate": str(x.point_estimate),
                                "uncertainty": str(x.uncertainty), "confidence": str(x.confidence),
                                "input_features": list(x.input_features), "horizon": x.horizon}
                               for x in self.components],
                "gross_expected_return": str(self.gross_expected_return),
                "expected_transaction_cost": str(self.expected_transaction_cost),
                "expected_tax_drag": str(self.expected_tax_drag), "expected_fx_cost": str(self.expected_fx_cost),
                "net_expected_return": str(self.net_expected_return), "lower_bound": str(self.lower_bound),
                "upper_bound": str(self.upper_bound), "confidence": str(self.confidence),
                "quality_status": self.quality_status.value, "as_of": self.as_of.isoformat()}

@dataclass(frozen=True)
class AlphaEstimate:
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

    def __post_init__(self) -> None:
        values = (self.net_expected_return, self.required_return, self.alpha,
                  self.lower_bound, self.upper_bound)
        if (not self.instrument_id.strip() or self.horizon not in HORIZONS or
                any(not x.is_finite() for x in values) or self.alpha != self.net_expected_return-self.required_return or
                self.lower_bound > self.upper_bound or self.decision not in {"ELIGIBLE", "ABSTAIN"} or
                self.as_of.tzinfo is None or self.as_of.utcoffset() is None or
                (self.decision == "ABSTAIN") != bool(self.reason_codes)):
            raise ValueError("ALPHA_ESTIMATE_INVALID")

    @property
    def abstain(self) -> bool: return self.decision == "ABSTAIN"
