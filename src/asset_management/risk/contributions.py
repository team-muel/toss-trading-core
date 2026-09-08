"""Euler volatility risk contributions."""
from decimal import Decimal
from asset_management.domain.errors import DataQualityError
from .models import CovarianceEstimate, RiskContribution

def portfolio_risk(weights, covariance: CovarianceEstimate) -> RiskContribution:
    weights=tuple(weights); matrix=covariance.matrix; n=len(weights)
    if len(matrix)!=n or any(not x.is_finite() for x in weights): raise DataQualityError("RISK_WEIGHT_INVALID")
    sigma_w=tuple(sum(matrix[i][j]*weights[j] for j in range(n)) for i in range(n))
    variance=sum(weights[i]*sigma_w[i] for i in range(n))
    if variance<=0: raise DataQualityError("PORTFOLIO_VARIANCE_NON_POSITIVE")
    volatility=variance.sqrt(); marginal=tuple(x/volatility for x in sigma_w)
    component=tuple(weights[i]*marginal[i] for i in range(n))
    if abs(sum(component)-volatility)>Decimal("1e-18"): raise DataQualityError("RISK_CONTRIBUTION_NOT_RECONCILED")
    return RiskContribution(marginal,component,volatility)
