"""Phase 15 risk contracts."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
import re
from typing import Mapping

from asset_management.domain.economics import CurrencyBasis


_HASH = re.compile(r"[0-9a-f]{64}")


class MissingPolicy(StrEnum):
    FAIL = "FAIL"
    DROP_ROW = "DROP_ROW"


@dataclass(frozen=True)
class ReturnPanel:
    instruments: tuple[str, ...]
    dates: tuple[date, ...]
    returns: tuple[tuple[Decimal, ...], ...]
    total_return: bool
    currency_basis: CurrencyBasis
    missing_policy: MissingPolicy
    information_cutoff: datetime
    dataset_manifest_ids: tuple[str, ...]
    available_at: tuple[datetime, ...]
    dropped_rows: int = 0
    outlier_policy: str = "NONE"
    prelisting_rows_excluded: int = 0
    periods_per_year: int = 252

    def __post_init__(self):
        cutoff = self.information_cutoff
        if not isinstance(cutoff, datetime) or cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("RETURN_PANEL_CUTOFF_INVALID")
        cutoff = cutoff.astimezone(timezone.utc)
        if (not self.total_return or not self.instruments or len(set(self.instruments)) != len(self.instruments)
                or len(self.dates) != len(self.returns) or len(set(self.dates)) != len(self.dates)
                or tuple(sorted(self.dates)) != self.dates
                or any(len(row) != len(self.instruments) for row in self.returns)
                or any(not x.is_finite() for row in self.returns for x in row)):
            raise ValueError("RETURN_PANEL_INVALID")
        if (not self.dataset_manifest_ids or
                any(not isinstance(item, str) or not _HASH.fullmatch(item)
                    for item in self.dataset_manifest_ids) or
                len(set(self.dataset_manifest_ids)) != len(self.dataset_manifest_ids)):
            raise ValueError("RETURN_PANEL_MANIFEST_LINEAGE_INVALID")
        if len(self.available_at) != len(self.dates):
            raise ValueError("RETURN_PANEL_AVAILABILITY_INVALID")
        normalized_available = []
        for day, instant in zip(self.dates, self.available_at):
            if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
                raise ValueError("RETURN_PANEL_AVAILABILITY_INVALID")
            instant = instant.astimezone(timezone.utc)
            if instant > cutoff or instant.date() < day:
                raise ValueError("RETURN_PANEL_FUTURE_DATA")
            normalized_available.append(instant)
        if self.periods_per_year < 1:
            raise ValueError("RETURN_PANEL_PERIOD_INVALID")
        object.__setattr__(self, "information_cutoff", cutoff)
        object.__setattr__(self, "dataset_manifest_ids", tuple(sorted(self.dataset_manifest_ids)))
        object.__setattr__(self, "available_at", tuple(normalized_available))


@dataclass(frozen=True)
class CovarianceEstimate:
    matrix: tuple[tuple[Decimal, ...], ...]
    method: str
    observation_count: int
    psd: bool
    stressed: bool = False
    annualization_factor: int = 1


@dataclass(frozen=True)
class RiskContribution:
    marginal: tuple[Decimal, ...]
    component: tuple[Decimal, ...]
    portfolio_volatility: Decimal

    def __post_init__(self):
        if (not isinstance(self.marginal, tuple) or not isinstance(self.component, tuple) or
                not self.component or len(self.component) != len(self.marginal) or
                any(not isinstance(v, Decimal) or not v.is_finite()
                    for v in (*self.marginal, *self.component, self.portfolio_volatility)) or
                self.portfolio_volatility <= 0 or
                abs(sum(self.component) - self.portfolio_volatility) > Decimal('1e-18')):
            raise ValueError('RISK_CONTRIBUTION_SEMANTICS_INVALID')

    @property
    def volatility_contribution(self):
        return self.component

    @property
    def variance_contribution(self):
        return tuple(v * self.portfolio_volatility for v in self.component)

    def payload(self):
        return {"schema_version": "risk-contribution@2",
                "variance_contribution": [str(v) for v in self.variance_contribution],
                "volatility_contribution": [str(v) for v in self.volatility_contribution],
                "marginal_volatility_contribution": [str(v) for v in self.marginal],
                "portfolio_volatility": str(self.portfolio_volatility),
                "portfolio_variance": str(self.portfolio_volatility ** 2),
                "variance_unit": "RETURN_SQUARED", "volatility_unit": "RETURN"}


@dataclass(frozen=True)
class TailRisk:
    confidence: Decimal
    historical_var: Decimal
    historical_cvar: Decimal
    expected_shortfall: Decimal


@dataclass(frozen=True)
class RiskGate:
    action: str
    reason_codes: tuple[str, ...]

    @property
    def optimizer_allowed(self):
        return self.action == "ALLOW"


def optimizer_risk_gate(*, covariance_valid: bool, inverse_fallback: bool,
                        return_panel_valid: bool, tail_risk_valid: bool) -> RiskGate:
    evidence = (covariance_valid, inverse_fallback, return_panel_valid, tail_risk_valid)
    if any(type(value) is not bool for value in evidence):
        return RiskGate("BLOCK_OPTIMIZER", ("RISK_GATE_EVIDENCE_TYPE_INVALID",))
    reasons = []
    if not covariance_valid:
        reasons.append("COVARIANCE_INVALID")
    if inverse_fallback:
        reasons.append("COVARIANCE_INVERSE_FALLBACK")
    if not return_panel_valid:
        reasons.append("RETURN_PANEL_INVALID")
    if not tail_risk_valid:
        reasons.append("TAIL_RISK_INVALID")
    return RiskGate("ALLOW" if not reasons else "BLOCK_OPTIMIZER", tuple(reasons))
