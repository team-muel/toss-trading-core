"""Canonical Black-Litterman equilibrium and posterior returns."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence
from asset_management.domain.errors import DataQualityError

Vector = tuple[Decimal, ...]
Matrix = tuple[Vector, ...]

@dataclass(frozen=True)
class BlackLittermanView:
    exposures: Vector
    expected_return: Decimal
    confidence: Decimal
    def __post_init__(self) -> None:
        if (not self.exposures or any(not x.is_finite() for x in self.exposures) or
                not self.expected_return.is_finite() or not Decimal(0) < self.confidence <= Decimal(1)):
            raise ValueError("BLACK_LITTERMAN_VIEW_INVALID")

def _covariance(value: Sequence[Sequence[Decimal]]) -> Matrix:
    matrix=tuple(tuple(row) for row in value); n=len(matrix)
    if (not n or any(len(row)!=n for row in matrix) or any(not x.is_finite() for row in matrix for x in row)
            or any(matrix[i][j]!=matrix[j][i] for i in range(n) for j in range(n))):
        raise DataQualityError("COVARIANCE_INVALID")
    return matrix

def _inverse(matrix: Matrix) -> Matrix:
    n=len(matrix); work=[list(row)+[Decimal(i==j) for j in range(n)] for i,row in enumerate(matrix)]
    for col in range(n):
        pivot=max(range(col,n),key=lambda row: abs(work[row][col]))
        if work[pivot][col]==0: raise DataQualityError("BLACK_LITTERMAN_SINGULAR")
        work[col],work[pivot]=work[pivot],work[col]
        divisor=work[col][col]; work[col]=[x/divisor for x in work[col]]
        for row in range(n):
            if row!=col:
                factor=work[row][col]
                work[row]=[work[row][j]-factor*work[col][j] for j in range(2*n)]
    return tuple(tuple(row[n:]) for row in work)

def equilibrium_returns(covariance: Sequence[Sequence[Decimal]], market_weights: Sequence[Decimal],
                        risk_aversion: Decimal) -> Vector:
    sigma=_covariance(covariance); weights=tuple(market_weights)
    if (len(weights)!=len(sigma) or not risk_aversion.is_finite() or risk_aversion<=0 or
            any(not x.is_finite() or x<0 for x in weights) or sum(weights)<=0):
        raise DataQualityError("MARKET_EQUILIBRIUM_INPUT_INVALID")
    total=sum(weights); weights=tuple(x/total for x in weights)
    return tuple(risk_aversion*sum(row[j]*weights[j] for j in range(len(sigma))) for row in sigma)

def posterior_returns(covariance: Sequence[Sequence[Decimal]], market_weights: Sequence[Decimal],
                      risk_aversion: Decimal, views: Sequence[BlackLittermanView], *,
                      capm_stable: bool, supply_stable: bool, market_caps_stable: bool,
                      tau: Decimal=Decimal("0.05")) -> Vector:
    """Compute Pi + tau*Sigma*P'*(P*tau*Sigma*P'+Omega)^-1*(Q-P*Pi)."""
    if not (capm_stable and supply_stable and market_caps_stable):
        raise DataQualityError("BLACK_LITTERMAN_PREREQUISITE_UNSTABLE")
    if not tau.is_finite() or tau<=0: raise DataQualityError("BLACK_LITTERMAN_TAU_INVALID")
    sigma=_covariance(covariance); prior=equilibrium_returns(sigma,market_weights,risk_aversion)
    if not views: return prior
    n=len(sigma); m=len(views)
    if any(len(view.exposures)!=n for view in views): raise DataQualityError("BLACK_LITTERMAN_VIEW_DIMENSION")
    p=tuple(view.exposures for view in views)
    p_sigma_pt=tuple(tuple(tau*sum(p[a][i]*sigma[i][j]*p[b][j] for i in range(n) for j in range(n))
                             for b in range(m)) for a in range(m))
    omega=[]
    for a,view in enumerate(views):
        variance=p_sigma_pt[a][a]
        if variance<=0: raise DataQualityError("BLACK_LITTERMAN_VIEW_VARIANCE_INVALID")
        omega.append(variance*(Decimal(1)-view.confidence)/view.confidence)
    system=tuple(tuple(p_sigma_pt[a][b]+(omega[a] if a==b else 0) for b in range(m)) for a in range(m))
    inverse=_inverse(system)
    surprise=tuple(views[a].expected_return-sum(p[a][i]*prior[i] for i in range(n)) for a in range(m))
    solved=tuple(sum(inverse[a][b]*surprise[b] for b in range(m)) for a in range(m))
    return tuple(prior[i]+tau*sum(sigma[i][j]*p[a][j]*solved[a] for j in range(n) for a in range(m))
                 for i in range(n))
