"""Alpha and fail-closed ABSTAIN decisions."""
from datetime import datetime
from decimal import Decimal
from asset_management.domain.errors import DataQualityError
from asset_management.pricing.models import PricingResult
from asset_management.quality.models import QualityStatus
from .models import AlphaEstimate, ExpectedReturnEstimate

def calculate_alpha(expected: ExpectedReturnEstimate, required: PricingResult, *,
                    as_of: datetime, model_conflict: bool = False, event_risk: bool = False,
                    feature_conflict: bool = False, uncertainty_buffer: Decimal = Decimal(0)) -> AlphaEstimate:
    if (expected.instrument_id != required.instrument_id or expected.horizon != required.horizon or
            expected.validity != required.validity or
            expected.as_of > as_of or required.as_of > as_of):
        raise DataQualityError("ALPHA_INPUT_MISMATCH")
    alpha = expected.net_expected_return-required.required_return
    lower = expected.lower_bound-required.upper_bound
    upper = expected.upper_bound-required.lower_bound
    reasons = []
    if lower <= 0 <= upper: reasons.append("ALPHA_INTERVAL_CROSSES_ZERO")
    if expected.quality_status is not QualityStatus.VALID or required.quality_status is not QualityStatus.VALID:
        reasons.append("LOW_DATA_QUALITY")
    if model_conflict: reasons.append("MODEL_CONFLICT")
    if event_risk: reasons.append("EVENT_RISK")
    if feature_conflict: reasons.append("FEATURE_CONFLICT")
    if expected.validity.effective_weight(produced_at=expected.as_of, evaluated_at=as_of) == 0:
        reasons.append("SIGNAL_EXPIRED")
    costs = expected.expected_transaction_cost+expected.expected_tax_drag+expected.expected_fx_cost
    if expected.gross_expected_return < costs+uncertainty_buffer: reasons.append("INSUFFICIENT_NET_BENEFIT")
    return AlphaEstimate(expected.instrument_id, expected.horizon, expected.net_expected_return,
                         required.required_return, alpha, lower, upper,
                         "ABSTAIN" if reasons else "ELIGIBLE", tuple(reasons), as_of,
                         expected.validity)
