from datetime import datetime, timezone
from decimal import Decimal

import pytest

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.economics import CurrencyBasis
from asset_management.domain.errors import DataQualityError
from asset_management.risk import FactorRiskAssessment, SpecificRiskPolicy, publish_factor_risk_evidence


NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)
LICENSE = "purpose=research;redistribution=forbidden;retention=project"


def raw_tiingo(store: ImmutableDatasetStore) -> str:
    return store.write({"prices": ["SPY"]}, layer="bronze", source="tiingo-eod", dataset="daily-prices",
                       schema_version="tiingo-eod@1", retrieved_at=NOW, available_at=NOW, provider_timestamp=NOW,
                       license_tag=LICENSE, code_revision="tiingo-eod@1", request_hash="d" * 64,
                       quality_status="RAW").manifest_id


def assessment() -> FactorRiskAssessment:
    return FactorRiskAssessment(("SPY",), (Decimal(".04"),), (Decimal(".01"),), (Decimal(".05"),),
                                ((Decimal(".05"),),), CurrencyBasis.BASE, NOW, "factor-risk@1", Decimal(0), Decimal(0))


def policy() -> SpecificRiskPolicy:
    return SpecificRiskPolicy(20, Decimal(".01"), Decimal(".25"), Decimal(".10"), "factor-risk@1")


def test_publishes_gold_evidence_with_tiingo_parent_and_exact_payload(tmp_path):
    store = ImmutableDatasetStore(tmp_path)
    parent = raw_tiingo(store)
    published = publish_factor_risk_evidence(store=store, assessment=assessment(), policy=policy(),
                                              source_manifest_ids=(parent,), published_at=NOW,
                                              code_revision="factor-risk-evidence@1")
    manifest, body = store.read(published.manifest_id)
    assert manifest.layer == "gold" and manifest.parent_manifest_ids == (parent,)
    assert body["assessment"] == assessment().payload()


def test_rejects_non_tiingo_or_missing_parent(tmp_path):
    store = ImmutableDatasetStore(tmp_path)
    with pytest.raises(DataQualityError, match="CONTEXT_INVALID"):
        publish_factor_risk_evidence(store=store, assessment=assessment(), policy=policy(),
                                     source_manifest_ids=(), published_at=NOW, code_revision="factor-risk-evidence@1")
