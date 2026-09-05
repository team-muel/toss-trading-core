"""Confidence shrinkage toward an explicit prior."""
from decimal import Decimal
from asset_management.domain.errors import DataQualityError

def shrink_estimate(estimate: Decimal, prior: Decimal, confidence: Decimal) -> Decimal:
    if (any(not x.is_finite() for x in (estimate, prior, confidence)) or
            not Decimal(0) <= confidence <= Decimal(1)):
        raise DataQualityError("EXPECTED_RETURN_CONFIDENCE_INVALID")
    return confidence * estimate + (Decimal(1)-confidence) * prior
