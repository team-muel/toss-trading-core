"""Alpha and fail-closed ABSTAIN decisions."""
from datetime import datetime
from decimal import Decimal
from asset_management.domain.errors import DataQualityError
from asset_management.pricing.models import PricingResult
from asset_management.quality.models import QualityStatus
from asset_management.domain.economics import EconomicValue, ReturnSemanticType, model_relative_alpha
from asset_management.governance import ModelAuthorization, ModelRegistry, ModelScope
from .confidence import shrink_estimate
from .models import AlphaEstimate, ExpectedReturnEstimate, ModelRelativeAlphaAssessment

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


def assess_model_relative_alpha(*, net_forecast: EconomicValue,
                                pricing_baseline: EconomicValue,
                                combined_signal_forecast_id: str,
                                confidence: Decimal, prior: Decimal,
                                forecast_uncertainty: Decimal,
                                baseline_uncertainty: Decimal,
                                uncertainty_threshold: Decimal,
                                formula_version: str, model_key: str,
                                as_of: datetime, model_registry: ModelRegistry,
                                authorization: ModelAuthorization) -> ModelRelativeAlphaAssessment:
    """Create an approved, typed residual from a combined signal forecast only.

    Confidence shrinks the net forecast toward ``baseline + prior`` before the
    residual is computed, preserving the model-relative-alpha identity.
    """
    if (len(combined_signal_forecast_id) != 64 or
            any(char not in "0123456789abcdef" for char in combined_signal_forecast_id) or
            any(not value.is_finite() for value in (confidence, prior, forecast_uncertainty,
                                                     baseline_uncertainty, uncertainty_threshold)) or
            not Decimal(0) <= confidence <= Decimal(1) or forecast_uncertainty < 0 or
            baseline_uncertainty < 0 or uncertainty_threshold < 0 or
            net_forecast.semantic_type is not ReturnSemanticType.FORECAST_TOTAL_RETURN_NET):
        raise DataQualityError("MODEL_RELATIVE_ALPHA_INPUT_INVALID")
    model_registry.require_authorization(authorization, model_key=model_key,
        scope=ModelScope.MODEL_RELATIVE_ALPHA, at=as_of)
    raw = model_relative_alpha(net_forecast, pricing_baseline, formula_version=formula_version)
    shrunk_forecast = EconomicValue(ReturnSemanticType.FORECAST_TOTAL_RETURN_NET,
        shrink_estimate(net_forecast.value, pricing_baseline.value + prior, confidence),
        net_forecast.status, net_forecast.currency, net_forecast.currency_basis,
        net_forecast.forecast_horizon, net_forecast.unit, formula_version,
        combined_signal_forecast_id)
    value = model_relative_alpha(shrunk_forecast, pricing_baseline, formula_version=formula_version)
    uncertainty = forecast_uncertainty + baseline_uncertainty
    lower, upper = value.value - uncertainty, value.value + uncertainty
    reasons = []
    if lower <= 0 <= upper:
        reasons.append("MODEL_RELATIVE_ALPHA_INTERVAL_CROSSES_ZERO")
    if uncertainty > uncertainty_threshold:
        reasons.append("MODEL_RELATIVE_ALPHA_UNCERTAINTY_HIGH")
    return ModelRelativeAlphaAssessment(value, raw.value, lower, upper, confidence, prior,
        raw.value - value.value, combined_signal_forecast_id,
        "ABSTAIN" if reasons else "ELIGIBLE", tuple(reasons))
