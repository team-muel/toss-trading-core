from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from asset_management.domain.economics import (
    CurrencyBasis, EconomicValue, ReturnMetricStatus, ReturnSemanticType, ReturnUnit,
)
from asset_management.domain.errors import DataQualityError
from asset_management.domain.scalars import Currency
from asset_management.expectations import assess_model_relative_alpha
from asset_management.governance import ModelDefinition, ModelRegistry, ModelScope, ModelStatus


D = Decimal
NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
FORECAST_ID = "a" * 64


def metric(semantic_type, value, reference="forecast@1"):
    return EconomicValue(semantic_type, D(value), ReturnMetricStatus.AVAILABLE,
        Currency.USD, CurrencyBasis.BASE, 252, ReturnUnit.TOTAL_RETURN,
        "formula@1", reference)


def authorization():
    registry = ModelRegistry()
    model = ModelDefinition("MODEL_ALPHA", "1", "model relative alpha",
        ("combined_signal_forecast", "pricing_baseline"), ("model_relative_alpha",),
        (ModelScope.MODEL_RELATIVE_ALPHA,), ("uncertain forecast",),
        date(2026, 1, 1), date(2026, 12, 31), "owner")
    registry.register(model)
    for status in (ModelStatus.VALIDATED, ModelStatus.APPROVED, ModelStatus.ACTIVE):
        registry.transition(model.key, status, effective_at=NOW, reason="test", evidence_ids=("evidence:test",))
    return registry, registry.authorize(model.key, ModelScope.MODEL_RELATIVE_ALPHA, at=NOW)


def test_assessment_shrinks_forecast_before_preserving_alpha_identity():
    registry, token = authorization()
    assessment = assess_model_relative_alpha(
        net_forecast=metric(ReturnSemanticType.FORECAST_TOTAL_RETURN_NET, ".12"),
        pricing_baseline=metric(ReturnSemanticType.PRICING_BASELINE_RETURN, ".04", "CAPM@2"),
        combined_signal_forecast_id=FORECAST_ID, confidence=D(".5"), prior=D("0"),
        forecast_uncertainty=D(".001"), baseline_uncertainty=D(".001"),
        uncertainty_threshold=D(".02"), formula_version="model-alpha@1", model_key="MODEL_ALPHA@1",
        as_of=NOW, model_registry=registry, authorization=token)
    assert assessment.raw_alpha == D(".08")
    assert assessment.value.value == D(".04")
    assert assessment.shrinkage_amount == D(".04")
    assert assessment.value.reference_version == "CAPM@2"
    assert not assessment.abstain
    schema = json.loads(Path("schemas/model_relative_alpha_assessment.schema.json").read_text())
    import jsonschema
    jsonschema.Draft202012Validator(schema).validate(assessment.payload())


def test_assessment_abstains_and_rejects_noncanonical_or_misaligned_inputs():
    registry, token = authorization()
    arguments = dict(net_forecast=metric(ReturnSemanticType.FORECAST_TOTAL_RETURN_NET, ".041"),
        pricing_baseline=metric(ReturnSemanticType.PRICING_BASELINE_RETURN, ".04", "CAPM@2"),
        combined_signal_forecast_id=FORECAST_ID, confidence=D(1), prior=D(0),
        forecast_uncertainty=D(".01"), baseline_uncertainty=D(".01"),
        uncertainty_threshold=D(".005"), formula_version="model-alpha@1", model_key="MODEL_ALPHA@1",
        as_of=NOW, model_registry=registry, authorization=token)
    assessment = assess_model_relative_alpha(**arguments)
    assert assessment.abstain
    assert set(assessment.reason_codes) == {"MODEL_RELATIVE_ALPHA_INTERVAL_CROSSES_ZERO", "MODEL_RELATIVE_ALPHA_UNCERTAINTY_HIGH"}
    with pytest.raises(DataQualityError, match="INPUT_INVALID"):
        assess_model_relative_alpha(**(arguments | {"combined_signal_forecast_id": "score"}))
    with pytest.raises(DataQualityError, match="CONTEXT_MISMATCH"):
        assess_model_relative_alpha(**(arguments | {"pricing_baseline": EconomicValue(
            ReturnSemanticType.PRICING_BASELINE_RETURN, D(".04"), ReturnMetricStatus.AVAILABLE,
            Currency.KRW, CurrencyBasis.BASE, 252, ReturnUnit.TOTAL_RETURN, "formula@1", "CAPM@2")}))
