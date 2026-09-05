"""Confidence shrinkage toward an explicit prior."""
from decimal import Decimal
from asset_management.domain.errors import DataQualityError
from asset_management.domain.horizon import SignalValidity

def shrink_estimate(estimate: Decimal, prior: Decimal, confidence: Decimal) -> Decimal:
    if (any(not x.is_finite() for x in (estimate, prior, confidence)) or
            not Decimal(0) <= confidence <= Decimal(1)):
        raise DataQualityError("EXPECTED_RETURN_CONFIDENCE_INVALID")
    return confidence * estimate + (Decimal(1)-confidence) * prior

def shrink_component(*, raw_estimate: Decimal, prior: Decimal, confidence: Decimal,
                     uncertainty: Decimal, component_name: str, input_features: tuple[str, ...],
                     horizon: int, validity: SignalValidity):
    """Build a component whose stored point estimate is the required shrunk value."""
    from .models import ExpectedReturnComponent
    return ExpectedReturnComponent(component_name, shrink_estimate(raw_estimate,prior,confidence),
                                   uncertainty,confidence,input_features,horizon,validity)
