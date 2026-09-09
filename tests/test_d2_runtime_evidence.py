from datetime import datetime, timezone
from decimal import Decimal

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.pricing import RiskFreeCurve, RiskFreePoint, materialize_usd_fred_risk_free_curve
from asset_management.quality.models import QualityStatus
from asset_management.domain.economics import CurrencyBasis
from asset_management.risk import FactorRiskAssessment, SpecificRiskPolicy, publish_factor_risk_evidence
import pytest

from asset_management.validation import (
    CheckEvidence, FactorRiskRuntimeEvidence, REQUIRED_PRICING_EXPECTATION_RISK_CHECKS, RiskFreeRuntimeEvidence,
    RUNTIME_D2_CHECKS, assemble_d2_runtime_evidence, build_d2_gate_input,
)


NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)
LICENSE = "purpose=research;redistribution=forbidden;retention=project"


def manifest(store: ImmutableDatasetStore, *, source: str, dataset: str) -> str:
    return store.write(
        {"evidence": dataset}, layer="bronze", source=source, dataset=dataset,
        schema_version="d2-evidence@1", retrieved_at=NOW, available_at=NOW,
        provider_timestamp=NOW, license_tag=LICENSE, code_revision="d2-evidence@1",
        request_hash="a" * 64, quality_status="RAW").manifest_id


def curve(manifest_id: str) -> RiskFreeCurve:
    return RiskFreeCurve(tuple(
        RiskFreePoint(NOW, NOW, horizon, Decimal(".04"), "fred-alfred", manifest_id,
                      QualityStatus.VALID, "USD", "BUS/252", "EFFECTIVE_ANNUAL",
                      "risk-free-curve@2", Decimal(".001"))
        for horizon in (21, 63, 126, 252)
    ))


def test_runtime_evidence_requires_artifacts_and_does_not_promote_missing_checks():
    result = assemble_d2_runtime_evidence(risk_free=None, factor_risk=None, model_lineage=None)
    assert set(result.failure_reasons) == set(result.checks)
    assert all(not check.passed for check in result.checks.values())


def test_risk_free_runtime_evidence_accepts_the_approved_usd_curve_only(tmp_path):
    store = ImmutableDatasetStore(tmp_path)
    evidence_id = store.write({"observations": [
        {"series_id": name, "as_of": NOW.isoformat(), "available_at": NOW.isoformat(), "value_percent": "4"}
        for name in ("DGS1MO", "DGS3MO", "DGS6MO", "DGS1")
    ]}, layer="bronze", source="fred-alfred", dataset="risk-free-curve", schema_version="fred-risk-free-curve@1",
        retrieved_at=NOW, available_at=NOW, provider_timestamp=NOW, license_tag=LICENSE,
        code_revision="fixture", request_hash="b"*64, quality_status="RAW").manifest_id
    actual_curve = materialize_usd_fred_risk_free_curve(store=store, manifest_id=evidence_id, information_cutoff=NOW)
    result = assemble_d2_runtime_evidence(
        risk_free=RiskFreeRuntimeEvidence(actual_curve, "USD", NOW, store), factor_risk=None, model_lineage=None)
    check = result.checks["RISK_FREE_CURRENCY_HORIZON_COMPOUNDING_VERIFIED"]
    assert check.passed
    assert check.artifact_ids == (f"dataset-manifest:{evidence_id}",)
    denied = assemble_d2_runtime_evidence(
        risk_free=RiskFreeRuntimeEvidence(actual_curve, "KRW", NOW, store), factor_risk=None, model_lineage=None)
    assert not denied.checks["RISK_FREE_CURRENCY_HORIZON_COMPOUNDING_VERIFIED"].passed
    assert denied.failure_reasons["RISK_FREE_CURRENCY_HORIZON_COMPOUNDING_VERIFIED"] == "RISK_FREE_CURRENCY_NOT_APPROVED"


def test_factor_evidence_requires_psd_covariance_and_the_specific_risk_floor(tmp_path):
    store = ImmutableDatasetStore(tmp_path)
    evidence_id = manifest(store, source="tiingo-eod", dataset="daily-prices")
    policy = SpecificRiskPolicy(20, Decimal(".01"), Decimal(".25"), Decimal(".10"), "specific-risk@1")
    valid = FactorRiskAssessment(("A",), (Decimal(".04"),), (Decimal(".01"),),
                                 (Decimal(".05"),), ((Decimal(".05"),),), CurrencyBasis.BASE,
                                 NOW, "specific-risk@1", Decimal(0), Decimal(0))
    published = publish_factor_risk_evidence(
        store=store, assessment=valid, policy=policy, source_manifest_ids=(evidence_id,), published_at=NOW,
        code_revision="factor-risk-evidence@1")
    result = assemble_d2_runtime_evidence(
        risk_free=None,
        factor_risk=FactorRiskRuntimeEvidence(valid, policy, NOW, published.manifest_id, store), model_lineage=None)
    # The old fixture never linked its numeric assessment to real returns.
    # Publishing a self-reported assessment is not empirical estimator evidence.
    assert not result.checks["FACTOR_SPECIFIC_RISK_DECOMPOSITION_AND_FLOOR_VERIFIED"].passed
    assert result.failure_reasons["FACTOR_SPECIFIC_RISK_DECOMPOSITION_AND_FLOOR_VERIFIED"] == "FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"
    below_floor = FactorRiskAssessment(("A",), (Decimal(".04"),), (Decimal(".001"),),
                                       (Decimal(".041"),), ((Decimal(".041"),),), CurrencyBasis.BASE,
                                       NOW, "specific-risk@1", Decimal(0), Decimal(0))
    denied = assemble_d2_runtime_evidence(
        risk_free=None,
        factor_risk=FactorRiskRuntimeEvidence(below_floor, policy, NOW, published.manifest_id, store), model_lineage=None)
    assert denied.failure_reasons["FACTOR_SPECIFIC_RISK_DECOMPOSITION_AND_FLOOR_VERIFIED"] == "FACTOR_RISK_DECOMPOSITION_INVALID"


def test_gate_builder_rejects_caller_supplied_runtime_passes():
    runtime = assemble_d2_runtime_evidence(risk_free=None, factor_risk=None, model_lineage=None)
    static = {name: CheckEvidence(True, (f"evidence:{name.lower()}",))
              for name in set(REQUIRED_PRICING_EXPECTATION_RISK_CHECKS) - RUNTIME_D2_CHECKS}
    gate_input = build_d2_gate_input(evaluated_at=NOW, code_revision="7f893118", static_checks=static)
    assert not gate_input.checks["RISK_FREE_CURRENCY_HORIZON_COMPOUNDING_VERIFIED"].passed
    with pytest.raises(ValueError, match="CHECK_SET_INVALID"):
        build_d2_gate_input(evaluated_at=NOW, code_revision="7f893118",
                            static_checks=static | {next(iter(RUNTIME_D2_CHECKS)): CheckEvidence(True, ("fake",))})

    # A display/result object is no longer an accepted authority argument.
    runtime.checks.update(dict.fromkeys(RUNTIME_D2_CHECKS, CheckEvidence(True, ("fake",))))
    with pytest.raises(TypeError, match="runtime_evidence"):
        build_d2_gate_input(evaluated_at=NOW, code_revision="test", static_checks=static, runtime_evidence=runtime)
