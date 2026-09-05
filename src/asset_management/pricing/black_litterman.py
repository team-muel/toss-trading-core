"""Black-Litterman equilibrium and confidence-weighted views."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence
from asset_management.domain.errors import DataQualityError

Vector = tuple[Decimal, ...]

@dataclass(frozen=True)
class BlackLittermanView:
    exposures: Vector
    expected_return: Decimal
    confidence: Decimal
    def __post_init__(self) -> None:
        if (not self.exposures or any(not x.is_finite() for x in self.exposures) or
                not self.expected_return.is_finite() or not Decimal(0) < self.confidence <= Decimal(1)):
            raise ValueError("BLACK_LITTERMAN_VIEW_INVALID")

def _covariance(value: Sequence[Sequence[Decimal]]) -> tuple[Vector, ...]:
    matrix = tuple(tuple(row) for row in value); n = len(matrix)
    if (not n or any(len(row) != n for row in matrix) or
            any(not x.is_finite() for row in matrix for x in row) or
            any(matrix[i][j] != matrix[j][i] for i in range(n) for j in range(n))):
        raise DataQualityError("COVARIANCE_INVALID")
    return matrix

def equilibrium_returns(covariance: Sequence[Sequence[Decimal]], market_weights: Sequence[Decimal],
                        risk_aversion: Decimal) -> Vector:
    matrix = _covariance(covariance); weights = tuple(market_weights)
    if (len(weights) != len(matrix) or not risk_aversion.is_finite() or risk_aversion <= 0 or
            any(not x.is_finite() or x < 0 for x in weights) or sum(weights) <= 0):
        raise DataQualityError("MARKET_EQUILIBRIUM_INPUT_INVALID")
    total = sum(weights); weights = tuple(x / total for x in weights)
    return tuple(risk_aversion * sum(row[j] * weights[j] for j in range(len(matrix))) for row in matrix)

def posterior_returns(covariance: Sequence[Sequence[Decimal]], market_weights: Sequence[Decimal],
                      risk_aversion: Decimal, views: Sequence[BlackLittermanView], *,
                      capm_stable: bool, supply_stable: bool, market_caps_stable: bool) -> Vector:
    if not (capm_stable and supply_stable and market_caps_stable):
        raise DataQualityError("BLACK_LITTERMAN_PREREQUISITE_UNSTABLE")
    matrix = _covariance(covariance)
    posterior = list(equilibrium_returns(matrix, market_weights, risk_aversion))
    for view in views:
        if len(view.exposures) != len(posterior):
            raise DataQualityError("BLACK_LITTERMAN_VIEW_DIMENSION")
        variance = sum(view.exposures[i] * matrix[i][j] * view.exposures[j]
                       for i in range(len(posterior)) for j in range(len(posterior)))
        if variance <= 0: raise DataQualityError("BLACK_LITTERMAN_VIEW_VARIANCE_INVALID")
        current = sum(view.exposures[i] * posterior[i] for i in range(len(posterior)))
        change = view.confidence * (view.expected_return - current)
        exposure = tuple(sum(matrix[i][j] * view.exposures[j] for j in range(len(posterior)))
                         for i in range(len(posterior)))
        posterior = [posterior[i] + change * exposure[i] / variance for i in range(len(posterior))]
    return tuple(posterior)
