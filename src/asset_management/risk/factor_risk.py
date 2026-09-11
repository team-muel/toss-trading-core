"""Named portfolio exposures and PIT factor/specific-risk decomposition."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from asset_management.domain.errors import DataQualityError
from asset_management.domain.economics import CurrencyBasis
from .covariance import factor_covariance

EXPOSURES=("market_beta","sector","industry","value","momentum","quality","growth","duration","credit","currency","liquidity")
def aggregate_exposure(weights, instrument_exposures):
    weights=tuple(weights); rows=tuple(instrument_exposures)
    if (len(weights)!=len(rows) or any(not x.is_finite() for x in weights) or
            any(set(row)!=set(EXPOSURES) or any(not x.is_finite() for x in row.values()) for row in rows)):
        raise DataQualityError("EXPOSURE_INPUT_INVALID")
    return {name:sum(weights[i]*rows[i][name] for i in range(len(weights))) for name in EXPOSURES}


@dataclass(frozen=True)
class FactorExposure:
    instrument_id: str
    loadings: tuple[Decimal, ...]
    as_of: datetime
    available_at: datetime
    source_version: str

    def __post_init__(self) -> None:
        if (not self.instrument_id.strip() or not self.loadings or not self.source_version.strip() or
                any(not value.is_finite() for value in self.loadings) or
                self.as_of.tzinfo is None or self.as_of.utcoffset() is None or
                self.available_at.tzinfo is None or self.available_at.utcoffset() is None):
            raise DataQualityError("FACTOR_EXPOSURE_INVALID")
        object.__setattr__(self, "as_of", self.as_of.astimezone(timezone.utc))
        object.__setattr__(self, "available_at", self.available_at.astimezone(timezone.utc))


@dataclass(frozen=True)
class SpecificRiskPolicy:
    minimum_history: int
    residual_variance_floor: Decimal
    shrinkage_weight: Decimal
    winsorization_limit: Decimal
    estimation_version: str

    def __post_init__(self) -> None:
        if (self.minimum_history < 2 or not self.residual_variance_floor.is_finite() or
                self.residual_variance_floor <= 0 or not Decimal(0) <= self.shrinkage_weight <= Decimal(1) or
                not self.winsorization_limit.is_finite() or self.winsorization_limit <= 0 or
                not self.estimation_version.strip()):
            raise DataQualityError("SPECIFIC_RISK_POLICY_INVALID")


@dataclass(frozen=True)
class FactorRiskAssessment:
    instruments: tuple[str, ...]
    systematic_variance: tuple[Decimal, ...]
    specific_variance: tuple[Decimal, ...]
    total_variance: tuple[Decimal, ...]
    covariance: tuple[tuple[Decimal, ...], ...]
    currency_basis: CurrencyBasis
    as_of: datetime
    estimation_version: str
    model_disagreement: Decimal
    regime_sensitivity: Decimal

    def __post_init__(self) -> None:
        size = len(self.instruments)
        if (not size or len(set(self.instruments)) != size or
                any(not item.strip() for item in self.instruments) or
                any(len(row) != size for row in self.covariance) or len(self.covariance) != size or
                any(len(values) != size for values in (self.systematic_variance, self.specific_variance, self.total_variance)) or
                any(not value.is_finite() or value < 0 for values in (self.systematic_variance, self.specific_variance, self.total_variance)
                    for value in values) or not isinstance(self.currency_basis, CurrencyBasis) or
                self.as_of.tzinfo is None or self.as_of.utcoffset() is None or not self.estimation_version.strip() or
                any(not value.is_finite() or value < 0 for value in (self.model_disagreement, self.regime_sensitivity)) or
                any(self.total_variance[index] != self.systematic_variance[index] + self.specific_variance[index]
                    for index in range(size))):
            raise DataQualityError("FACTOR_RISK_ASSESSMENT_INVALID")
        object.__setattr__(self, "as_of", self.as_of.astimezone(timezone.utc))

    def payload(self) -> dict[str, object]:
        return {"instruments": list(self.instruments),
                "systematic_variance": [str(value) for value in self.systematic_variance],
                "specific_variance": [str(value) for value in self.specific_variance],
                "total_variance": [str(value) for value in self.total_variance],
                "covariance": [[str(value) for value in row] for row in self.covariance],
                "currency_basis": self.currency_basis.value, "as_of": self.as_of.isoformat(),
                "estimation_version": self.estimation_version,
                "model_disagreement": str(self.model_disagreement),
                "regime_sensitivity": str(self.regime_sensitivity)}


def assess_factor_specific_risk(*, exposures: tuple[FactorExposure, ...],
                                factor_matrix: tuple[tuple[Decimal, ...], ...],
                                residual_variance: tuple[Decimal, ...],
                                residual_history: tuple[int, ...],
                                residual_serial_correlation: tuple[Decimal, ...],
                                residual_heteroskedasticity: tuple[Decimal, ...],
                                policy: SpecificRiskPolicy, currency_basis: CurrencyBasis,
                                as_of: datetime, information_cutoff: datetime,
                                full_covariance: tuple[tuple[Decimal, ...], ...] | None = None) -> FactorRiskAssessment:
    if (as_of.tzinfo is None or as_of.utcoffset() is None or information_cutoff.tzinfo is None or
            information_cutoff.utcoffset() is None or not isinstance(currency_basis, CurrencyBasis) or
            as_of > information_cutoff):
        raise DataQualityError("FACTOR_RISK_CONTEXT_INVALID")
    size = len(exposures)
    if (not size or len(residual_variance) != size or len(residual_history) != size or
            len(residual_serial_correlation) != size or len(residual_heteroskedasticity) != size or
            len({item.instrument_id for item in exposures}) != size or
            any(item.as_of > as_of or item.available_at > information_cutoff for item in exposures) or
            any(history < policy.minimum_history for history in residual_history) or
            any(not value.is_finite() or value < 0 for values in (residual_variance, residual_serial_correlation,
                residual_heteroskedasticity) for value in values)):
        raise DataQualityError("FACTOR_RISK_INPUT_INVALID")
    loadings = tuple(item.loadings for item in exposures)
    if any(len(item) != len(factor_matrix) for item in loadings):
        raise DataQualityError("FACTOR_COVARIANCE_DIMENSION_INVALID")
    specific = tuple(max(policy.residual_variance_floor,
        policy.shrinkage_weight * policy.residual_variance_floor +
        (Decimal(1) - policy.shrinkage_weight) * min(value, policy.winsorization_limit))
        for value in residual_variance)
    covariance = factor_covariance(loadings, factor_matrix, specific).matrix
    systematic = tuple(sum(loadings[row][left] * factor_matrix[left][right] * loadings[row][right]
        for left in range(len(factor_matrix)) for right in range(len(factor_matrix))) for row in range(size))
    if any(value < 0 for value in systematic):
        raise DataQualityError("FACTOR_SYSTEMATIC_VARIANCE_INVALID")
    total = tuple(systematic[index] + specific[index] for index in range(size))
    disagreement = Decimal(0)
    if full_covariance is not None:
        if len(full_covariance) != size or any(len(row) != size for row in full_covariance):
            raise DataQualityError("FACTOR_RISK_FULL_COVARIANCE_INVALID")
        disagreement = max(abs(full_covariance[index][index] - total[index]) for index in range(size))
    regime = max((*residual_serial_correlation, *residual_heteroskedasticity), default=Decimal(0))
    return FactorRiskAssessment(tuple(item.instrument_id for item in exposures), systematic, specific, total,
        covariance, currency_basis, as_of, policy.estimation_version, disagreement, regime)
