from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from asset_management.domain.economics import CurrencyBasis
from asset_management.domain.errors import DataQualityError
from asset_management.risk import FactorExposure, SpecificRiskPolicy, assess_factor_specific_risk


D = Decimal
NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
POLICY = SpecificRiskPolicy(20, D(".01"), D(".25"), D(".10"), "specific-risk@1")


def test_factor_specific_risk_separates_variances_and_applies_floor():
    result = assess_factor_specific_risk(
        exposures=(FactorExposure("A", (D(1),), NOW, NOW, "exposure@1"),
                   FactorExposure("B", (D(".5"),), NOW, NOW, "exposure@1")),
        factor_matrix=((D(".04"),),), residual_variance=(D(0), D(".08")),
        residual_history=(30, 30), residual_serial_correlation=(D(".1"), D(".2")),
        residual_heteroskedasticity=(D(".3"), D(".1")), policy=POLICY,
        currency_basis=CurrencyBasis.BASE, as_of=NOW, information_cutoff=NOW,
        full_covariance=((D(".06"), D(0)), (D(0), D(".10"))))
    assert result.specific_variance[0] == D(".01")
    assert result.total_variance == tuple(a + b for a, b in zip(result.systematic_variance, result.specific_variance))
    assert result.model_disagreement > 0 and result.regime_sensitivity == D(".3")
    import jsonschema
    schema = json.loads(Path("schemas/factor_specific_risk_assessment.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(result.payload())


def test_factor_specific_risk_rejects_stale_or_insufficient_history():
    arguments = dict(exposures=(FactorExposure("A", (D(1),), NOW, NOW + timedelta(seconds=1), "exposure@1"),),
        factor_matrix=((D(".04"),),), residual_variance=(D(".02"),), residual_history=(30,),
        residual_serial_correlation=(D(0),), residual_heteroskedasticity=(D(0),), policy=POLICY,
        currency_basis=CurrencyBasis.BASE, as_of=NOW, information_cutoff=NOW)
    with pytest.raises(DataQualityError, match="FACTOR_RISK_INPUT_INVALID"):
        assess_factor_specific_risk(**arguments)
    with pytest.raises(DataQualityError, match="FACTOR_RISK_INPUT_INVALID"):
        assess_factor_specific_risk(**(arguments | {"exposures": (FactorExposure("A", (D(1),), NOW, NOW, "exposure@1"),), "residual_history": (3,)}))
